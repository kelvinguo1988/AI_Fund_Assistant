"""信号回测服务 — 将历史信号与净值对齐，模拟仓位策略累计收益"""

import logging
from datetime import date
from typing import Optional

import numpy as np
from sqlalchemy import select
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


class BacktestService:
    """信号回测服务"""

    # 净值短期缓存：{(fund_code, period): (timestamp, fund_data)}
    # 用户反复回测同一基金时避免重复拉取全量净值（10 分钟内命中）
    _nav_cache: dict[tuple[str, int], tuple[float, object]] = {}
    _NAV_CACHE_TTL = 600.0  # 10 分钟

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
        fund_data = await adapter.get_fund_data(fund_code, period=period)
        if fund_data is not None:
            BacktestService._nav_cache[key] = (now, fund_data)
        return fund_data

    async def run_backtest(
        self,
        fund_id: int,
        period: int = 365,
        effectiveness_window: int = 5,
    ) -> Optional[BacktestSummary]:
        """运行信号回测

        Args:
            fund_id: 基金 ID
            period: 回测天数（净值序列长度）
            effectiveness_window: 信号有效性评估窗口（交易日数）

        Returns:
            BacktestSummary 或 None（基金不存在 / 无净值数据）
        """
        # 1. 查询基金信息
        fund = await self._get_fund(fund_id)
        if fund is None:
            return None

        # 2. 获取净值序列（10 分钟缓存）
        fund_data = await self._get_nav_series(fund.code, period)

        if not fund_data.close_history or not fund_data.date_history:
            logger.warning(f"基金 {fund.code} 无净值数据")
            return None

        dates = fund_data.date_history
        navs = fund_data.close_history

        if len(dates) != len(navs):
            logger.warning(f"基金 {fund.code} 日期/净值序列长度不一致")
            return None

        # 3. 获取该基金的历史信号
        signal_map = await self._get_signal_map(fund_id)

        # 4. 按日期对齐 + 计算累计收益
        points = self._build_points(dates, navs, signal_map, effectiveness_window)

        # 5. 计算统计指标
        total_nav_return = points[-1].nav_return if points else 0.0
        total_strategy_return = points[-1].strategy_return if points else 0.0
        excess_return = round(total_strategy_return - total_nav_return, 4)
        max_drawdown = self._calc_max_drawdown(points)
        signal_count = sum(1 for p in points if p.signal_direction is not None)

        # 6. 信号有效性统计
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
            points=points,
        )

    # ── 内部方法 ──────────────────────────────────────────────────────

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
            aligned[norm_dates[idx]] = signal_map[date_key]  # 后写覆盖 → 保留最新
        return aligned

    def _build_points(
        self,
        dates: list[str],
        navs: list[float],
        signal_map: dict[str, dict],
        effectiveness_window: int = 5,
    ) -> list[BacktestPoint]:
        """构建回测数据点序列

        策略逻辑（next-bar execution，避免前视偏差）：
        - 当日信号在收盘后生成（默认 15:10 后），记录在当日点；
        - 但仓位由「前一日信号」决定，作用于当日涨跌；
        - 即 T 日信号 → T+1 日仓位 → 作用于 T+1 日收益。
        无信号日默认 hold（50% 仓位）。

        收益累计：几何复利（非加法），strategy_nav 维护策略净值。
        """
        # 非交易日信号（周末/节假日运行分析）前向对齐到下一交易日
        aligned_signal_map = self._align_signals_to_trading_days(dates, signal_map)

        points: list[BacktestPoint] = []
        # 默认仓位（无信号时）
        default_position = 0.5
        # 策略净值（几何复利），初始 1.0
        strategy_nav = 1.0
        initial_nav = navs[0] if navs else 1.0

        # 前一日信号决定的仓位（next-bar execution）
        prev_position = default_position

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

            # 查找当日信号（记录在当日点，但仓位作用于下一日）
            # 日期格式可能是 "2025-06-13 00:00:00" 或 "2025-06-13"
            date_key = d[:10]  # 取前 10 字符
            sig = aligned_signal_map.get(date_key)

            if sig:
                direction = sig["direction"]
                strength = sig["strength"]
                score = sig["score"]
                current_position = POSITION_MAP.get(strength, default_position)
            else:
                direction = None
                strength = None
                score = None
                current_position = default_position

            # 策略收益 = 当日涨跌 × 仓位（仓位由前一日信号决定，避免前视偏差）
            position = prev_position
            strategy_daily = daily_return * position
            # 几何复利：(1+r1)(1+r2)...-1
            strategy_nav *= (1 + strategy_daily / 100)
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
        """计算策略累计收益的最大回撤 (%)"""
        if not points:
            return 0.0

        returns = [p.strategy_return for p in points]
        peak = returns[0]
        max_dd = 0.0

        for r in returns:
            if r > peak:
                peak = r
            dd = peak - r
            if dd > max_dd:
                max_dd = dd

        return round(max_dd, 4)
