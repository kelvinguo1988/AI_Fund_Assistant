"""P2 因子诊断引擎测试：纯统计黄金用例 + Q12 IC 口径 + 注入净值源的服务级回算"""

import json
import math
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from backend.ai.factor_audit import (
    MIN_IC_PERIODS_FOR_CONCLUSION,
    TRADING_DAYS_PER_YEAR,
    FactorAuditService,
    benjamini_hochberg,
    daily_ic,
    forward_return,
    group_compare,
    ic_p_value,
    quintile_returns,
    signal_stats,
    spearman,
    t_two_sided_p,
    whipsaw_counts,
)


class TestPureStats:
    def test_forward_return_alignment(self):
        nav = [("2026-09-01", 1.0), ("2026-09-02", 1.1), ("2026-09-03", 1.21)]
        assert forward_return(nav, "2026-09-01", 1) == pytest.approx(0.1)
        assert forward_return(nav, "2026-08-31", 1) is None        # 早于序列
        assert forward_return(nav, "2026-09-02", 5) is None        # 越界
        assert forward_return(nav, "2026-09-01", 2) == pytest.approx(0.21)
        assert forward_return(None, "2026-09-01", 1) is None

    def test_spearman_perfect_and_random(self):
        xs = [1, 2, 3, 4, 5, 6]
        assert spearman(xs, [2, 4, 6, 8, 10, 12]) == pytest.approx(1.0)
        assert spearman(xs, [12, 10, 8, 6, 4, 2]) == pytest.approx(-1.0)
        # 并列取平均名次：[1,1,2] vs [10,10,20] 仍完全正相关
        assert spearman([1, 1, 2], [10, 10, 20]) == pytest.approx(1.0)
        assert spearman([1, 2], [1, 2]) is None  # 样本 <3

    def test_daily_ic_perfect_factor(self):
        """构造每日截面：因子分与 T+1 收益完全同序 → RankIC=1"""
        samples = {}
        for d in ["2026-09-0%d" % i for i in range(1, 6)]:
            samples[d] = {"f_good": [(s / 10.0, s * 0.01) for s in range(1, 8)]}
        st = daily_ic(samples, "f_good", horizon=1)
        assert st.days == 5
        assert st.rank_ic_mean == pytest.approx(1.0)
        assert st.rank_ic_ir is None  # IC 恒定 std=0 → IR 无定义（不放无穷大）

    def test_daily_ic_respects_min_cross_section(self):
        samples = {
            "d1": {"f": [(i * 0.1, i * 0.01) for i in range(1, 5)]},  # 4 样本 < 5 剔除
            "d2": {"f": [(i * 0.1, i * 0.01) for i in range(1, 8)]},
        }
        st = daily_ic(samples, "f", 1)
        assert st.days == 1

    def test_quintile_monotonic(self):
        pairs = [(s / 100.0, s * 0.001) for s in range(200)]
        buckets = quintile_returns(pairs, buckets=5)
        assert len(buckets) == 5
        assert buckets[0]["avg_fwd_return"] < buckets[-1]["avg_fwd_return"]

    def test_signal_stats_buy_sell_direction(self):
        rows = [
            # buy 且跑赢池均 → 赢
            {"direction": "buy", "fwd": 0.05, "pool_avg": 0.01},
            {"direction": "buy", "fwd": -0.02, "pool_avg": 0.0},
            # sell 且标的跑输池均 → 赢（躲过）
            {"direction": "sell", "fwd": -0.03, "pool_avg": 0.0},
            {"direction": "hold", "fwd": 0.1, "pool_avg": 0.0},
        ]
        st = {s["direction"]: s for s in signal_stats(rows)}
        assert st["buy"]["count"] == 2 and st["buy"]["win_rate"] == 0.5
        assert st["sell"]["count"] == 1 and st["sell"]["win_rate"] == 1.0
        assert "hold" not in st

    def test_whipsaw_counts_ignores_hold(self):
        series = {
            "004011": [("d1", "buy"), ("d2", "hold"), ("d3", "sell")],  # buy→sell 记 1 次翻转
            "162719": [("d1", "buy"), ("d2", "buy")],                    # 0
        }
        out = whipsaw_counts(series)
        assert out == [{"code": "004011", "flips": 1, "observations": 3}]

    def test_group_compare(self):
        g = group_compare([0.01, 0.01], [0.02, 0.04])
        assert g["with_avg_pct"] == 1.0 and g["without_avg_pct"] == 3.0
        assert g["delta_pct"] == -2.0


