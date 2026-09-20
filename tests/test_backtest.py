"""回测数学回归测试 — next-bar 执行 + 几何复利 + 调仓成本 + 费率配置"""

import pytest

from backend.services.backtest_service import (
    DEFAULT_ROUND_TRIP_FEE_PCT,
    FEE_CONFIG_KEY,
    BacktestService,
)


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

    # 01-02 仓位仍 0.5（信号次日生效）且无变动 → 不扣费；
    # 01-03 应用 0.9：+10%×0.9=+9%，当日调仓 0.5→0.9 扣 0.24pp → 8.76
    assert points[2].strategy_return == pytest.approx(9.0 - 0.24, abs=1e-3)
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

    # 01-01 信号 → 01-02 应用 0.9 仓位：毛 +9% 但当日调仓 0.5→0.9 扣 0.24pp → +8.76；
    # 01-03 无信号回落 0.5：毛 +5% 扣 |0.9-0.5|×0.6=0.24pp → +4.76，复利串联
    from backend.services.backtest_service import DEFAULT_ROUND_TRIP_FEE_PCT as FEE
    expected = ((1 + (9 - 0.4 * FEE) / 100) * (1 + (5 - 0.4 * FEE) / 100) - 1) * 100
    assert points[-1].strategy_return == pytest.approx(expected, abs=1e-3)
    assert points[1].strategy_return == pytest.approx(9.0 - 0.4 * FEE, abs=1e-3)


def test_sell_signal_reduces_position(service):
    navs = [1.0, 1.0, 0.9]
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    signal_map = {"2026-01-02": {"direction": "sell", "strength": "heavy_sell", "score": -5.0}}
    points = service._build_points(dates, navs, signal_map, effectiveness_window=5)

    # -10% × 0.1 = -1%；01-03 仓位 0.5→0.1 生效扣 |0.4|×0.6=0.24pp → -1.24
    # （01-02 信号仅记录，当日仓位未变不扣费）
    assert points[2].strategy_return == pytest.approx(-1.0 - 0.24, abs=1e-3)


def test_datetime_string_dates(service):
    """日期可能是 '2026-01-01 00:00:00' 格式，仍应对齐到信号"""
    navs = [1.0, 1.0, 1.1]
    dates = ["2026-01-01 00:00:00", "2026-01-02 00:00:00", "2026-01-03 00:00:00"]
    signal_map = {"2026-01-02": {"direction": "buy", "strength": "moderate_buy", "score": 4.0}}
    points = service._build_points(dates, navs, signal_map, effectiveness_window=5)

    assert points[1].signal_direction == "buy"
    # +10% × 0.7 = +7%，扣调仓成本 |0.7-0.5|×0.6 = 0.12pp
    assert points[2].strategy_return == pytest.approx(7.0 - 0.12, abs=1e-3)


def test_turnover_fee_charged_on_position_change(service):
    """调仓成本：净值走平时只暴露费用，每次仓位变动按 |Δ仓位|×ROUND_TRIP_FEE_PCT 扣减"""
    from backend.services.backtest_service import DEFAULT_ROUND_TRIP_FEE_PCT as FEE
    navs = [1.0, 1.0, 1.0, 1.0]  # 净值走平，隔离费用影响
    dates = [f"2026-01-0{i}" for i in range(1, 5)]
    signal_map = {
        "2026-01-01": {"direction": "buy", "strength": "heavy_buy", "score": 5.0},
        "2026-01-02": {"direction": "sell", "strength": "heavy_sell", "score": -5.0},
    }
    points = service._build_points(dates, navs, signal_map, effectiveness_window=5)

    # 生效仓位序列 0.5/0.9/0.1/0.5（01-01 信号在 01-02 生效，依此类推）：
    #   Δ = 0 / 0.4 / 0.8 / 0.4，费用逐日复利扣减
    fee_steps = [0.0, 0.4, 0.8, 0.4]
    nav = 1.0
    for i, delta in enumerate(fee_steps):
        nav *= 1 - delta * FEE / 100
        assert points[i].strategy_return == pytest.approx((nav - 1) * 100, abs=1e-4)


