"""信号回测对齐逻辑测试（2026-08-28 修复）

背景 bug：分析在周末手动运行 → 信号记录在自然日（周六/周日），
净值序列只含交易日 → 按日期直接匹配丢信号 → 前端无标注、
有效性/胜率全空。修复：非交易日信号前向对齐到下一交易日。
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.services.backtest_service import BacktestService


def _svc() -> BacktestService:
    return BacktestService(db=None)


# 交易日序列（周五 + 下周一二三，跳过周末）
TRADING_DAYS = ["2026-05-22", "2026-05-25", "2026-05-26", "2026-05-27", "2026-05-28"]
NAVS = [1.0, 1.01, 1.02, 0.99, 1.03]


class TestAlignSignals:
    def test_weekend_signal_aligns_to_next_trading_day(self):
        signal_map = {
            "2026-05-23": {"direction": "buy", "strength": "moderate_buy", "score": 2.0},  # 周六
            "2026-05-24": {"direction": "buy", "strength": "heavy_buy", "score": 3.5},     # 周日
        }
        aligned = BacktestService._align_signals_to_trading_days(TRADING_DAYS, signal_map)
        # 周六+周日两个信号都对齐到周一，取最新（周日的 heavy_buy）
        assert aligned == {"2026-05-25": {"direction": "buy", "strength": "heavy_buy", "score": 3.5}}

    def test_trading_day_signal_unchanged(self):
        signal_map = {"2026-05-26": {"direction": "sell", "strength": "moderate_sell", "score": -2.0}}
        aligned = BacktestService._align_signals_to_trading_days(TRADING_DAYS, signal_map)
        assert aligned == {"2026-05-26": signal_map["2026-05-26"]}

    def test_signal_before_series_aligns_to_first_day(self):
        aligned = BacktestService._align_signals_to_trading_days(
            TRADING_DAYS, {"2026-05-01": {"direction": "buy", "strength": "hold", "score": 0.5}}
        )
        assert aligned == {"2026-05-22": {"direction": "buy", "strength": "hold", "score": 0.5}}

    def test_signal_after_series_dropped(self):
        aligned = BacktestService._align_signals_to_trading_days(
            TRADING_DAYS, {"2026-06-30": {"direction": "buy", "strength": "hold", "score": 0.5}}
        )
        assert aligned == {}

    def test_datetime_with_time_suffix_dates(self):
        # 日期可能带时间后缀
        days = ["2026-05-25 00:00:00", "2026-05-26 00:00:00"]
        aligned = BacktestService._align_signals_to_trading_days(
            days, {"2026-05-25": {"direction": "buy", "strength": "hold", "score": 1.0}}
        )
        assert aligned == {"2026-05-25": {"direction": "buy", "strength": "hold", "score": 1.0}}


class TestBuildPointsSignals:
    def test_weekend_signal_visible_in_points(self):
        """周末信号经对齐后必须出现在 points 中（修复前直接丢失）"""
        signal_map = {
            "2026-05-23": {"direction": "buy", "strength": "heavy_buy", "score": 3.2},
            "2026-05-24": {"direction": "buy", "strength": "heavy_buy", "score": 3.2},
        }
        points = _svc()._build_points(TRADING_DAYS, NAVS, signal_map, effectiveness_window=2)
        sig_points = [p for p in points if p.signal_direction is not None]
        # 两个周末信号对齐到 05-25 一个点
        assert len(sig_points) == 1
        assert sig_points[0].date == "2026-05-25"
        assert sig_points[0].signal_direction == "buy"
        assert sig_points[0].signal_strength == "heavy_buy"

    def test_effectiveness_computed_for_aligned_signal(self):
        signal_map = {"2026-05-23": {"direction": "buy", "strength": "moderate_buy", "score": 2.0}}
        points = _svc()._build_points(TRADING_DAYS, NAVS, signal_map, effectiveness_window=2)
        buy_points = [p for p in points if p.signal_direction == "buy"]
        assert len(buy_points) == 1
        # 对齐到 05-25（index 1），后 2 天 nav 变化: 1.02↑, 0.99↓ → 1/2 上涨
        assert buy_points[0].signal_effectiveness == pytest.approx(50.0)

    def test_effectiveness_stats_non_empty_with_signals(self):
        signal_map = {"2026-05-24": {"direction": "buy", "strength": "moderate_buy", "score": 2.0}}
        points = _svc()._build_points(TRADING_DAYS, NAVS, signal_map, effectiveness_window=3)
        stats = BacktestService._calc_effectiveness_stats(points)
        assert stats["avg"] is not None
        assert stats["rate"] is not None
        assert stats["buy"] is not None

    def test_no_signals_stats_empty(self):
        points = _svc()._build_points(TRADING_DAYS, NAVS, {}, effectiveness_window=3)
        stats = BacktestService._calc_effectiveness_stats(points)
        assert stats == {"avg": None, "buy": None, "sell": None, "rate": None}
