"""市场环境指标服务 — 大盘估值分位 / 市场情绪 / 资金面

提供 MarketRegimeSnapshot 快照，供两个消费方使用：
1. factor_engine 三个市场环境因子（market_valuation / market_sentiment /
   market_fund_flow）通过模块级上下文读取快照打分；
2. quality_filter 动态阈值在极端估值区间调节买入阈值。

数据源全部来自 AKShare 免费接口：
- 大盘估值分位: stock_zh_index_value_csindex("000300") 的市盈率1历史序列，
  复用 AKShareAdapter._index_value_cache 避免重复请求；
- 市场情绪: MarketService.get_market_adv_decline 涨跌家数比；
- 资金面: stock_margin_sse 上交所融资融券余额 7 日变化率（深交所单日接口
  需逐日调用成本高，且沪深两融趋势高度同步，用沪市作代理）。

设计原则：任何指标获取失败时对应字段为 None，因子层降级为中性 0 分，
不抛异常、不阻塞主分析流程。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import akshare as ak  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class MarketRegimeSnapshot:
    """市场环境快照（单次分析周期内全局共享）"""
    # 大盘估值：沪深300 PE 近5年分位 (0~1)，越低越便宜
    valuation_percentile: Optional[float] = None
    valuation_date: Optional[str] = None          # 估值数据日期
    valuation_current_pe: Optional[float] = None  # 当前 PE
    # 市场情绪：涨跌家数比 (up-down)/(up+down)，-1~1
    adv_decline_ratio: Optional[float] = None
    up_count: Optional[int] = None
    down_count: Optional[int] = None
    # 资金面：上交所两融余额（元）及 7 日变化率
    margin_balance: Optional[float] = None
    margin_change_pct_7d: Optional[float] = None
    margin_date: Optional[str] = None
    fetched_at: str = field(default_factory=lambda: date.today().isoformat())


class MarketRegimeService:
    """市场环境指标服务 — 类级 TTL 缓存（日频数据，1 小时足够）"""

    _snapshot: Optional[MarketRegimeSnapshot] = None
    _snapshot_ts: float = 0.0
    _SNAPSHOT_TTL: float = 3600.0  # 1 小时
    _FAIL_TTL: float = 60.0        # 全部失败时的短缓存（防雪崩，不致降级 1 小时）
    _lock: Optional[asyncio.Lock] = None  # 2026-08-29 修复：并发快照请求穿透缓存

    @classmethod
    def clear_cache(cls) -> None:
        cls._snapshot = None
        cls._snapshot_ts = 0.0

    async def get_snapshot(self) -> MarketRegimeSnapshot:
        """获取市场环境快照（带缓存；单项失败对应字段保持 None）"""
        if MarketRegimeService._lock is None:
            MarketRegimeService._lock = asyncio.Lock()

        now = time.time()
        if MarketRegimeService._snapshot is not None:
            ttl = (
                MarketRegimeService._SNAPSHOT_TTL
                if self._snapshot_has_data(MarketRegimeService._snapshot)
                else MarketRegimeService._FAIL_TTL
            )
            if now - MarketRegimeService._snapshot_ts < ttl:
                return MarketRegimeService._snapshot

        async with MarketRegimeService._lock:
            # 双重检查：等锁期间可能已被其他协程填充
            now = time.time()
            if MarketRegimeService._snapshot is not None:
                ttl = (
                    MarketRegimeService._SNAPSHOT_TTL
                    if self._snapshot_has_data(MarketRegimeService._snapshot)
                    else MarketRegimeService._FAIL_TTL
                )
                if now - MarketRegimeService._snapshot_ts < ttl:
                    return MarketRegimeService._snapshot

            snap = await self._fetch_snapshot()
            MarketRegimeService._snapshot = snap
            MarketRegimeService._snapshot_ts = time.time()
            return snap

    @staticmethod
    def _snapshot_has_data(snap: MarketRegimeSnapshot) -> bool:
        """全字段 None（三源全挂）的快照只允许短缓存，避免降级 1 小时"""
        return (
            snap.valuation_percentile is not None
            or snap.adv_decline_ratio is not None
            or snap.margin_change_pct_7d is not None
        )

    async def _fetch_snapshot(self) -> MarketRegimeSnapshot:
        """实际拉取三项指标（锁内调用，全部吞异常保持字段 None）"""
        snap = MarketRegimeSnapshot()

        # 1. 大盘估值分位
        try:
            await self._fill_valuation_percentile(snap)
        except Exception as e:
            logger.warning(
                f"大盘估值分位获取失败（该项置空，因子中性分）: "
                f"{type(e).__name__}: {e}"
            )

        # 2. 市场情绪（涨跌家数比）
        try:
            await self._fill_adv_decline(snap)
        except Exception as e:
            logger.warning(
                f"市场情绪获取失败（该项置空，因子中性分）: "
                f"{type(e).__name__}: {e}"
            )

        # 3. 资金面（两融余额 7 日变化率）
        try:
            await self._fill_margin_flow(snap)
        except Exception as e:
            logger.warning(
                f"资金面获取失败（该项置空，因子中性分）: "
                f"{type(e).__name__}: {e}"
            )

        logger.info(
            "市场环境快照: 估值分位=%s, 涨跌比=%s, 两融7日变化=%s",
            snap.valuation_percentile, snap.adv_decline_ratio, snap.margin_change_pct_7d,
        )
        return snap

    # ── 大盘估值分位 ──────────────────────────────────────────────

    async def _get_index_pe_series(self) -> Optional[tuple[list, list]]:
        """获取沪深300 PE 历史序列（日期 + PE）

        优先复用 AKShareAdapter._index_value_cache（与 PE/PB 填充共享），
        未命中或过期时发起一次网络请求并写回缓存。
        """
        from backend.data_sources.akshare_adapter import AKShareAdapter

        index_code = "000300"
        now = time.time()
        cached = AKShareAdapter._index_value_cache.get(index_code)
        df = None
        if cached is not None:
            ts, cached_df = cached
            if now - ts < AKShareAdapter._SHARED_CACHE_TTL and cached_df is not None:
                df = cached_df

        if df is None:
            from backend.utils.concurrency import run_with_timeout
            df = await run_with_timeout(
                ak.stock_zh_index_value_csindex, symbol=index_code, timeout=25.0
            )
            if df is not None and not df.empty:
                AKShareAdapter._index_value_cache[index_code] = (now, df)

        if df is None or df.empty:
            return None

        pe = df["市盈率1"].astype(float)
        dates = df["日期"].astype(str).tolist()
        return dates, pe.tolist()

    async def _fill_valuation_percentile(self, snap: MarketRegimeSnapshot) -> None:
        series = await self._get_index_pe_series()
        if not series:
            return
        dates, pe_list = series

        # 过滤 None/NaN
        valid = [(d, p) for d, p in zip(dates, pe_list) if p is not None and p > 0]
        if len(valid) < 250:  # 至少一年数据
            logger.info(f"估值历史数据不足: {len(valid)} 行，跳过分位计算")
            return

        # 近 5 年窗口（约 1215 个交易日）
        window = valid[-1215:]
        pe_values = [p for _, p in window]
        current_pe = window[-1][1]
        current_date = window[-1][0]

        # 分位 = 历史中 <= 当前值 的占比
        rank = sum(1 for p in pe_values if p <= current_pe) / len(pe_values)
        snap.valuation_percentile = round(rank, 4)
        snap.valuation_current_pe = round(current_pe, 2)
        snap.valuation_date = str(current_date)[:10]

    # ── 市场情绪 ─────────────────────────────────────────────────

    async def _fill_adv_decline(self, snap: MarketRegimeSnapshot) -> None:
        from backend.services.market_service import MarketService

        svc = MarketService()
        adv = await svc.get_market_adv_decline()
        if adv is None or (adv.up_count + adv.down_count) == 0:
            return
        snap.up_count = adv.up_count
        snap.down_count = adv.down_count
        snap.adv_decline_ratio = round(
            (adv.up_count - adv.down_count) / (adv.up_count + adv.down_count), 4
        )

    # ── 资金面（两融余额） ───────────────────────────────────────

    async def _fill_margin_flow(self, snap: MarketRegimeSnapshot) -> None:
        from backend.utils.concurrency import run_with_timeout

        end = date.today()
        start = end - timedelta(days=30)  # 日历30天 ≈ 20+ 交易日，足够取 7 日窗口
        df = await run_with_timeout(
            ak.stock_margin_sse,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            timeout=25.0,
        )
        if df is None or df.empty or "融资融券余额" not in df.columns:
            return

        df = df.sort_values("信用交易日期")
        balances = df["融资融券余额"].astype(float).tolist()
        dates = df["信用交易日期"].astype(str).tolist()
        if len(balances) < 8 or balances[-8] <= 0:
            return

        snap.margin_balance = balances[-1]
        snap.margin_change_pct_7d = round(balances[-1] / balances[-8] - 1, 6)
        snap.margin_date = dates[-1]