# ═══════════════════════════════════════════════════════════════════
# 服务级：注入确定性净值，全链路回算
# ═══════════════════════════════════════════════════════════════════

async def _fake_nav_factory(returns_by_code):
    """code -> 每日收益；净值序列从今日往回铺 200 个点"""
    today = date.today()

    async def provider(code: str, period: int):
        daily = returns_by_code.get(code)
        if daily is None:
            return None
        navs, nav = [], 1.0
        for i in range(period):
            d = today - timedelta(days=period - 1 - i)
            navs.append((d.isoformat(), nav))
            nav *= 1 + daily
        return navs

    return provider


async def _seed_pool(db, n_funds=8, n_days=30):
    from backend.models.analysis_result import AnalysisResult
    from backend.models.fund import Fund

    codes = [f"0000{i:02d}" for i in range(n_funds)]
    for i, c in enumerate(codes):
        db.add(Fund(code=c, name=f"基金{c}", fund_type="otc", status="active"))
    await db.flush()
    funds = {
        f.code: f.id for f in
        (await db.execute(select(Fund))).scalars().all()
    }
    today = date.today()
    rows = []
    for di in range(n_days):
        d = today - timedelta(days=n_days - 1 - di)
        if d.weekday() >= 5:
            continue
        for i, c in enumerate(codes):
            # 因子分与"基金真实日收益"完全同序 → 理想因子
            score = (i + 1) / n_funds
            rows.append(AnalysisResult(
                fund_id=funds[c], analysis_date=d,
                weighted_score=round(score * 8, 2), signal_direction="buy" if score > 0.5 else "sell",
                signal_strength="hold", operation_advice="", equity_ratio=0.5,
                factor_scores=json.dumps({"f_ideal": {"name": "理想因子", "raw_value": score,
                                                      "score": score, "direction": "positive"}}),
                original_score=round(score * 8, 2),
                quality_warnings='["风格漂移"]' if i == 0 else None,
            ))
    db.add_all(rows)
    await db.commit()
    return codes


class TestFactorAuditService:
    @pytest.mark.asyncio
    async def test_ideal_pool_full_pipeline(self, db_session):
        codes = await _seed_pool(db_session)
        returns = {c: 0.0005 * i for i, c in enumerate(codes)}  # 与评分同序
        nav = await _fake_nav_factory(returns)
        report = await FactorAuditService(db_session, nav_provider=nav).audit(days=40, horizons=(3,))

        assert report.rows_total > 0 and report.funds_with_nav == len(codes)
        ideal = next(f for f in report.factor_ic if f["factor"] == "f_ideal")
        assert ideal["rank_ic_mean"] > 0.9           # 理想因子高 RankIC
        q = report.score_quintiles["3"]
        assert q[-1]["avg_fwd_return"] > q[0]["avg_fwd_return"]  # 五分位单调
        assert report.signal_win_rate                 # buy/sell 胜率均有输出
        buy = next(s for s in report.signal_win_rate if s["direction"] == "buy")
        assert buy["win_rate"] > 0.9
        assert report.quality_group_compare is not None
        assert "因子 RankIC" in report.summary_md()

    @pytest.mark.asyncio
    async def test_no_nav_degrades_with_caveat(self, db_session):
        await _seed_pool(db_session, n_funds=6, n_days=20)

        async def dead(code, period):
            return None

        report = await FactorAuditService(db_session, nav_provider=dead).audit(days=30, horizons=(3,))
        assert report.funds_with_nav == 0
        assert any("净值不可用" in c for c in report.caveats)
        assert report.factor_ic == []               # 无 IC
        assert report.rows_total > 0                # 装载统计仍有效

    @pytest.mark.asyncio
    async def test_empty_window(self, db_session):
        report = await FactorAuditService(db_session, nav_provider=_noop).audit(days=15)
        assert report.rows_total == 0
        assert "无分析记录" in report.caveats[0]


