"""收益口径统一服务（Q11，2026-10-02 第二批 2A）— 净值复权 / 基准含息 / 报告口径头

一批数字只有共用同一份口径定义才有可比性。第一批 #56 把**因子链**的场外净值改成分红
复权后，另外三条链路并没有跟上，于是偏差方向反而变严重：

- 复盘与建议回填仍取裸单位净值（`review_service._fetch_nav_series` 场外分支）→
  有分红的基金在除息日被记成一天真实下跌；
- 基准是**价格指数**（沪深300 点位，不含股息），而基金侧已含分红 →
  只跟着指数走的组合被白记约 2.7pp/年 的"超额"。

本模块把两件事收成一个开关组：
- `review_nav_adjusted`（默认 1）：复盘/PK/回填的场外净值是否走分红复权；置 0 回旧口径。
  ETF 一侧本来就是 qfq，不受该键影响。
- `benchmark_dividend_yield_pct`（默认 2.7，置 0 即纯价格指数）：给基准点位序列按交易日
  累乘股息，等价于"按区间天数折算"。

上游预算：**0 请求**。股息是常数、复权只在已取回的序列上做变换，基准仍走
`AKShareAdapter.get_benchmark_series()` 的类级 1h 缓存。

详见 docs/QUANT_DECISIONS_2026-10.md §5.1（裁定 A + B② + C，不接全收益指数）。
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.utils.timezone import now_beijing

logger = logging.getLogger(__name__)

NAV_ADJUSTED_CONFIG_KEY = "review_nav_adjusted"
DEFAULT_NAV_ADJUSTED = True

BENCH_DIV_YIELD_CONFIG_KEY = "benchmark_dividend_yield_pct"
# 沪深300 股息率约 2.7%/年（可配，用户可按自己口径校准）
DEFAULT_BENCH_DIV_YIELD_PCT = 2.7
BENCH_DIV_YIELD_MAX = 10.0

TRADING_DAYS_PER_YEAR = 252.0

CONFIG_DESCRIPTIONS = {
    NAV_ADJUSTED_CONFIG_KEY: "复盘/PK/建议回填的场外净值口径：1=分红复权（默认）/ 0=单位净值（旧口径）",
    BENCH_DIV_YIELD_CONFIG_KEY: "基准股息率（%/年，按交易日折算回基准点位序列）；0 = 纯价格指数（旧口径）",
}


async def load_caliber(db: Optional[AsyncSession] = None) -> dict:
    """读取生效口径：`{nav_adjusted: bool, bench_div_yield_pct: float}`

    db 省略时自开一个会话 —— 回填任务与调度器没有注入会话，而一轮只读一次，
    代价可以忽略。缺行/脏值一律回落默认（口径不能因为配置写错而悄悄变掉）。
    """
    if db is None:
        from backend.database import async_session_factory

        async with async_session_factory() as session:
            return await load_caliber(session)

    from backend.models.system_config import SystemConfig

    rows = (await db.execute(
        select(SystemConfig).where(
            SystemConfig.config_key.in_([
                NAV_ADJUSTED_CONFIG_KEY, BENCH_DIV_YIELD_CONFIG_KEY,
            ])
        )
    )).scalars().all()
    kv = {r.config_key: (r.config_value or "").strip() for r in rows}

    raw_nav = kv.get(NAV_ADJUSTED_CONFIG_KEY, "")
    nav_adjusted = DEFAULT_NAV_ADJUSTED
    if raw_nav:
        nav_adjusted = raw_nav.lower() not in ("0", "false", "off", "no")

    raw_div = kv.get(BENCH_DIV_YIELD_CONFIG_KEY, "")
    div_yield = DEFAULT_BENCH_DIV_YIELD_PCT
    if raw_div:
        try:
            div_yield = max(0.0, min(BENCH_DIV_YIELD_MAX, float(raw_div)))
        except (TypeError, ValueError):
            logger.warning(
                f"基准股息率配置无法解析（{raw_div!r}），用默认 {DEFAULT_BENCH_DIV_YIELD_PCT}"
            )

    return {"nav_adjusted": nav_adjusted, "bench_div_yield_pct": div_yield}


async def save_caliber(
    db: AsyncSession,
    nav_adjusted: Optional[bool] = None,
    bench_div_yield_pct: Optional[float] = None,
) -> dict:
    """写入口径（None 表示不动该项），返回落库后的生效值"""
    from backend.models.system_config import SystemConfig

    updates: dict[str, str] = {}
    if nav_adjusted is not None:
        updates[NAV_ADJUSTED_CONFIG_KEY] = "1" if nav_adjusted else "0"
    if bench_div_yield_pct is not None:
        updates[BENCH_DIV_YIELD_CONFIG_KEY] = str(
            max(0.0, min(BENCH_DIV_YIELD_MAX, float(bench_div_yield_pct)))
        )

    for key, value in updates.items():
        row = (await db.execute(
            select(SystemConfig).where(SystemConfig.config_key == key)
        )).scalars().first()
        if row:
            row.config_value = value
            row.updated_at = now_beijing()
        else:
            db.add(SystemConfig(
                config_key=key,
                config_value=value,
                description=CONFIG_DESCRIPTIONS.get(key),
                updated_at=now_beijing(),
            ))
    if updates:
        await db.commit()
    return await load_caliber(db)


def with_dividend_carry(
    series: list[tuple[str, float]], annual_yield_pct: float
) -> list[tuple[str, float]]:
    """价格指数点位序列 → 含息序列（逐交易日累乘 annual_yield_pct/252）

    累乘因子是前缀式的，任意子区间求比值时公共前缀自动约掉，剩下的正好是
    "该区间交易日数 × 单日股息" —— 也就是按区间天数折算，无需调用方再传日期。
    股息率为 0 时原样返回（回滚路径下基准完全不变）。
    """
    if not series:
        return []
    if not annual_yield_pct or annual_yield_pct <= 0:
        return list(series)
    step = 1.0 + annual_yield_pct / 100.0 / TRADING_DAYS_PER_YEAR
    out: list[tuple[str, float]] = []
    factor = 1.0
    for d, v in series:
        out.append((d, v * factor))
        factor *= step
    return out


def caliber_head_lines(policy: dict, *, extra: str = "", cash_line: str) -> list[str]:
    """三行口径头（Q11-C）：净值口径 / 基准口径 / 是否计息

    写进每一含"区间收益/年化/超额"的报告头部 —— 读者先看尺子再看数字，
    回滚后文案会跟着变（不会像硬编码注释那样骗人）。
    """
    nav = (
        "场外基金分红复权（前复权）、场内 ETF 前复权"
        if policy.get("nav_adjusted", DEFAULT_NAV_ADJUSTED)
        else "单位净值（未复权，除息日会记成下跌）"
    )
    div = float(policy.get("bench_div_yield_pct") or 0.0)
    bench = (
        f"沪深300 价格指数 + 股息 {div:g}%/年（按区间交易日折算）"
        if div > 0
        else "沪深300 价格指数（不含股息）"
    )
    lines = [
        f"> 净值口径：{nav}" + (f"；{extra}" if extra else ""),
        f"> 基准口径：{bench}",
        f"> 计息口径：{cash_line}",
    ]
    return lines
