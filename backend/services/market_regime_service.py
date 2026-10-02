"""市场环境指标服务 — 大盘估值分位 / 市场情绪 / 资金面

提供 MarketRegimeSnapshot 快照，供两个消费方使用：
1. factor_engine 三个市场环境因子（market_valuation / market_sentiment /
   market_fund_flow）通过模块级上下文读取快照打分；
2. quality_filter 动态阈值在极端估值区间调节买入阈值。

数据源全部来自 AKShare 免费接口：
- 大盘估值分位: 主源 stock_index_pe_lg("沪深300") 的「滚动市盈率」历史序列
  （乐咕，2005 年至今约 258 个月度点，实测可用）；主源失败时降级到
  stock_zh_index_value_csindex("000300") 的「市盈率1」日频序列，复用
  AKShareAdapter._index_value_cache 避免重复请求。序列本身另有 6 小时
  缓存 —— 分位计算只要快照那一次，日频主源却会让每次快照都拉一遍全量历史。
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
from backend.utils.stats import percentile_rank_inclusive
from backend.utils.timezone import beijing_today

logger = logging.getLogger(__name__)


@dataclass
class MarketRegimeSnapshot:
    """市场环境快照（单次分析周期内全局共享）"""
    # 大盘估值：沪深300 PE 近10年分位 (0~1)，越低越便宜
    valuation_percentile: Optional[float] = None
    valuation_date: Optional[str] = None          # 估值数据日期
    valuation_current_pe: Optional[float] = None  # 当前 PE
    # 参与分位计算的历史样本数：月频主源≈120、日频降级源≈2400，
    # 数量级差异必须暴露给消费方，否则 0.4 与 0.4 不是同一个置信度
    valuation_sample_points: Optional[int] = None
    # 市场情绪：涨跌家数比 (up-down)/(up+down)，-1~1
    adv_decline_ratio: Optional[float] = None
    up_count: Optional[int] = None
    down_count: Optional[int] = None
    # 资金面：上交所两融余额（元）及 7 日变化率
    margin_balance: Optional[float] = None
    margin_change_pct_7d: Optional[float] = None
    margin_date: Optional[str] = None
    fetched_at: str = field(default_factory=lambda: beijing_today().isoformat())


class MarketRegimeService:
    """市场环境指标服务 — 类级 TTL 缓存（日频数据，1 小时足够）"""

    _snapshot: Optional[MarketRegimeSnapshot] = None
    _snapshot_ts: float = 0.0
    _SNAPSHOT_TTL: float = 3600.0  # 1 小时
    _FAIL_TTL: float = 60.0        # 全部失败时的短缓存（防雪崩，不致降级 1 小时）
    _lock: Optional[asyncio.Lock] = None  # 2026-08-29 修复：并发快照请求穿透缓存

    # PE 历史序列缓存：序列是月频/日频长表，1 小时快照 TTL 若不带序列缓存，
    # 每天 24 次快照就会拉 24 遍同一段全量历史 —— 与防封禁目标相反。
    _pe_series_cache: Optional[tuple[list, list]] = None
    _pe_series_ts: float = 0.0
    _pe_series_fail_ts: float = 0.0
    _PE_SERIES_TTL: float = 6 * 3600.0

    # 分位计算参数
    _PE_LG_SYMBOL = "沪深300"
    _PE_LG_COLUMN = "滚动市盈率"
    _MIN_PE_POINTS = 60      # 月频主源 5 年 = 60 点；原 250 点门槛把月频源直接判死
    _PE_WINDOW_YEARS = 10    # 分位窗口按日历跨度取近 10 年，而非固定条数

    @classmethod
    def clear_cache(cls) -> None:
        cls._snapshot = None
        cls._snapshot_ts = 0.0
        cls._pe_series_cache = None
        cls._pe_series_ts = 0.0
        cls._pe_series_fail_ts = 0.0

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
        """获取沪深300 PE 历史序列（日期 + PE，按日期升序）

        主源 stock_index_pe_lg（乐咕，月频滚动市盈率，2005 年至今）；
        失败时降级 csindex（日频市盈率1），复用 AKShareAdapter._index_value_cache。
        结果带 _PE_SERIES_TTL 缓存。
        """
        now = time.time()
        cache = MarketRegimeService._pe_series_cache
        if cache is not None and now - MarketRegimeService._pe_series_ts < MarketRegimeService._PE_SERIES_TTL:
            return cache
        # 双源全挂时按 _FAIL_TTL 负缓存，别让每次快照都重打两个接口
        if cache is None and now - MarketRegimeService._pe_series_fail_ts < MarketRegimeService._FAIL_TTL:
            return None

        series = await self._fetch_pe_lg()
        if series is None:
            series = await self._fetch_pe_csindex()
        if series is None:
            MarketRegimeService._pe_series_fail_ts = time.time()
            return None

        MarketRegimeService._pe_series_cache = series
        MarketRegimeService._pe_series_ts = time.time()
        return series

    @staticmethod
    def _sort_series(dates: list, pes: list) -> tuple[list, list]:
        pairs = sorted(zip(dates, pes), key=lambda x: x[0])
        return [d for d, _ in pairs], [p for _, p in pairs]

    async def _fetch_pe_lg(self) -> Optional[tuple[list, list]]:
        """主源：乐咕沪深300 滚动市盈率（月频，约 20 年）"""
        from backend.utils.concurrency import run_with_timeout

        try:
            df = await run_with_timeout(
                ak.stock_index_pe_lg, symbol=self._PE_LG_SYMBOL, timeout=25.0
            )
        except Exception as e:
            logger.info(
                f"stock_index_pe_lg 不可用（降级 csindex）: {type(e).__name__}: {e}"
            )
            return None
        if df is None or df.empty or self._PE_LG_COLUMN not in df.columns:
            return None

        pes = []
        dates = []
        for _, row in df.iterrows():
            pe_val = row.get(self._PE_LG_COLUMN)
            try:
                pe_f = float(pe_val)
            except (TypeError, ValueError):
                continue
            # `> 0` 而非 `<= 0` 取反：NaN 两种比较都是 False，后者会把空值放进来
            if not pe_f > 0:
                continue
            dates.append(str(row.get("日期"))[:10])
            pes.append(pe_f)
        if not pes:
            return None
        return self._sort_series(dates, pes)

    async def _fetch_pe_csindex(self) -> Optional[tuple[list, list]]:
        """降级源：中证官网指数估值（日频市盈率1），复用适配器共享缓存"""
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
            try:
                df = await run_with_timeout(
                    ak.stock_zh_index_value_csindex, symbol=index_code, timeout=25.0
                )
            except Exception as e:
                logger.warning(
                    f"csindex 指数估值不可用（估值分位该项置空）: {type(e).__name__}: {e}"
                )
                return None
            if df is not None and not df.empty:
                AKShareAdapter._index_value_cache[index_code] = (now, df)

        if df is None or df.empty or "市盈率1" not in df.columns:
            return None

        try:
            pe_values = df["市盈率1"].astype(float).tolist()
        except (TypeError, ValueError):
            return None
        dates = df["日期"].astype(str).str[:10].tolist()

        pairs = [(d, p) for d, p in zip(dates, pe_values) if p is not None and p > 0]
        if not pairs:
            return None
        return self._sort_series([d for d, _ in pairs], [p for _, p in pairs])

    async def _fill_valuation_percentile(self, snap: MarketRegimeSnapshot) -> None:
        series = await self._get_index_pe_series()
        if not series:
            return
        dates, pe_list = series

        # 近 N 年日历窗口。旧写法取 valid[-1215:]（假定日频 1215 条≈5 年），
        # 换成月频源后那切片只剩 1 个点 —— 分位直接失真。
        cutoff = (beijing_today() - timedelta(days=int(365.25 * self._PE_WINDOW_YEARS))).isoformat()
        window = [
            (d, p) for d, p in zip(dates, pe_list)
            if p is not None and p > 0 and str(d)[:10] >= cutoff
        ]
        if len(window) < self._MIN_PE_POINTS:
            logger.info(
                f"估值历史数据不足: 窗口内 {len(window)} 点 < {self._MIN_PE_POINTS}，跳过分位计算"
            )
            return

        pe_values = [p for _, p in window]
        current_date, current_pe = window[-1]

        # 分位 = 历史中 <= 当前值 的占比（统一走 percentile_rank_inclusive，Q13：
        # 此前 index_valuation_service 用严格 <，同一指数两处会差 1 个点）
        rank = percentile_rank_inclusive(pe_values, current_pe)
        snap.valuation_percentile = round(rank, 4)
        snap.valuation_current_pe = round(current_pe, 2)
        snap.valuation_date = str(current_date)[:10]
        snap.valuation_sample_points = len(pe_values)

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

        end = beijing_today()
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
