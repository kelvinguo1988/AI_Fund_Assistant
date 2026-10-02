"""回测度量口径测试（2026-10-02 第二批 Q6）

覆盖四件事：
- A 仓位状态机：无信号日延续最近仓位（可回滚到回落半仓的旧口径）
- B 三条基线：满仓买入持有 / 静态半仓 / 两个超额口径的算法关系
- C 样本下限：非 hold 信号数与覆盖度不足时只出 caveat
- E 选择偏差标注：覆盖起点、跨度与入池时点池子规模

全部纯内存/纯计算，零网络请求。
"""

from datetime import date, datetime, timedelta

import pytest
from types import SimpleNamespace

from backend.services.backtest_service import (
    CARRY_CONFIG_KEY,
    DEFAULT_CARRY_POSITION,
    DEFAULT_MIN_COVERAGE_PCT,
    DEFAULT_MIN_SIGNALS,
    DEFAULT_ROUND_TRIP_FEE_PCT as FEE,
    MIN_COVERAGE_CONFIG_KEY,
    MIN_SIGNALS_CONFIG_KEY,
    BacktestService,
    load_measurement_policy,
    save_measurement_policy,
)


@pytest.fixture
def service() -> BacktestService:
    return BacktestService(db=None)


def _sig(direction, strength, score=0.0):
    return {"direction": direction, "strength": strength, "score": score}


# ── A. 仓位状态机 ─────────────────────────────────────────────────────

def test_carry_position_by_default(service):
    """默认口径：漏跑一天分析不再被动砍回半仓、也不再白扣一次换仓费"""
    navs = [1.0, 1.0, 1.1, 1.1]
    dates = [f"2026-01-0{i}" for i in range(1, 5)]
    signal_map = {"2026-01-01": _sig("buy", "heavy_buy", 5.0)}

    points = service._build_points(dates, navs, signal_map, effectiveness_window=5)

    # 生效仓位 0.5 → 0.9 → 0.9 → 0.9：只在 01-02 换手扣费一次
    assert [p.position_applied for p in points] == [0.5, 0.9, 0.9, 0.9]
    expected = (1 - 0.4 * FEE / 100) * (1 + 9.0 / 100) - 1
    assert points[-1].strategy_return == pytest.approx(expected * 100, abs=1e-3)


def test_carry_disabled_reverts_to_half_position(service):
    """carry_position=False（回滚档）＝ 旧行为：漏跑一天就回落 50% 并再次计费"""
    navs = [1.0, 1.0, 1.1, 1.1]
    dates = [f"2026-01-0{i}" for i in range(1, 5)]
    signal_map = {"2026-01-01": _sig("buy", "heavy_buy", 5.0)}

    legacy = service._build_points(
        dates, navs, signal_map, effectiveness_window=5, carry_position=False
    )
    carry = service._build_points(dates, navs, signal_map, effectiveness_window=5)

    # 旧口径 01-03 起就掉回半仓，只吃到 5% 涨幅还再扣一次换手费
    assert [p.position_applied for p in legacy] == [0.5, 0.9, 0.5, 0.5]
    expected = (1 - 0.4 * FEE / 100) * (1 + (5 - 0.4 * FEE) / 100) - 1
    assert legacy[-1].strategy_return == pytest.approx(expected * 100, abs=1e-3)
    # 延续口径满仓吃到后半段涨幅 → 明显更高
    assert carry[-1].strategy_return > legacy[-1].strategy_return + 4


def test_explicit_hold_signal_breaks_carry(service):
    """显式 hold 是真实决策（回到中性半仓），必须能覆盖延续仓位"""
    navs = [1.0, 1.0, 1.0, 1.1]
    dates = [f"2026-01-0{i}" for i in range(1, 5)]
    signal_map = {
        "2026-01-01": _sig("buy", "heavy_buy", 5.0),
        "2026-01-02": _sig("hold", "hold", 0.0),
    }

    points = service._build_points(dates, navs, signal_map, effectiveness_window=5)

    # 01-02 的 hold → 01-03 起半仓，且此后延续半仓；01-04 的 +10% 只吃到 5%
    assert [p.position_applied for p in points] == [0.5, 0.9, 0.5, 0.5]
    expected = ((1 - 0.4 * FEE / 100) ** 2) * (1 + 5.0 / 100) - 1
    assert points[-1].strategy_return == pytest.approx(expected * 100, abs=1e-3)


# ── B. 三条基线 ───────────────────────────────────────────────────────

def test_static_half_baseline_matches_flat_half_strategy(service):
    """无信号时策略就是静态半仓：两条线应完全重合（都无换手成本）"""
    navs = [1.0, 1.1, 1.21]
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]

    points = service._build_points(dates, navs, {}, effectiveness_window=5)

    half = (1.05 ** 2 - 1) * 100
    assert points[-1].baseline_static_half == pytest.approx(half, abs=1e-3)
    assert points[-1].strategy_return == pytest.approx(half, abs=1e-3)
    # 满仓买入持有基线 = 净值累计收益本身
    assert points[-1].nav_return == pytest.approx(21.0, abs=1e-3)