async def _noop(code, period):  # noqa: D401
    return None


# ═══════════════════════════════════════════════════════════════════
# Q12 IC 统计口径：非重叠抽样 / 年化 IR / 双尾 p / BH 多重校正
# 背景：T+h 的前瞻收益在相邻交易日重叠 h-1 天，逐日算 IC 会让序列强自相关，
# IR = mean/std 虚高约 √h 倍 —— 下面的用例就是把"抽样方式"钉死，而不是只看均值。
# ═══════════════════════════════════════════════════════════════════

FACTOR = "f"


def _cross_section(n: int = 6, up: bool = True) -> list[tuple[float, float]]:
    """n 只基金的截面：因子分与 T+h 收益完全同序（up=True → RankIC=+1）"""
    xs = [float(i) for i in range(1, n + 1)]
    ys = xs if up else list(reversed(xs))
    return list(zip(xs, ys))


def _samples(days: list[int], factor: str = FACTOR, n: int = 6) -> dict:
    """{date: {factor: pairs}}；days 里的整数决定正/反相关（奇偶交替）"""
    out = {}
    for i in days:
        d = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
        out[d] = {factor: _cross_section(n, up=(i % 2 == 0))}
    return out


class TestTwoSidedP:
    """双尾 t 检验用正则化不完全贝塔实现，必须对上教科书数值"""

    def test_known_textbook_values(self):
        assert t_two_sided_p(0.0, 8) == pytest.approx(1.0, abs=1e-9)
        # t(8) 的 97.5% 分位 = 2.306 → 双尾 p = 0.05
        assert t_two_sided_p(2.306, 8) == pytest.approx(0.05, abs=1e-3)
        assert t_two_sided_p(-2.306, 8) == pytest.approx(0.05, abs=1e-3)  # 双尾对称
        # df=1 是柯西分布：p = 1 - 2·atan(t)/π
        assert t_two_sided_p(1.0, 1) == pytest.approx(1.0 - 2 * math.atan(1.0) / math.pi, abs=1e-9)
        assert t_two_sided_p(12.706, 1) == pytest.approx(0.05, abs=1e-3)

    def test_undefined_inputs_return_none(self):
        assert t_two_sided_p(1.0, 0) is None
        assert t_two_sided_p(1.0, -3) is None
        assert t_two_sided_p(float("inf"), 8) is None
        assert t_two_sided_p(float("nan"), 8) is None

    def test_monotone_decreasing_in_abs_t(self):
        ps = [t_two_sided_p(t, 12) for t in (0.5, 1.0, 2.0, 4.0)]
        assert ps == sorted(ps, reverse=True)   # |t| 越大 p 越小
        assert all(0.0 <= p <= 1.0 for p in ps)


class TestIcPValue:
    def test_needs_two_periods(self):
        assert ic_p_value([]) is None
        assert ic_p_value([0.2]) is None

    def test_zero_variance_is_not_significant(self):
        """逐日常数 IC → IR 无定义，也绝不能报成「显著」（旧口径下 IR=inf 最容易骗人）"""
        assert ic_p_value([0.3, 0.3, 0.3, 0.3]) is None

    def test_stable_positive_signal_reaches_significance(self):
        series = [0.05 + 0.01 * (i % 3) for i in range(24)]
        p = ic_p_value(series)
        assert p is not None and p < 0.05

    def test_zero_mean_series_is_not_significant(self):
        p = ic_p_value([0.1, -0.1] * 12)
        assert p is not None and p > 0.9