def test_max_drawdown(service):
    from backend.schemas.backtest import BacktestPoint

    # 几何净值口径：peak index 1.10 → 谷底 0.98 → 回撤 -10.909%
    # （旧百分点口径误报 12.0，把高涨幅后的回落系统性放大）
    points = [
        BacktestPoint(date="d", nav=1, nav_return=0, strategy_return=10.0),
        BacktestPoint(date="d", nav=1, nav_return=0, strategy_return=5.0),
        BacktestPoint(date="d", nav=1, nav_return=0, strategy_return=8.0),
        BacktestPoint(date="d", nav=1, nav_return=0, strategy_return=-2.0),
    ]
    dd = BacktestService._calc_max_drawdown(points)
    assert dd == pytest.approx((0.98 / 1.10 - 1) * 100, abs=1e-3)
    assert dd < 0

    # +100% → +80%：真实回撤 10%，百分点口径会误报 20
    points2 = [
        BacktestPoint(date="d", nav=1, nav_return=0, strategy_return=100.0),
        BacktestPoint(date="d", nav=1, nav_return=0, strategy_return=80.0),
    ]
    assert BacktestService._calc_max_drawdown(points2) == pytest.approx(-10.0, abs=1e-6)


# ── 费率可配置（system_config.backtest_fee_pct）──────────────────────

@pytest.mark.asyncio
async def test_fee_config_defaults_when_unset(db_session):
    from backend.services.backtest_service import load_fee_pct

    assert await load_fee_pct(db_session) == DEFAULT_ROUND_TRIP_FEE_PCT


@pytest.mark.asyncio
async def test_fee_config_save_and_reload(db_session):
    from backend.services.backtest_service import load_fee_pct, save_fee_pct

    saved = await save_fee_pct(db_session, 1.25)
    assert saved == 1.25
    assert await load_fee_pct(db_session) == 1.25

    # 零费率合法（回到纯信号口径），二次保存走 update 分支
    assert await save_fee_pct(db_session, 0.0) == 0.0
    assert await load_fee_pct(db_session) == 0.0


@pytest.mark.asyncio
async def test_fee_config_clamps_out_of_range(db_session):
    from sqlalchemy import select
    from backend.models.system_config import SystemConfig
    from backend.services.backtest_service import load_fee_pct, save_fee_pct

    assert await save_fee_pct(db_session, 99.0) == 5.0
    assert await save_fee_pct(db_session, -3.0) == 0.0

    # 脏值（人工写库）回落默认，不炸
    row = (await db_session.execute(
        select(SystemConfig).where(SystemConfig.config_key == FEE_CONFIG_KEY)
    )).scalars().first()
    assert row is not None
    row.config_value = "not-a-number"
    await db_session.commit()
    assert await load_fee_pct(db_session) == DEFAULT_ROUND_TRIP_FEE_PCT


def test_build_points_respects_fee_override(service):
    """fee_pct 显式覆盖生效；None 时用默认常量（无库场景）"""
    navs = [1.0, 1.0, 1.1]
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    signal_map = {"2026-01-02": {"direction": "buy", "strength": "heavy_buy", "score": 5.0}}

    zero = service._build_points(dates, navs, signal_map, 5, fee_pct=0.0)
    assert zero[2].strategy_return == pytest.approx(9.0, abs=1e-6)

    heavy = service._build_points(dates, navs, signal_map, 5, fee_pct=2.0)
    # 01-03 调仓 Δ0.4 × 2.0 = 0.8pp → 9 - 0.8 = 8.2
    assert heavy[2].strategy_return == pytest.approx(8.2, abs=1e-6)

    default = service._build_points(dates, navs, signal_map, 5)
    assert default[2].strategy_return == pytest.approx(
        9.0 - 0.4 * DEFAULT_ROUND_TRIP_FEE_PCT, abs=1e-6
    )