def test_static_half_baseline_ignores_signals(service):
    """基线不受信号影响：满仓行情里策略跑不赢净值，但可能跑赢静态半仓"""
    navs = [1.0, 1.1, 1.21]
    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    signal_map = {"2026-01-01": _sig("sell", "heavy_sell", -5.0)}

    points = service._build_points(dates, navs, signal_map, effectiveness_window=5)

    assert points[-1].baseline_static_half == pytest.approx((1.05 ** 2 - 1) * 100, abs=1e-3)
    assert points[-1].strategy_return < points[-1].baseline_static_half


# ── C. 样本下限 ───────────────────────────────────────────────────────

def test_sample_gate_reasons():
    low, caveat = BacktestService._sample_gate(
        signal_count_non_hold=3,
        coverage_ratio=0.1,
        min_signals=DEFAULT_MIN_SIGNALS,
        min_coverage_pct=DEFAULT_MIN_COVERAGE_PCT,
    )
    assert low is True
    assert "3 个" in caveat and "10%" in caveat and "不构成策略结论" in caveat

    ok, none_caveat = BacktestService._sample_gate(
        signal_count_non_hold=20, coverage_ratio=0.8,
        min_signals=DEFAULT_MIN_SIGNALS, min_coverage_pct=DEFAULT_MIN_COVERAGE_PCT,
    )
    assert (ok, none_caveat) == (False, None)


def test_sample_gate_zero_thresholds_never_block():
    """回滚路径：下限配 0 → 样本再少也不拦（回到"永远出结论"的旧行为）"""
    low, caveat = BacktestService._sample_gate(
        signal_count_non_hold=0, coverage_ratio=0.0, min_signals=0, min_coverage_pct=0
    )
    assert (low, caveat) == (False, None)


def test_sample_gate_only_one_reason():
    low, caveat = BacktestService._sample_gate(
        signal_count_non_hold=20, coverage_ratio=0.05,
        min_signals=DEFAULT_MIN_SIGNALS, min_coverage_pct=DEFAULT_MIN_COVERAGE_PCT,
    )
    assert low is True
    assert "非 hold 信号" not in caveat and "覆盖" in caveat


# ── 口径配置读写（回滚键）─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_measurement_policy_defaults(db_session):
    policy = await load_measurement_policy(db_session)
    assert policy == {
        "carry_position": DEFAULT_CARRY_POSITION,
        "min_signals": DEFAULT_MIN_SIGNALS,
        "min_coverage_pct": DEFAULT_MIN_COVERAGE_PCT,
    }


@pytest.mark.asyncio
async def test_measurement_policy_roundtrip_and_clamp(db_session):
    from backend.models.system_config import SystemConfig
    from sqlalchemy import select

    policy = await save_measurement_policy(
        db_session, carry_position=False, min_signals=15, min_coverage_pct=55.0
    )
    assert policy == {"carry_position": False, "min_signals": 15, "min_coverage_pct": 55.0}

    # 二次保存只动一项，其余保持
    policy = await save_measurement_policy(db_session, min_signals=0)
    assert policy["min_signals"] == 0 and policy["carry_position"] is False

    # 越界值夹住，脏值回落默认不炸
    await save_measurement_policy(db_session, min_coverage_pct=250.0, min_signals=-3)
    assert (await load_measurement_policy(db_session))["min_coverage_pct"] == 100.0

    row = (await db_session.execute(
        select(SystemConfig).where(SystemConfig.config_key == CARRY_CONFIG_KEY)
    )).scalars().first()
    row.config_value = "not-a-number"
    await db_session.commit()
    assert (await load_measurement_policy(db_session))["carry_position"] is True


@pytest.mark.asyncio
async def test_measurement_policy_accepts_bool_words(db_session):
    """兼容人工写库的 true/false 与 1/0 两种写法"""
    from backend.models.system_config import SystemConfig

    db_session.add_all([
        SystemConfig(config_key=CARRY_CONFIG_KEY, config_value="false"),
        SystemConfig(config_key=MIN_SIGNALS_CONFIG_KEY, config_value="12"),
        SystemConfig(config_key=MIN_COVERAGE_CONFIG_KEY, config_value="20"),
    ])
    await db_session.commit()

    policy = await load_measurement_policy(db_session)
    assert policy["carry_position"] is False
    assert policy["min_signals"] == 12
    assert policy["min_coverage_pct"] == 20.0


