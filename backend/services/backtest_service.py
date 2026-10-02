"""信号回测服务 — 将历史信号与净值对齐，模拟仓位策略累计收益

口径要点（2026-10-02 第二批 Q6 固化，详见 docs/QUANT_DECISIONS_2026-10.md §5.1）：
- 三条基线：满仓买入持有（= 基金净值曲线）、静态半仓（恒 50%、零调仓、不计费）、
  策略曲线；**头号指标是 excess_vs_static_half**，excess_return 只是对满仓持有的差值，
  在上涨市里主要由"半仓敞口"决定而非信号能力。
- 仓位状态机默认「延续最近一次信号」，漏跑一天分析不再等于被动回半仓 + 白扣一次换仓费。
- 未平仓的现金部分按 **0% 计息（不计息）**，与 fund_compare 的 RF_ANNUAL=2.0 口径不同，
  展示文案必须写明"不计息"。
- 样本下限（非 hold 信号数 / 信号覆盖交易日占比）不足时只出 caveat，不给出策略结论。
"""
from backend.utils.timezone import now_beijing

import logging
from datetime import date, datetime, time
from typing import Optional

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.schemas.backtest import BacktestPoint, BacktestSummary

logger = logging.getLogger(__name__)

# 信号强度 → 仓位比例映射
POSITION_MAP = {
    "heavy_buy": 0.9,
    "moderate_buy": 0.7,
    "hold": 0.5,
    "moderate_sell": 0.3,
    "heavy_sell": 0.1,
}

# 单次调仓综合费率（申购+赎回各按半程合计的典型值）
# 用于让回测收益更接近真实申赎成本，避免零费用下高换手策略超额虚高
DEFAULT_ROUND_TRIP_FEE_PCT = 0.6

# system_config 键与取值范围（0 = 不计成本，回到纯信号口径）；
# 实际生效值走 load_fee_pct()，可在回测页调整
FEE_CONFIG_KEY = "backtest_fee_pct"
FEE_MIN, FEE_MAX = 0.0, 5.0

# ── 回测度量口径（Q6）：三条 system_config 键即回滚路径 ────────────────
# 仓位延续：1=沿用最近一次信号的仓位（默认）；0=旧行为，无信号日回落半仓
CARRY_CONFIG_KEY = "backtest_carry_position"
DEFAULT_CARRY_POSITION = True
# 样本下限：非 hold 信号数低于此值 → 只出 caveat。0 = 不拦（回到今日行为）
MIN_SIGNALS_CONFIG_KEY = "backtest_min_signals"
DEFAULT_MIN_SIGNALS = 8
# 信号覆盖交易日占比下限（%）。0 = 不拦
MIN_COVERAGE_CONFIG_KEY = "backtest_min_coverage_pct"
DEFAULT_MIN_COVERAGE_PCT = 30.0

# 净值窗口起点之前的信号，最多容忍顺延多少自然日（见
# _align_signals_to_trading_days）：周末 2 天、元旦/清明 3~4 天、
# 国庆/春节 8 天长假都要覆盖得到，再早的信号与这段净值无关。
_MAX_PRE_WINDOW_SIGNAL_DAYS = 10


async def load_fee_pct(db: AsyncSession) -> float:
    """读取回测调仓费率；未配置/非法值回落到默认，并夹在合法区间内"""
    from backend.models.system_config import SystemConfig

    row = (await db.execute(
        select(SystemConfig).where(SystemConfig.config_key == FEE_CONFIG_KEY)
    )).scalars().first()
    if row is None or not (row.config_value or "").strip():
        return DEFAULT_ROUND_TRIP_FEE_PCT
    try:
        value = float(row.config_value)
    except (TypeError, ValueError):
        logger.warning(
            f"回测费率配置无法解析（{row.config_value!r}），用默认 {DEFAULT_ROUND_TRIP_FEE_PCT}"
        )
        return DEFAULT_ROUND_TRIP_FEE_PCT
    return min(max(value, FEE_MIN), FEE_MAX)


