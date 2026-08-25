"""回测数学回归测试 — next-bar 执行 + 几何复利"""

import pytest

from backend.services.backtest_service import BacktestService


@pytest.fixture
def service() -> BacktestService:
    return BacktestService(db=None)


def test_no_signal_default_half_position(service):
    """无信号日全程 50% 仓位：策略收益 ≈ 净值收益的一半（几何复利）"""
    navs = [1.0, 1.1, 1.21]  # 每日 +10%
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    points = service._build_points(dates, navs, {}, effectiveness_window=5)

    # 净值累计 +21%；策略每日 +5% 复利 → 1.05^2-1 = 10.25%
    assert points[-1].nav_return == pytest.approx(21.0)
    assert points[-1].strategy_return == pytest.approx((1.05 ** 2 - 1) * 100, abs=1e-3)


def test_signal_applies_next_bar_not_same_day(service):
    """T 日信号必须作用于 T+1 日收益（防前视偏差）"""
    # 第 2 日出现 heavy_buy(0.9 仓位)，但当日收益仍由前一日默认仓位 0.5 决定
    navs = [1.0, 1.0, 1.1]
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    signal_map = {"2026-01-02": {"direction": "buy", "strength": "heavy_buy", "score": 5.0}}

    points = service._build_points(dates, navs, signal_map, effectiveness_window=5)

    # T+1 (01-03) 收益 +10% × 0.9 = +9%
    assert points[2].strategy_return == pytest.approx(9.0, abs=1e-3)
    # 信号记录在 01-02 点上
    assert points[1].signal_direction == "buy"
    assert points[1].signal_strength == "heavy_buy"


def test_geometric_compounding_not_additive(service):
    """验证复利而非加法：两次 +10% 应得 +21% 而非 +20%"""
    navs = [1.0, 1.1, 1.21]
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    signal_map = {
        "2026-01-01": {"direction": "buy", "strength": "heavy_buy", "score": 5.0},
    }
    points = service._build_points(dates, navs, signal_map, effectiveness_window=5)

    # 01-01 信号 → 01-02 用 0.9 仓位(+9%)；01-03 无信号回落默认 0.5(+5%)，复利串联
    expected = (1.09 * 1.05 - 1) * 100
    assert points[-1].strategy_return == pytest.approx(expected, abs=1e-3)


def test_sell_signal_reduces_position(service):
    navs = [1.0, 1.0, 0.9]
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    signal_map = {"2026-01-02": {"direction": "sell", "strength": "heavy_sell", "score": -5.0}}
    points = service._build_points(dates, navs, signal_map, effectiveness_window=5)

    # -10% × 0.1 = -1%
    assert points[2].strategy_return == pytest.approx(-1.0, abs=1e-3)


def test_datetime_string_dates(service):
    """日期可能是 '2026-01-01 00:00:00' 格式，仍应对齐到信号"""
    navs = [1.0, 1.0, 1.1]
    dates = ["2026-01-01 00:00:00", "2026-01-02 00:00:00", "2026-01-03 00:00:00"]
    signal_map = {"2026-01-02": {"direction": "buy", "strength": "moderate_buy", "score": 4.0}}
    points = service._build_points(dates, navs, signal_map, effectiveness_window=5)

    assert points[1].signal_direction == "buy"
    # +10% × 0.7 = +7%
    assert points[2].strategy_return == pytest.approx(7.0, abs=1e-3)


def test_max_drawdown(service):
    from backend.schemas.backtest import BacktestPoint

    points = [
        BacktestPoint(date="d", nav=1, nav_return=0, strategy_return=10.0),
        BacktestPoint(date="d", nav=1, nav_return=0, strategy_return=5.0),
        BacktestPoint(date="d", nav=1, nav_return=0, strategy_return=8.0),
        BacktestPoint(date="d", nav=1, nav_return=0, strategy_return=-2.0),
    ]
    assert BacktestService._calc_max_drawdown(points) == pytest.approx(12.0)
