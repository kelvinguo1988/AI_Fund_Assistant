"""指数估值 + 场外交易状态服务 — 模块 A'（交易提示）/ C'（高低估区间）

指数高低估（C'）:
- 数据源：乐咕乐股主要指数 PE 历史（非东财域名，不受东财限连影响）
- 支持指数：沪深300 / 中证500 / 上证50 / 中证1000（创业板指等暂无数据源）
- 口径：**近一年 PE 分位**（乐咕序列约一年日线）——<30% 低估 / 30~70% 合理 / >70% 高估
- 映射：场外基金基准/名称含指数词 → 对应区间提示；主动基金无官方估值不标

场外交易可执行性（A'）:
- fund_open_fund_daily_em 全市场单请求（申购/赎回状态/手续费），类级缓存 1h
- 暂停申购/限大额 → 买入阻断；暂停赎回 → 卖出阻断
"""

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# PE 分位区间（近一年，市场通行口径）
LOW_THRESHOLD = 30.0
HIGH_THRESHOLD = 70.0

# 乐咕支持的指数 → 名称关键词（基金基准/名称匹配用）
SUPPORTED_INDEXES: dict[str, list[str]] = {
    "沪深300": ["沪深300", "沪深 300"],
    "中证500": ["中证500", "中证 500"],
    "上证50": ["上证50", "上证 50"],
    "中证1000": ["中证1000", "中证 1000"],
}


def pe_zone(percentile: float) -> str:
    if percentile < LOW_THRESHOLD:
        return "低估"
    if percentile > HIGH_THRESHOLD:
        return "高估"
    return "合理"


class IndexValuationService:
    """主要指数 PE 分位（类级缓存 1h，日频数据日内一次足够）"""

    _cache: Optional[list[dict]] = None
    _ts: float = 0.0
    _TTL = 3600.0

    @classmethod
    async def get_valuations(cls, force: bool = False) -> list[dict]:
        now = time.time()
        if not force and cls._cache is not None and now - cls._ts < cls._TTL:
            return cls._cache

        import numpy as np

        def _fetch_all():
            import akshare as ak
            out = []
            for index_name in SUPPORTED_INDEXES:
                try:
                    df = ak.stock_index_pe_lg(symbol=index_name)
                    pe_col = "滚动市盈率"
                    pe_series = df[pe_col].dropna().astype(float)
                    if pe_series.empty:
                        continue
                    current_pe = float(pe_series.iloc[-1])
                    # 近一年分位（序列本身约一年）
                    percentile = float((pe_series < current_pe).mean() * 100)
                    out.append({
                        "index": index_name,
                        "pe": round(current_pe, 2),
                        "percentile_1y": round(percentile, 1),
                        "zone": pe_zone(percentile),
                        "advice": (
                            "低估区间——适合定投加码"
                            if percentile < LOW_THRESHOLD
                            else "高估区间——注意减投/止盈"
                            if percentile > HIGH_THRESHOLD
                            else "合理区间——正常持有/定投"
                        ),
                        "updated": str(df["日期"].iloc[-1]),
                    })
                except Exception as e:
                    logger.warning(f"指数 PE 获取失败 {index_name}: {e}")
            return out

        from backend.utils.concurrency import run_with_timeout

        try:
            # 走全局 akshare 信号量 + 强制超时（乐咕接口无 patch 超时保护）
            rows = await run_with_timeout(_fetch_all, timeout=100.0)
        except Exception as e:
            logger.warning(f"指数 PE 获取失败: {e}")
            return cls._cache or []
        if rows:
            cls._cache = rows
            cls._ts = now
        return rows or (cls._cache or [])

    # 基准指数占比提取（近似映射用）：指数名 ... ×占比%
    _BENCH_RATIO_RE = r"({})[^0-9×*]*[×*](\d+(?:\.\d+)?)%"

    @classmethod
    def match_fund_hint(
        cls, name: str, benchmark: Optional[str],
        is_fixed_income: bool = False,
    ) -> Optional[dict]:
        """基金名称/基准匹配指数 → 高低估区间提示

        2026-08-31 增强（用户确认）：主动基金（基准为宽基组合）用基准中
        占比最高的支持指数**近似映射**，提示标注"近似"及依据；
        固收+/偏债 基金权益占比低，高低估不适用，返回 None。
        """
        if is_fixed_income:
            return None
        # 1. 直接匹配：基金**名称**含支持指数词（如"天弘沪深300ETF联接"）
        for index_name, keywords in SUPPORTED_INDEXES.items():
            if any(k in (name or "") for k in keywords):
                for v in (cls._cache or []):
                    if v["index"] == index_name:
                        return cls._build_hint(v, "跟踪指数")
        # 2. 基准占比解析（仅基准含指数词时——指数基金占 70%+ 为直接跟踪，
        #    主动基金 20~70% 为业绩基准近似映射，<20% 无近似意义）
        if benchmark:
            best: Optional[tuple[str, float]] = None
            for index_name, keywords in SUPPORTED_INDEXES.items():
                m = re.search(
                    cls._BENCH_RATIO_RE.format(keywords[0]), benchmark
                )
                if m:
                    ratio = float(m.group(2))  # g1=指数名 g2=占比
                    if best is None or ratio > best[1]:
                        best = (index_name, ratio)
            if best and best[1] >= 20.0:
                for v in (cls._cache or []):
                    if v["index"] == best[0]:
                        if best[1] >= 70.0:
                            return cls._build_hint(v, "跟踪指数")
                        hint = cls._build_hint(
                            v, f"按基准 {best[0]}（占比 {best[1]:.0f}%）近似"
                        )
                        if hint:
                            hint["approximate"] = True
                        return hint
        return None

    @classmethod
    def _build_hint(cls, v: dict, basis: str) -> Optional[dict]:
        zone = v["zone"]
        level = "positive" if zone == "低估" else "warning" if zone == "高估" else "info"
        return {
            "type": "valuation",
            "level": level,
            "message": (
                f"{basis} {v['index']}：PE {v['pe']}，近一年分位 "
                f"{v['percentile_1y']}%（{zone}）——{v['advice']}"
            ),
        }


