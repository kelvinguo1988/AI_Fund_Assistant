"""P2 因子诊断引擎测试：纯统计黄金用例 + 注入净值源的服务级回算"""

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from backend.ai.factor_audit import (
    FactorAuditService,
    daily_ic,
    forward_return,
    group_compare,
    quintile_returns,
    signal_stats,
    spearman,
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
