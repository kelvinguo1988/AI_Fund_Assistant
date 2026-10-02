"""Q11 收益口径统一回归测试 — 净值复权 / 基准含息 / 口径配置与三行口径头

覆盖三条不变量：
1. 场外净值在复盘/PK/回填链路上是**分红复权**序列（除息日不再被记成暴跌），
   `review_nav_adjusted=0` 时回到裸单位净值；
2. 基准序列按可配股息率逐交易日累乘，且**任意子区间的折算只与区间长度有关**
   （前缀因子在比值里约掉）；股息率 0 时基准完全不变；
3. 口径读写走 system_config，脏值不得让口径悄悄变掉；含区间收益的报告都印三行口径头。

红线：全程零真实数据源请求（净值/基准序列一律手工构造或 monkeypatch）。
"""

import os
import sys

import pandas as pd
import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.models.system_config import SystemConfig  # noqa: E402
from backend.services.caliber_service import (  # noqa: E402
    BENCH_DIV_YIELD_CONFIG_KEY,
    BENCH_DIV_YIELD_MAX,
    DEFAULT_BENCH_DIV_YIELD_PCT,
    NAV_ADJUSTED_CONFIG_KEY,
    caliber_head_lines,
    load_caliber,
    save_caliber,
    with_dividend_carry,
)
from backend.services.review_service import (  # noqa: E402
    ReviewService,
    otc_nav_pairs,
)

# 一次分红除息：DWJZ 从 1.30 掉到 1.25，但当日 日增长率 是 +0.74%
# （真实形态见第一批 #56 的 004815：真实 +0.74% 被裸净值记成 -3.80%）
EX_DIVIDEND = [
    ("2026-01-12", 1.30, None),
    ("2026-01-13", 1.25, 0.74),
    ("2026-01-14", 1.26, 0.80),
]


def _df(rows, with_growth: bool = True):
    """[(日期, 单位净值, 日增长率)] → 净值 DataFrame（两种备源列集的口径）"""
    data = {"净值日期": [d for d, _, _ in rows], "单位净值": [v for _, v, _ in rows]}
    if with_growth:
        data["日增长率"] = [g for _, _, g in rows]
    return pd.DataFrame(data)


# ── A. 场外净值复权（复盘/PK/回填共用同一函数）────────────────────────

class TestOtcNavPairs:
    def test_dividend_day_is_no_longer_a_crash(self):
        pairs = otc_nav_pairs(_df(EX_DIVIDEND))
        got = dict(pairs)
        assert got["2026-01-14"] == pytest.approx(1.26)          # 前复权：末值=最新单位净值
        assert got["2026-01-13"] / got["2026-01-12"] == pytest.approx(1.0074)
        # 裸净值区间 -3.08%，复权后必须是正收益（分红已回算）
        assert pairs[-1][1] / pairs[0][1] - 1 > 0

    def test_rollback_flag_keeps_raw_unit_nav(self):
        assert [v for _, v in otc_nav_pairs(_df(EX_DIVIDEND), adjusted=False)] == [1.30, 1.25, 1.26]

    def test_source_without_growth_column_is_left_alone(self):
        """只有 DWJZ 的备源不做任何调整（复权后反而更差是不可接受的）"""
        assert [v for _, v in otc_nav_pairs(_df(EX_DIVIDEND, with_growth=False))] == [1.30, 1.25, 1.26]

    def test_suspended_empty_nav_dropped(self):
        rows = [("2026-01-12", 1.30, None), ("2026-01-13", None, None),
                ("2026-01-14", 1.26, 0.80), ("2026-01-15", float("nan"), 0.1)]
        assert [d for d, _ in otc_nav_pairs(_df(rows))] == ["2026-01-12", "2026-01-14"]

    def test_timestamp_dates_normalised(self):
        df = _df(EX_DIVIDEND)
        df["净值日期"] = pd.to_datetime(df["净值日期"])
        assert [d for d, _ in otc_nav_pairs(df)] == ["2026-01-12", "2026-01-13", "2026-01-14"]