class TestBenjaminiHochberg:
    def test_step_up_values(self):
        got = benjamini_hochberg([0.001, 0.01, 0.04, 0.2, 0.5])
        assert got == [0.005, 0.025, pytest.approx(0.066667), 0.25, 0.5]

    def test_none_kept_out_of_ranking(self):
        assert benjamini_hochberg([None, 0.01, 0.2]) == [None, 0.02, 0.2]
        assert benjamini_hochberg([None, None]) == [None, None]

    def test_monotone_and_capped_at_one(self):
        got = benjamini_hochberg([0.6, 0.61])
        assert got == [0.61, 0.61]          # 单调不降：小的 p 不会大过大的 p
        assert all(q is not None and q <= 1.0 for q in got)

    def test_single_factor_degenerates_to_p(self):
        assert benjamini_hochberg([0.03]) == [0.03]


class TestDailyIcSampling:
    """非重叠抽样的核心断言：抽样方式必须改变 IR，而不是只改个字段名"""

    def test_horizon_2_alternating_pattern(self):
        # 20 个交易日：偶数日完全正相关、奇数日完全反相关
        samples = _samples(list(range(20)))
        st = daily_ic(samples, FACTOR, horizon=2)
        assert st.n_days_valid == 20                    # 重叠口径下本来有 20 个点
        assert st.days == 10                            # 每 2 日取 1 → 独立周期减半
        assert st.rank_ic_mean == pytest.approx(1.0)    # 取到的恰好全是正相关那些
        assert st.sampling == "non_overlapping"
        # 全是 +1 → 方差 0 → IR/p 无定义（旧口径混着 -1 会算出 IR=0 的假"无效"）
        assert st.rank_ic_ir is None
        assert st.rank_ic_p_value is None
        assert st.rank_ic_positive_ratio == pytest.approx(1.0)

    def test_overlapping_rollback_restores_old_numbers(self):
        samples = _samples(list(range(20)))
        st = daily_ic(samples, FACTOR, horizon=2, overlapping=True)
        assert st.days == 20
        assert st.sampling == "overlapping"
        assert st.rank_ic_mean == pytest.approx(0.0)
        assert st.rank_ic_ir is not None and abs(st.rank_ic_ir) < 0.15

    def test_horizon_1_identical_in_both_modes(self):
        samples = _samples(list(range(12)))
        a = daily_ic(samples, FACTOR, horizon=1)
        b = daily_ic(samples, FACTOR, horizon=1, overlapping=True)
        assert a.days == b.days == 12
        assert a.rank_ic_mean == pytest.approx(b.rank_ic_mean) == pytest.approx(0.0)

    def test_insertion_order_does_not_matter(self):
        """抽样按时间等距，不是按 dict 插入顺序"""
        days = list(range(20))
        asc = daily_ic(_samples(days), FACTOR, horizon=2)
        desc = daily_ic(_samples(list(reversed(days))), FACTOR, horizon=2)
        assert asc.days == desc.days == 10
        assert asc.rank_ic_mean == pytest.approx(1.0)
        assert desc.rank_ic_mean == pytest.approx(1.0)

    def test_thin_days_are_skipped_not_sampled(self):
        """截面不足 MIN_CROSS_SECTION 的交易日既不进序列，也不占抽样槽位"""
        from backend.ai.factor_audit import MIN_CROSS_SECTION

        samples = _samples(list(range(10)))
        thin = (date(2026, 1, 3)).isoformat()
        samples[thin] = {FACTOR: _cross_section(n=MIN_CROSS_SECTION - 1)}
        st = daily_ic(samples, FACTOR, horizon=2)
        assert st.n_days_valid == 9
        assert st.days == 5

    def test_ir_annualization_uses_sqrt_of_periods_per_year(self):
        # 让 IC 有方差：截面里混入反相关日，且保证抽样后仍拿到混合序列
        samples = {}
        for i in range(24):
            d = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
            n = 6 if i % 4 < 2 else 7
            samples[d] = {FACTOR: _cross_section(n=n, up=(i % 3 != 0))}
        st = daily_ic(samples, FACTOR, horizon=5)
        assert st.rank_ic_ir is not None
        assert st.rank_ic_ir_annualized == pytest.approx(
            st.rank_ic_ir * math.sqrt(TRADING_DAYS_PER_YEAR / 5), rel=1e-6
        )
        assert st.avg_pairs > 0

    def test_to_dict_carries_every_caliber_field(self):
        d = daily_ic(_samples(list(range(8))), FACTOR, horizon=2).to_dict()
        for key in ("days", "n_days_valid", "sampling", "rank_ic_ir_annualized",
                    "rank_ic_p_value", "rank_ic_q_bh", "significant"):
            assert key in d