async def save_fee_pct(db: AsyncSession, fee_pct: float) -> float:
    """写入回测调仓费率（夹在 [FEE_MIN, FEE_MAX]）"""
    from backend.models.system_config import SystemConfig

    value = min(max(float(fee_pct), FEE_MIN), FEE_MAX)
    row = (await db.execute(
        select(SystemConfig).where(SystemConfig.config_key == FEE_CONFIG_KEY)
    )).scalars().first()
    if row:
        row.config_value = str(value)
        row.updated_at = now_beijing()
    else:
        db.add(SystemConfig(
            config_key=FEE_CONFIG_KEY,
            config_value=str(value),
            description="回测单次调仓综合费率（%），0 表示不计交易成本",
            updated_at=now_beijing(),
        ))
    await db.commit()
    return value


async def load_measurement_policy(db: AsyncSession) -> dict:
    """读取回测度量口径（仓位延续 / 样本下限）

    批量回测应像费率一样在一轮开始时读一次、逐只复用：中途改口径会让
    同一轮结果内部不可比。
    """
    from backend.models.system_config import SystemConfig

    rows = (await db.execute(select(SystemConfig))).scalars().all()
    kv = {r.config_key: (r.config_value or "").strip() for r in rows}

    def _flag(key: str, default: bool) -> bool:
        raw = kv.get(key)
        if raw in (None, ""):
            return default
        # 兼容 true/false 与 1/0 两种写法（配置文件常被手工编辑）
        return raw not in ("0", "false", "False")

    def _num(key: str, default: float) -> float:
        try:
            return float(kv.get(key) or default)
        except (TypeError, ValueError):
            logger.warning(f"回测口径配置无法解析（{key}={kv.get(key)!r}），用默认 {default}")
            return default

    return {
        "carry_position": _flag(CARRY_CONFIG_KEY, DEFAULT_CARRY_POSITION),
        "min_signals": max(0, int(_num(MIN_SIGNALS_CONFIG_KEY, DEFAULT_MIN_SIGNALS))),
        "min_coverage_pct": max(
            0.0, min(100.0, _num(MIN_COVERAGE_CONFIG_KEY, DEFAULT_MIN_COVERAGE_PCT))
        ),
    }


async def save_measurement_policy(
    db: AsyncSession,
    carry_position: Optional[bool] = None,
    min_signals: Optional[int] = None,
    min_coverage_pct: Optional[float] = None,
) -> dict:
    """写入回测度量口径（None 表示不动该项），返回落库后的生效值"""
    from backend.models.system_config import SystemConfig

    updates: dict[str, str] = {}
    if carry_position is not None:
        updates[CARRY_CONFIG_KEY] = "1" if carry_position else "0"
    if min_signals is not None:
        updates[MIN_SIGNALS_CONFIG_KEY] = str(max(0, int(min_signals)))
    if min_coverage_pct is not None:
        updates[MIN_COVERAGE_CONFIG_KEY] = str(
            max(0.0, min(100.0, float(min_coverage_pct)))
        )

    for key, value in updates.items():
        row = (await db.execute(
            select(SystemConfig).where(SystemConfig.config_key == key)
        )).scalars().first()
        if row:
            row.config_value = value
            row.updated_at = now_beijing()
        else:
            db.add(SystemConfig(config_key=key, config_value=value, updated_at=now_beijing()))
    await db.commit()
    return await load_measurement_policy(db)