# ── B②. 基准含息（价格指数 + 按区间交易日折算的股息）──────────────────

class TestWithDividendCarry:
    def test_zero_yield_is_identity(self):
        series = [("2026-01-01", 100.0), ("2026-01-02", 101.0)]
        assert with_dividend_carry(series, 0.0) == series
        assert with_dividend_carry([], 2.7) == []

    def test_flat_price_becomes_dividend_return(self):
        series = [(f"D{i:03d}", 4000.0) for i in range(20)]
        out = with_dividend_carry(series, 2.7)
        assert out[0][1] == 4000.0                       # 起点不缩放
        # 19 个交易日步长 × 单日股息，即"按区间天数折算"（复利与线性的差在 0.001pp 内）
        assert (out[-1][1] / out[0][1] - 1) * 100 == pytest.approx(19 * 2.7 / 252, abs=1e-3)

    def test_prefix_cancels_so_only_window_length_matters(self):
        series = [(f"D{i:03d}", 4000.0) for i in range(100)]
        out = with_dividend_carry(series, 2.7)
        head = out[30][1] / out[0][1] - 1
        tail = out[99][1] / out[69][1] - 1
        assert head == pytest.approx(tail, rel=1e-9)

    def test_input_series_not_mutated(self):
        """基准序列来自 adapter 的类级缓存，就地改写会污染同进程其他请求"""
        series = [("2026-01-01", 4000.0), ("2026-01-02", 4000.0)]
        with_dividend_carry(series, 2.7)
        assert series == [("2026-01-01", 4000.0), ("2026-01-02", 4000.0)]


# ── C. 口径配置（两条回滚键）与三行口径头 ─────────────────────────────

class TestCaliberConfig:
    async def test_defaults_when_unset(self, db_session):
        assert await load_caliber(db_session) == {
            "nav_adjusted": True, "bench_div_yield_pct": DEFAULT_BENCH_DIV_YIELD_PCT,
        }

    async def test_round_trip_and_keys_are_the_rollback_path(self, db_session):
        saved = await save_caliber(db_session, nav_adjusted=False, bench_div_yield_pct=0.0)
        assert saved == {"nav_adjusted": False, "bench_div_yield_pct": 0.0}
        assert await load_caliber(db_session) == saved
        # 回滚键落在 system_config，配置页/SQL 都能看到说明（否则半年后没人知道怎么回退）
        rows = (await db_session.execute(select(SystemConfig))).scalars().all()
        assert {r.config_key for r in rows} == {
            NAV_ADJUSTED_CONFIG_KEY, BENCH_DIV_YIELD_CONFIG_KEY}
        assert all(r.description for r in rows)

    async def test_partial_update_keeps_other_item(self, db_session):
        await save_caliber(db_session, nav_adjusted=False)
        policy = await load_caliber(db_session)
        assert policy["nav_adjusted"] is False
        assert policy["bench_div_yield_pct"] == DEFAULT_BENCH_DIV_YIELD_PCT

    async def test_dirty_values_fall_back_to_default(self, db_session):
        from backend.utils.timezone import now_beijing

        db_session.add_all([
            SystemConfig(config_key=NAV_ADJUSTED_CONFIG_KEY, config_value="ture",
                         updated_at=now_beijing()),
            SystemConfig(config_key=BENCH_DIV_YIELD_CONFIG_KEY, config_value="abc",
                         updated_at=now_beijing()),
        ])
        await db_session.commit()
        policy = await load_caliber(db_session)
        assert policy["nav_adjusted"] is True          # 认不出来的值不改动默认口径
        assert policy["bench_div_yield_pct"] == DEFAULT_BENCH_DIV_YIELD_PCT

    async def test_div_yield_is_clamped(self, db_session):
        policy = await save_caliber(db_session, bench_div_yield_pct=9999.0)
        assert policy["bench_div_yield_pct"] == BENCH_DIV_YIELD_MAX

    def test_head_lines_state_both_calibers(self):
        lines = caliber_head_lines(
            {"nav_adjusted": True, "bench_div_yield_pct": 2.7},
            extra="组合按基金池等权买入持有",
            cash_line="满仓假设，不涉及现金利息",
        )
        assert len(lines) == 3
        assert lines[0].startswith("> 净值口径：场外基金分红复权")
        assert lines[1].startswith("> 基准口径：沪深300 价格指数 + 股息 2.7%/年")
        assert lines[2].startswith("> 计息口径：满仓假设")

    def test_head_lines_follow_rollback(self):
        lines = caliber_head_lines(
            {"nav_adjusted": False, "bench_div_yield_pct": 0.0}, cash_line="不计息")
        assert "单位净值（未复权" in lines[0]
        assert "不含股息" in lines[1]