class TestAuditExposesQ12:
    @pytest.mark.asyncio
    async def test_service_marks_sampling_and_bh(self, db_session):
        codes = await _seed_pool(db_session)
        nav = await _fake_nav_factory({c: 0.0005 * i for i, c in enumerate(codes)})
        report = await FactorAuditService(db_session, nav_provider=nav).audit(days=40, horizons=(3,))
        assert report.ic_sampling == "non_overlapping"

        ideal = next(f for f in report.factor_ic if f["factor"] == "f_ideal")
        assert ideal["sampling"] == "non_overlapping"
        assert ideal["days"] < ideal["n_days_valid"]        # 确实抽稀过
        # 完美因子 → 方差 0 → 不可检验，q 与 significant 必须是 None 而不是"✓"
        assert ideal["rank_ic_q_bh"] is None
        assert ideal["significant"] is None

        md = report.summary_md()
        assert "非重叠周期" in md and "Benjamini" in md and "q(BH)" in md
        assert "IR年化" in md and "独立周期" in md

    @pytest.mark.asyncio
    async def test_overlapping_flag_is_the_rollback_switch(self, db_session):
        codes = await _seed_pool(db_session)
        nav = await _fake_nav_factory({c: 0.0005 * i for i, c in enumerate(codes)})
        report = await FactorAuditService(db_session, nav_provider=nav).audit(
            days=40, horizons=(3,), overlapping_ic=True)
        assert report.ic_sampling == "overlapping"
        ideal = next(f for f in report.factor_ic if f["factor"] == "f_ideal")
        assert ideal["days"] == ideal["n_days_valid"]
        assert "逐日重叠周期" in report.summary_md()

    @pytest.mark.asyncio
    async def test_few_independent_periods_forbids_conclusion(self, db_session):
        """独立周期 < 门槛时必须出现"不要据此调权重"的告警行"""
        codes = await _seed_pool(db_session)
        nav = await _fake_nav_factory({c: 0.0005 * i for i, c in enumerate(codes)})
        report = await FactorAuditService(db_session, nav_provider=nav).audit(days=30, horizons=(20,))
        ideal = next((f for f in report.factor_ic if f["factor"] == "f_ideal"), None)
        if ideal is not None:
            assert ideal["days"] < MIN_IC_PERIODS_FOR_CONCLUSION
        assert any("不要据此调整因子权重" in c for c in report.caveats)

    def test_llm_prompt_holds_the_same_numbers(self):
        from backend.ai.presets import PRESET_TASKS

        prompt = PRESET_TASKS["factor_audit"]["default_prompt"]
        assert "非重叠周期" in prompt and "rank_ic_q_bh" in prompt
        assert str(MIN_IC_PERIODS_FOR_CONCLUSION) in prompt
        assert "rank_ic_ir_annualized" in prompt