class BacktestService:
    """信号回测服务"""

    # 净值短期缓存：{(fund_code, period): (timestamp, fund_data)}
    # 用户反复回测同一基金时避免重复拉取全量净值（10 分钟内命中）
    _nav_cache: dict[tuple[str, int], tuple[float, object]] = {}
    _NAV_CACHE_TTL = 600.0  # 10 分钟
    # key 是 (代码, 区间) 且区间由入参决定，缓存值为整条净值序列：不设上限时
    # 反复变换区间回测会让字典只增不减（TTL 只挡读取，不淘汰条目）
    _NAV_CACHE_MAX = 200

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _get_nav_series(self, fund_code: str, period: int):
        """获取净值序列（10 分钟缓存，失败/过期时重新拉取）"""
        import time as _time
        key = (fund_code, period)
        now = _time.time()
        cached = BacktestService._nav_cache.get(key)
        if cached is not None and now - cached[0] < BacktestService._NAV_CACHE_TTL:
            return cached[1]
        from backend.data_sources.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter()
        try:
            fund_data = await adapter.get_fund_data(fund_code, period=period)
        except Exception as e:
            # adapter 主备双失败现在上抛（为打通降级链），回测按"无数据"处理
            logger.warning(f"回测净值拉取失败 {fund_code}: {e}")
            return None
        if fund_data is not None:
            while len(BacktestService._nav_cache) >= BacktestService._NAV_CACHE_MAX:
                # dict 保持插入序，弹最早写入的键即可（净值缓存无严格 LRU 语义需求）
                BacktestService._nav_cache.pop(next(iter(BacktestService._nav_cache)), None)
            BacktestService._nav_cache[key] = (now, fund_data)
        return fund_data

    async def run_backtest(
        self,
        fund_id: int,
        period: int = 365,
        effectiveness_window: int = 5,
        fee_pct: Optional[float] = None,
        policy: Optional[dict] = None,
    ) -> Optional[BacktestSummary]:
        """运行信号回测

        Args:
            fund_id: 基金 ID
            period: 回测天数（净值序列长度）
            effectiveness_window: 信号有效性评估窗口（交易日数）
            fee_pct: 单次调仓综合费率（%）；None 时读 system_config 配置
                     批量回测应在轮次开始时 load_fee_pct 一次，逐只复用同一口径
                     （费率中途被改会让同轮结果不可比）
            policy: 度量口径（load_measurement_policy 的返回）；None 时现读
                    与 fee_pct 同理，批量回测一轮只读一次

        Returns:
            BacktestSummary 或 None（基金不存在 / 无净值数据）
        """
        # 1. 查询基金信息
        fund = await self._get_fund(fund_id)
        if fund is None:
            return None

        # 2. 获取净值序列（10 分钟缓存）
        fund_data = await self._get_nav_series(fund.code, period)

        if not fund_data or not fund_data.close_history or not fund_data.date_history:
            logger.warning(f"基金 {fund.code} 无净值数据")
            return None

        dates = fund_data.date_history
        navs = fund_data.close_history

        if len(dates) != len(navs):
            logger.warning(f"基金 {fund.code} 日期/净值序列长度不一致")
            return None

        # 3. 获取该基金的历史信号
        signal_map = await self._get_signal_map(fund_id)

        # 4. 未显式传费率/口径时读配置
        if fee_pct is None:
            fee_pct = await load_fee_pct(self.db)
        if policy is None:
            policy = await load_measurement_policy(self.db)

        # 5. 按日期对齐 + 计算累计收益
        points = self._build_points(
            dates, navs, signal_map, effectiveness_window,
            fee_pct=fee_pct, carry_position=policy["carry_position"],
        )

        # 6. 计算统计指标（三条基线 + 两个超额口径）
        total_nav_return = points[-1].nav_return if points else 0.0
        total_strategy_return = points[-1].strategy_return if points else 0.0
        baseline_buy_hold = total_nav_return
        baseline_static_half = points[-1].baseline_static_half if points else 0.0
        excess_return = round(total_strategy_return - baseline_buy_hold, 4)
        excess_vs_static_half = round(total_strategy_return - baseline_static_half, 4)
        max_drawdown = self._calc_max_drawdown(points)

        signal_idx = [i for i, p in enumerate(points) if p.signal_direction is not None]
        signal_count = len(signal_idx)
        signal_count_non_hold = sum(
            1 for p in points if p.signal_direction in ("buy", "sell")
        )
        coverage_ratio = round(signal_count / len(points), 4) if points else 0.0

        # 回测区间的真实起点 = 该基金首次被分析的交易日；池子规模取该时点入池数，
        # 用于标注"样本由入池时点决定"的选择偏差（代码无法消除，只能显示）
        coverage_start = points[signal_idx[0]].date if signal_idx else None
        coverage_days = (signal_idx[-1] - signal_idx[0] + 1) if signal_idx else 0
        pool_size_at = await self._pool_size_at(coverage_start)

        low_sample, caveat = self._sample_gate(
            signal_count_non_hold=signal_count_non_hold,
            coverage_ratio=coverage_ratio,
            min_signals=policy["min_signals"],
            min_coverage_pct=policy["min_coverage_pct"],
        )

        # 7. 信号有效性统计
        eff_stats = self._calc_effectiveness_stats(points)

        return BacktestSummary(
            fund_code=fund.code,
            fund_name=fund.name or fund.code,
            period=period,
            total_nav_return=total_nav_return,
            total_strategy_return=total_strategy_return,
            excess_return=excess_return,
            max_drawdown=max_drawdown,
            signal_count=signal_count,
            total_days=len(points),
            effectiveness_window=effectiveness_window,
            avg_effectiveness=eff_stats["avg"],
            buy_effectiveness=eff_stats["buy"],
            sell_effectiveness=eff_stats["sell"],
            effectiveness_rate=eff_stats["rate"],
            baseline_buy_hold=baseline_buy_hold,
            baseline_static_half=round(baseline_static_half, 4),
            excess_vs_static_half=excess_vs_static_half,
            signal_count_non_hold=signal_count_non_hold,
            signal_coverage_ratio=coverage_ratio,
            low_sample=low_sample,
            caveat=caveat,
            coverage_start_date=coverage_start,
            coverage_days=coverage_days,
            pool_size_at=pool_size_at,
            carry_position=policy["carry_position"],
            points=points,
        )

    # ── 内部方法 ──────────────────────────────────────────────────────

    @staticmethod
    def _sample_gate(
        signal_count_non_hold: int,
        coverage_ratio: float,
        min_signals: int,
        min_coverage_pct: float,
    ) -> tuple[bool, Optional[str]]:
        """样本量下限：不足则只出警告，不出策略结论（Q6-C）

        两项阈值任一配 0 即该项不拦（回滚路径）。当前库里多数基金只有个位数
        信号日，命中此项是预期行为，不是错误。
        """
        reasons: list[str] = []
        if min_signals > 0 and signal_count_non_hold < min_signals:
            reasons.append(f"非 hold 信号仅 {signal_count_non_hold} 个（下限 {min_signals}）")
        if min_coverage_pct > 0 and coverage_ratio * 100.0 < min_coverage_pct:
            reasons.append(
                f"信号仅覆盖 {coverage_ratio * 100:.0f}% 的交易日（下限 {min_coverage_pct:.0f}%）"
            )
        if not reasons:
            return False, None
        return True, "样本不足：" + "；".join(reasons) + "。曲线仅作过程展示，不构成策略结论"

    async def _get_fund(self, fund_id: int) -> Optional[Fund]:
        """查询基金"""
        result = await self.db.execute(select(Fund).where(Fund.id == fund_id))
        return result.scalars().first()

    async def _get_signal_map(self, fund_id: int) -> dict[str, dict]:
        """查询该基金的全部历史信号，返回 {date_str: {direction, strength, score}}"""
        stmt = (
            select(AnalysisResult)
            .where(AnalysisResult.fund_id == fund_id)
            .order_by(AnalysisResult.analysis_date)
        )
        result = await self.db.execute(stmt)
        rows = result.scalars().all()

        signal_map: dict[str, dict] = {}
        for r in rows:
            date_str = r.analysis_date.isoformat() if isinstance(r.analysis_date, date) else str(r.analysis_date)
            signal_map[date_str] = {
                "direction": r.signal_direction,
                "strength": r.signal_strength,
                "score": r.weighted_score,
            }
        return signal_map

    async def _pool_size_at(self, on_date: Optional[str]) -> Optional[int]:
        """该回测区间起点时已入池的基金数（含此后移出的：池子规模按入池时点算，不看当前状态）"""
        if not on_date:
            return None
        try:
            day_end = datetime.combine(date.fromisoformat(on_date[:10]), time.max)
        except ValueError:
            logger.warning(f"无法解析覆盖起点日期: {on_date!r}")
            return None
        return (await self.db.execute(
            select(func.count()).select_from(Fund).where(Fund.created_at <= day_end)
        )).scalar()

    @staticmethod
    def _align_signals_to_trading_days(
        dates: list[str], signal_map: dict[str, dict]
    ) -> dict[str, dict]:
        """非交易日信号前向对齐到下一个交易日

        分析可能在周末/节假日手动运行，信号记录在自然日（如周六 05-23），
        而净值序列只含交易日 → 直接按日期匹配会丢信号。
        语义：周末生成的信号，其实际作用时点是下一个交易日。
        同一交易日命中多个信号（周末连跑多日分析）时保留最新一条。
        晚于净值序列末尾的信号（尚无后续交易日）丢弃。
        """
        import bisect

        # 归一化交易日键（日期可能是 "2026-05-13" 或 "2026-05-13 00:00:00"）
        norm_dates = [d[:10] for d in dates]
        aligned: dict[str, dict] = {}
        for date_key in sorted(signal_map.keys()):
            idx = bisect.bisect_left(norm_dates, date_key)
            if idx >= len(norm_dates):
                continue  # 晚于序列末尾，无交易日可作用
            if idx == 0 and date_key < norm_dates[0]:
                # 早于净值窗口起点：映射到首日会触发一次无来由调仓并计费，
                # 所以只接受"顺延"而非"补历史"，上限见 _MAX_PRE_WINDOW_SIGNAL_DAYS。
                # 不用交易日历精确判"整段闭市"：调休补班的周六日股市实际不开市，
                # 日历近似的判错方向更糟，且会让回测依赖日历数据。
                try:
                    gap = (date.fromisoformat(norm_dates[0][:10])
                           - date.fromisoformat(date_key[:10])).days
                except ValueError:
                    continue
                if gap > _MAX_PRE_WINDOW_SIGNAL_DAYS:
                    continue
            aligned[norm_dates[idx]] = signal_map[date_key]  # 后写覆盖 → 保留最新
        return aligned

    def _build_points(
        self,
        dates: list[str],
        navs: list[float],
        signal_map: dict[str, dict],
        effectiveness_window: int = 5,
        fee_pct: Optional[float] = None,
        carry_position: bool = DEFAULT_CARRY_POSITION,
    ) -> list[BacktestPoint]:
        """构建回测数据点序列

        策略逻辑（next-bar execution，避免前视偏差）：
        - 当日信号在收盘后生成（默认 15:10 后），记录在当日点；
        - 但仓位由「前一日信号」决定，作用于当日涨跌；
        - 即 T 日信号 → T+1 日仓位 → 作用于 T+1 日收益。

        仓位状态机（Q6-A）：
        - carry_position=True（默认）：无信号日**延续**最近一次信号决定的仓位，
          直到出现新信号（含显式 hold → 回到 50%）。分析漏跑一天不再等于
          被动砍回半仓并白扣一次换仓费。
        - carry_position=False（旧行为/回滚档）：无信号日回落默认 50% 仓位。
        - 尚无任何信号时（序列开头）从 50% 起步。

        收益累计：几何复利（非加法），strategy_nav 维护策略净值。
        成本：仓位变动日扣减 |Δ仓位| × 调仓费率（默认 DEFAULT_ROUND_TRIP_FEE_PCT，
        实际由 system_config.backtest_fee_pct 决定，0 表示不计成本）。
        基线：同一循环内并行累计 `baseline_static_half`（恒 50%、零调仓、不计费）；
        满仓买入持有基线就是 nav_return 本身。未平仓现金部分按 0% 计（不计息）。
        """
        # 非交易日信号（周末/节假日运行分析）前向对齐到下一交易日
        aligned_signal_map = self._align_signals_to_trading_days(dates, signal_map)

        points: list[BacktestPoint] = []
        # 默认仓位（尚无任何信号时的起点；旧口径也是无信号日的回落目标）
        default_position = 0.5
        # 策略净值（几何复利），初始 1.0
        strategy_nav = 1.0
        static_half_nav = 1.0
        initial_nav = navs[0] if navs else 1.0

        # 生效费率（None → 默认值，保持单测与无库场景可用）
        fee = DEFAULT_ROUND_TRIP_FEE_PCT if fee_pct is None else fee_pct

        # 前一日信号决定的仓位（next-bar execution）
        prev_position = default_position
        # 上一日实际生效仓位（调仓成本基准；首日建仓不计费）
        prev_applied_position = default_position

        for i in range(len(dates)):
            d = dates[i]
            nav = navs[i]

            # 日收益率（%）
            if i == 0:
                daily_return = 0.0
            else:
                prev_nav = navs[i - 1]
                daily_return = (nav / prev_nav - 1) * 100 if prev_nav > 0 else 0.0

            # 累计净值收益（几何复利：用 nav 比值直接算区间收益，非加法累计）
            nav_cum_return = round((nav / initial_nav - 1) * 100, 4) if initial_nav > 0 else 0.0
            # 静态半仓基线：恒 50% 敞口、不调仓所以无换手成本
            static_half_nav *= (1 + daily_return * default_position / 100)
            static_half_cum = round((static_half_nav - 1) * 100, 4)

            # 查找当日信号（记录在当日点，但仓位作用于下一日）
            # 日期格式可能是 "2025-06-13 00:00:00" 或 "2025-06-13"
            date_key = d[:10]  # 取前 10 字符
            sig = aligned_signal_map.get(date_key)

            if sig:
                direction = sig["direction"]
                strength = sig["strength"]
                score = sig["score"]
                # 未知强度按"不动"处理：延续当前仓位（旧口径回落半仓）
                current_position = POSITION_MAP.get(
                    strength, prev_position if carry_position else default_position
                )
            else:
                direction = None
                strength = None
                score = None
                current_position = prev_position if carry_position else default_position

            # 策略收益 = 当日涨跌 × 仓位（仓位由前一日信号决定，避免前视偏差）
            # 现金部分 (1 - position) 不计息
            position = prev_position
            strategy_daily = daily_return * position
            # 调仓成本：|仓位变动| × 单次综合费率，在变化当日扣减
            turnover_cost = abs(position - prev_applied_position) * fee
            prev_applied_position = position
            # 几何复利：(1+r1)(1+r2)...-1
            strategy_nav *= (1 + (strategy_daily - turnover_cost) / 100)
            strategy_cum_return = round((strategy_nav - 1) * 100, 4)

            # 当日信号更新为下一日的 prev_position（next-bar execution）
            prev_position = current_position

            points.append(BacktestPoint(
                date=date_key,
                nav=round(nav, 4),
                nav_return=nav_cum_return,
                strategy_return=strategy_cum_return,
                signal_direction=direction,
                signal_strength=strength,
                weighted_score=score,
                position_applied=round(position, 4),
                baseline_static_half=static_half_cum,
            ))

        # 后处理：计算信号有效性评分
        self._score_effectiveness(points, navs, effectiveness_window)
        return points

    @staticmethod
    def _score_effectiveness(
        points: list[BacktestPoint],
        navs: list[float],
        window: int,
    ) -> None:
        """为每个有 buy/sell 信号的点计算 signal_effectiveness（原地修改）

        买入信号：后 window 天上涨天数越多分越高
        卖出信号：后 window 天下跌天数越多分越高
        """
        for i, p in enumerate(points):
            if p.signal_direction not in ("buy", "sell"):
                continue

            end = min(i + window, len(points) - 1)
            available = end - i
            if available <= 0:
                continue

            up_days = 0
            down_days = 0
            for j in range(i + 1, end + 1):
                if navs[j - 1] > 0:
                    day_return = navs[j] / navs[j - 1] - 1
                else:
                    day_return = 0.0
                if day_return > 0:
                    up_days += 1
                elif day_return < 0:
                    down_days += 1

            if p.signal_direction == "buy":
                p.signal_effectiveness = round(up_days / available * 100, 1)
            else:  # sell
                p.signal_effectiveness = round(down_days / available * 100, 1)

    @staticmethod
    def _calc_effectiveness_stats(points: list[BacktestPoint]) -> dict:
        """计算整体信号有效性统计"""
        buy_scores = [
            p.signal_effectiveness for p in points
            if p.signal_direction == "buy" and p.signal_effectiveness is not None
        ]
        sell_scores = [
            p.signal_effectiveness for p in points
            if p.signal_direction == "sell" and p.signal_effectiveness is not None
        ]
        all_scores = buy_scores + sell_scores

        if not all_scores:
            return {"avg": None, "buy": None, "sell": None, "rate": None}

        return {
            "avg": round(sum(all_scores) / len(all_scores), 1),
            "buy": round(sum(buy_scores) / len(buy_scores), 1) if buy_scores else None,
            "sell": round(sum(sell_scores) / len(sell_scores), 1) if sell_scores else None,
            "rate": round(sum(1 for s in all_scores if s >= 50) / len(all_scores) * 100, 1),
        }

    @staticmethod
    def _calc_max_drawdown(points: list[BacktestPoint]) -> float:
        """计算策略净值最大回撤 (%)，负值

        在净值指数空间 (1 + 累计收益/100) 做几何回撤 (peak-index)/peak，
        而非"累计收益百分点"的差值——后者会把高涨幅后回落的回撤系统性放大
        （+100%→+80% 真实回撤 10%，百分点口径误报 20）。
        """
        if not points:
            return 0.0

        peak = 1.0
        worst = 0.0
        for p in points:
            index = 1.0 + p.strategy_return / 100.0
            if index > peak:
                peak = index
            if peak > 0:
                dd = (index / peak - 1) * 100.0
                if dd < worst:
                    worst = dd

        return round(worst, 4)