class OtcTradeStatusService:
    """场外申购/赎回状态（全市场单请求，类级缓存 1h）"""

    _cache: Optional[dict[str, dict]] = None
    _ts: float = 0.0
    _TTL = 3600.0

    @classmethod
    async def get_status_map(cls, force: bool = False) -> dict[str, dict]:
        now = time.time()
        if not force and cls._cache is not None and now - cls._ts < cls._TTL:
            return cls._cache

        def _fetch_all():
            import akshare as ak
            df = ak.fund_open_fund_daily_em()
            out: dict[str, dict] = {}
            for _, row in df.iterrows():
                code = str(row.get("基金代码", ""))
                if not code:
                    continue
                out[code] = {
                    "purchase": str(row.get("申购状态", "")),
                    "redeem": str(row.get("赎回状态", "")),
                    "fee": str(row.get("手续费", "") or ""),
                }
            return out

        from backend.utils.concurrency import run_with_timeout

        try:
            # 东财域名接口：统一走信号量串行，避免绕过防封禁管线
            data = await run_with_timeout(_fetch_all, timeout=45.0)
            if data:
                cls._cache = data
                cls._ts = now
            return data or (cls._cache or {})
        except Exception as e:
            logger.warning(f"场外申购状态获取失败: {e}")
            return cls._cache or {}

    @classmethod
    def trade_hints(cls, code: str, status: Optional[dict]) -> list[dict]:
        """申购/赎回可执行性提示（A'）"""
        hints: list[dict] = []
        if not status:
            return hints
        purchase = status.get("purchase", "")
        redeem = status.get("redeem", "")
        fee = status.get("fee", "")

        if purchase == "暂停申购":
            hints.append({
                "type": "purchase_blocked", "level": "warning",
                "message": "该基金暂停申购，买入提示暂不可执行",
            })
        elif purchase == "限大额":
            hints.append({
                "type": "purchase_limited", "level": "info",
                "message": "该基金限大额申购，单笔金额受限制",
            })
        elif purchase == "封闭期":
            hints.append({
                "type": "purchase_blocked", "level": "warning",
                "message": "该基金处于封闭期，暂不可申购",
            })
        if redeem == "暂停赎回":
            hints.append({
                "type": "redeem_blocked", "level": "warning",
                "message": "该基金暂停赎回，卖出提示暂不可执行",
            })
        if fee and fee not in ("0.00%",):
            hints.append({
                "type": "fee", "level": "info",
                "message": f"申购费率 {fee}（第三方平台常有折扣）",
            })
        return hints
