"""场内 ETF 信号服务 — 交易提示（模块 A）+ 潜力扫描（模块 B）

仅适用场内 ETF：换手率/量比/成交额/主力资金流/折溢价来自交易所实时撮合，
场外基金无这些概念。数据全部来自已有的 fund_etf_spot_em 快照（零新增请求），
复用其缓存与东财→腾讯降级链（腾讯降级时场内字段缺失，相关规则自动跳过）。

模块 A 交易提示规则（默认值，2026-08-31 与用户确认）:
- 流动性风险: 日成交额 < 1000 万 → 不建议买入提示
- 溢价追高: 溢价率 > 1%（价格高于 IOPV）→ 买入提示降级
- 折价加分: 溢价率 < -0.5%（价格低于 IOPV）→ 低成本买入提示
- 放量流出: 量比 > 2 且 主力净流入占比 < -10% → 资金出逃预警
- 放量流入: 量比 > 1.5 且 主力净流入占比 > 10% → 买入信号确认

模块 B 潜力扫描（全市场 ~1600 只，单次快照）:
- 量价齐升榜: 涨幅 > 2% 且 量比 > 1.5 且 主力净流入 > 0
- 资金流入榜: 主力净流入占比 Top 20（成交额 > 5000 万过滤）
- 异动榜: 换手率 > 5% 或 量比 > 3（成交额 > 1000 万过滤）
所有榜单为量价特征排名，仅供参考不构成投资建议。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── 规则阈值（模块 A）────────────────────────────────────────────────
LIQUIDITY_MIN_AMOUNT = 1e7      # 1000 万
PREMIUM_WARN = 1.0              # 溢价率 > 1% 警示
DISCOUNT_BONUS = -0.5           # 溢价率 < -0.5%（折价）加分
OUTFLOW_VOLUME_RATIO = 2.0
OUTFLOW_MAIN_PCT = -10.0
INFLOW_VOLUME_RATIO = 1.5
INFLOW_MAIN_PCT = 10.0


def evaluate_etf_hints(spot: dict) -> list[dict]:
    """对单只 ETF 的行情快照生成场内交易提示

    Args:
        spot: _get_etf_spot 行（price/pct/iopv/turnover_rate/volume_ratio/
              main_inflow_pct/amount；腾讯降级时场内字段为 None）

    Returns:
        [{type, level, message}] — level: warning/info/positive
    """
    hints: list[dict] = []
    amount = spot.get("amount")
    price = spot.get("price")
    iopv = spot.get("iopv")
    premium = spot.get("premium_discount")
    turnover = spot.get("turnover_rate")
    vol_ratio = spot.get("volume_ratio")
    main_pct = spot.get("main_inflow_pct")

    # 1. 流动性风险（买入阻断）
    if amount is not None and amount < LIQUIDITY_MIN_AMOUNT:
        hints.append({
            "type": "liquidity", "level": "warning",
            "message": f"日成交额 {amount / 1e7:.2f} 千万，流动性不足，谨慎买入",
        })

    # 2/3. 折溢价：优先 IOPV 自算（口径无歧义），缺 IOPV 时用东财折价率列
    premium_pct: Optional[float] = None
    if price is not None and iopv and iopv > 0:
        premium_pct = (price / iopv - 1) * 100
    elif premium is not None:
        premium_pct = -premium  # 东财列口径为折价率，溢价取负

    if premium_pct is not None:
        if premium_pct > PREMIUM_WARN:
            hints.append({
                "type": "premium", "level": "warning",
                "message": f"溢价 {premium_pct:+.2f}%（价格高于 IOPV），追高买入需谨慎",
            })
        elif premium_pct < DISCOUNT_BONUS:
            hints.append({
                "type": "discount", "level": "positive",
                "message": f"折价 {abs(premium_pct):.2f}%（价格低于 IOPV），低成本买入窗口",
            })

    # 4/5. 量价资金确认
    if vol_ratio is not None and main_pct is not None:
        if vol_ratio > OUTFLOW_VOLUME_RATIO and main_pct < OUTFLOW_MAIN_PCT:
            hints.append({
                "type": "outflow", "level": "warning",
                "message": f"放量流出：量比 {vol_ratio:.1f}，主力净流出 {abs(main_pct):.1f}%",
            })
        elif vol_ratio > INFLOW_VOLUME_RATIO and main_pct > INFLOW_MAIN_PCT:
            hints.append({
                "type": "inflow", "level": "positive",
                "message": f"放量流入：量比 {vol_ratio:.1f}，主力净流入 {main_pct:.1f}%，买入信号获确认",
            })

    # 换手率背景信息（极高换手单独提示）
    if turnover is not None and turnover > 15:
        hints.append({
            "type": "turnover", "level": "info",
            "message": f"换手率 {turnover:.1f}%，交投异常活跃，波动可能加剧",
        })

    return hints


# ── 模块 B：潜力扫描规则 ─────────────────────────────────────────────

SCAN_MOVERS_PCT = 2.0
SCAN_MOVERS_VOL_RATIO = 1.5
SCAN_INFLOW_TOP = 20
SCAN_INFLOW_MIN_AMOUNT = 5e7    # 5000 万
SCAN_UNUSUAL_TURNOVER = 5.0
SCAN_UNUSUAL_VOL_RATIO = 3.0
SCAN_UNUSUAL_MIN_AMOUNT = 1e7


def _row_with_fields(row: dict) -> bool:
    """行情行含场内字段（东财快照）；腾讯降级行缺字段不参与扫描"""
    return row.get("volume_ratio") is not None


def scan_potential(spot_map: dict[str, dict], pool_codes: set[str]) -> dict:
    """全市场潜力 ETF 扫描

    Args:
        spot_map: {code: spot 行}（全市场快照）
        pool_codes: 基金池内代码集合（交叉标注）

    Returns:
        {movers, inflow, unusual, scanned}
        榜单项含 in_pool 标记与完整场内字段
    """
    scanned = 0
    movers: list[dict] = []
    inflow: list[dict] = []
    unusual: list[dict] = []

    for code, row in spot_map.items():
        if not _row_with_fields(row):
            continue
        scanned += 1
        pct = row.get("pct")
        vol_ratio = row.get("volume_ratio")
        main_pct = row.get("main_inflow_pct")
        turnover = row.get("turnover_rate")
        amount = row.get("amount")

        base = {
            "code": code,
            "name": row.get("name", ""),
            "price": row.get("price"),
            "pct": pct,
            "turnover_rate": turnover,
            "volume_ratio": vol_ratio,
            "main_inflow_pct": main_pct,
            "amount": amount,
            "in_pool": code in pool_codes,
        }

        # 量价齐升榜（流动性过滤：成交额 > 1000 万）
        if (pct is not None and pct > SCAN_MOVERS_PCT
                and vol_ratio is not None and vol_ratio > SCAN_MOVERS_VOL_RATIO
                and main_pct is not None and main_pct > 0
                and amount is not None and amount > 1e7):
            movers.append(base)

        # 资金流入榜（主力占比降序 Top N，流动性过滤）
        if (main_pct is not None and amount is not None
                and amount > SCAN_INFLOW_MIN_AMOUNT):
            inflow.append(base)

        # 异动榜
        if amount is not None and amount > SCAN_UNUSUAL_MIN_AMOUNT and (
            (turnover is not None and turnover > SCAN_UNUSUAL_TURNOVER)
            or (vol_ratio is not None and vol_ratio > SCAN_UNUSUAL_VOL_RATIO)
        ):
            unusual.append(base)

    inflow.sort(key=lambda x: x.get("main_inflow_pct") or 0, reverse=True)
    movers.sort(key=lambda x: x.get("pct") or 0, reverse=True)
    unusual.sort(key=lambda x: (x.get("volume_ratio") or 0), reverse=True)

    return {
        "scanned": scanned,
        "movers": movers[:30],
        "inflow": inflow[:SCAN_INFLOW_TOP],
        "unusual": unusual[:30],
    }