# ── E + run_backtest 全链接线 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_backtest_reports_baselines_and_low_sample(db_session, monkeypatch):
    from backend.models.analysis_result import AnalysisResult
    from backend.models.fund import Fund

    start = datetime(2026, 1, 1)
    db_session.add(Fund(id=1, code="018994", name="样本不足基金", status="active",
                        created_at=start))
    # 净值窗口内只有 2 个信号日 / 5 个交易日 → 样本下限必然命中
    db_session.add_all([
        AnalysisResult(fund_id=1, analysis_date=date(2026, 1, 2), weighted_score=5.0,
                       signal_direction="buy", signal_strength="heavy_buy",
                       operation_advice="", factor_scores="{}"),
        AnalysisResult(fund_id=1, analysis_date=date(2026, 1, 4), weighted_score=-5.0,
                       signal_direction="sell", signal_strength="heavy_sell",
                       operation_advice="", factor_scores="{}"),
    ])
    await db_session.commit()

    navs = [1.0, 1.02, 0.98, 1.05, 1.01]
    dates = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    fake = SimpleNamespace(date_history=dates, close_history=navs)

    async def _fake_nav(self, fund_code, period):
        return fake

    monkeypatch.setattr(BacktestService, "_get_nav_series", _fake_nav)

    svc = BacktestService(db_session)
    summary = await svc.run_backtest(fund_id=1, period=365)

    assert summary is not None
    # 基线关系：满仓持有基线就是净值收益；超额口径互为差值
    assert summary.baseline_buy_hold == summary.total_nav_return
    assert summary.excess_return == pytest.approx(
        round(summary.total_strategy_return - summary.baseline_buy_hold, 4))
    assert summary.excess_vs_static_half == pytest.approx(
        round(summary.total_strategy_return - summary.baseline_static_half, 4))
    # 覆盖度：2/5 天有信号，1 个 buy + 1 个 sell
    assert summary.signal_count == 2
    assert summary.signal_count_non_hold == 2
    assert summary.signal_coverage_ratio == pytest.approx(0.4)
    # 样本下限命中 → 只出 caveat
    assert summary.low_sample is True and "样本不足" in summary.caveat
    assert summary.carry_position is DEFAULT_CARRY_POSITION
    # 选择偏差标注：首个信号落在净值窗口首日，跨度 2 个交易日
    assert summary.coverage_start_date == "2026-01-02"
    assert summary.coverage_days == 2
    assert summary.pool_size_at == 1


@pytest.mark.asyncio
async def test_run_backtest_sample_floor_can_be_disabled(db_session, monkeypatch):
    """把下限配 0 → 同样本情况下不再拦（回滚验证）"""
    from backend.models.analysis_result import AnalysisResult
    from backend.models.fund import Fund

    db_session.add(Fund(id=2, code="016874", name="放开下限基金", status="active",
                        created_at=datetime(2026, 1, 1)))
    db_session.add(AnalysisResult(fund_id=2, analysis_date=date(2026, 1, 2),
                                  weighted_score=5.0, signal_direction="buy",
                                  signal_strength="heavy_buy",
                                  operation_advice="", factor_scores="{}"))
    await db_session.commit()

    dates = ["2026-01-02", "2026-01-05", "2026-01-06"]
    fake = SimpleNamespace(date_history=dates, close_history=[1.0, 1.01, 0.99])

    async def _fake_nav(self, fund_code, period):
        return fake

    monkeypatch.setattr(BacktestService, "_get_nav_series", _fake_nav)

    svc = BacktestService(db_session)
    summary = await svc.run_backtest(
        fund_id=2, period=365,
        policy={"carry_position": False, "min_signals": 0, "min_coverage_pct": 0.0},
    )
    assert summary.low_sample is False and summary.caveat is None
    # 旧口径：01-06 无信号回落到 0.5（与延续口径可比较出差异）
    assert [p.position_applied for p in summary.points] == [0.5, 0.9, 0.5]


@pytest.mark.asyncio
async def test_pool_size_at_counts_funds_registered_by_then(db_session):
    """池子规模按入池时点算：起点当天及之前入池的都算，之后入池的不算（含 disabled）"""
    from backend.models.fund import Fund

    base = datetime(2026, 3, 1)
    db_session.add_all([
        Fund(id=11, code="A00011", name="早入池", status="active",
             created_at=base - timedelta(days=10)),
        Fund(id=12, code="A00012", name="当天入池", status="active",
             created_at=base + timedelta(hours=9)),
        Fund(id=13, code="A00013", name="起点后入池", status="active",
             created_at=base + timedelta(days=1)),
        Fund(id=14, code="A00014", name="早已移出", status="disabled",
             created_at=base - timedelta(days=30)),
    ])
    await db_session.commit()

    svc = BacktestService(db_session)
    assert await svc._pool_size_at("2026-03-01") == 3
    assert await svc._pool_size_at(None) is None
    assert await svc._pool_size_at("不是日期") is None