# ── D. 复盘基准真的走了含息序列 ───────────────────────────────────────

class _BenchAdapter:
    def __init__(self, series):
        self._series = series

    async def get_benchmark_series(self, symbol: str = "sh000300", period: int = 600):
        return list(self._series)


@pytest.fixture
def bench_source(monkeypatch):
    """替换 review_service 内部构造的 AKShareAdapter：基准序列由用例给定"""
    import backend.data_sources.akshare_adapter as ak_mod

    def install(series):
        monkeypatch.setattr(ak_mod, "AKShareAdapter",
                            lambda *a, **kw: _BenchAdapter(series))
        return series

    return install


RISING_3_POINTS = [("2026-08-03", 4000.0), ("2026-08-04", 4040.0), ("2026-08-05", 4080.0)]


class TestBenchmarkGrowth:
    async def test_price_only_window(self, bench_source):
        bench_source(RISING_3_POINTS)
        svc = ReviewService(None)
        assert await svc._benchmark_growth("2026-08-03", "2026-08-05", 0.0) == 2.0

    async def test_dividend_added_by_days(self, bench_source):
        """两个交易日步长：2.7%/年 → 约 +0.021pp 叠在 2% 价格涨跌上"""
        bench_source(RISING_3_POINTS)
        svc = ReviewService(None)
        with_div = await svc._benchmark_growth("2026-08-03", "2026-08-05", 2.7)
        assert with_div == pytest.approx(2.0 + 2 * 2.7 / 252, abs=0.02)

    async def test_nearest_before_alignment_unchanged(self, bench_source):
        bench_source([("2026-08-03", 4000.0), ("2026-08-04", 4040.0)])
        svc = ReviewService(None)
        # 结束日不是净值日 → 仍取 08-04 那一点（区间只剩 1 个步长）
        assert await svc._benchmark_growth("2026-08-03", "2026-08-10", 0.0) == 1.0

    async def test_empty_series_returns_none(self, bench_source):
        bench_source([])
        assert await ReviewService(None)._benchmark_growth(
            "2026-08-03", "2026-08-05", 2.7) is None


# ── E. 对比报告头（与复盘共用同一份口径）──────────────────────────────

class TestCompareHead:
    def test_summary_md_carries_three_caliber_lines(self):
        from backend.services.caliber_service import caliber_head_lines
        from backend.schemas.analysis import CompareReport
        from backend.services.fund_compare_service import FundCompareService

        policy = {"nav_adjusted": True, "bench_div_yield_pct": 2.7}
        r = CompareReport(
            baseline="沪深300", items=[],
            caliber={**policy, "lines": caliber_head_lines(
                policy, cash_line="夏普/Alpha 扣减无风险利率 2.0%/年，净值不另计利息")},
        )
        md = FundCompareService._summary_md(r)
        assert "净值口径：场外基金分红复权" in md
        assert "股息 2.7%/年" in md
        assert "无风险利率 2.0%/年" in md
