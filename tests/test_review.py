"""投资复盘服务回归测试 — 区间切片/组合收益/信号同向率（绝对+超额）/三行口径头报告生成"""

import sys, os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.review_service import (
    ReviewService,
    _nearest_on_or_before,
    _slice_range,
)


# ── 区间切片纯函数 ────────────────────────────────────────────────────

SERIES = [
    ("2026-08-01", 1.00),
    ("2026-08-05", 1.10),
    ("2026-08-10", 1.20),
    ("2026-08-15", 0.99),
    ("2026-08-20", 1.05),
]


class TestSliceRange:
    def test_exact_day(self):
        s0, s1 = _slice_range(SERIES, "2026-08-05", "2026-08-15")
        assert s0 == ("2026-08-05", 1.10)
        assert s1 == ("2026-08-15", 0.99)

    def test_nearest_before(self):
        """起始日是非交易日 → 取之前最近净值日"""
        s0, s1 = _slice_range(SERIES, "2026-08-03", "2026-08-18")
        assert s0 == ("2026-08-01", 1.00)
        assert s1 == ("2026-08-15", 0.99)

    def test_before_all(self):
        s0, _ = _slice_range(SERIES, "2026-07-01", "2026-08-15")
        assert s0 is None

    def test_nearest_on_or_before(self):
        assert _nearest_on_or_before(SERIES, "2026-08-07") == ("2026-08-05", 1.10)
        assert _nearest_on_or_before(SERIES, "2026-07-01") is None


# ── 信号命中率 ────────────────────────────────────────────────────────

def _item(code, growth, signal_start):
    from backend.schemas.analysis import FundReviewItem
    return FundReviewItem(
        fund_code=code, fund_name=code, growth_pct=growth, signal_start=signal_start,
    )


class TestSignalHitStats:
    def test_mixed_signals(self):
        items = [
            _item("A", 5.0, "buy"),     # buy 涨 → 命中
            _item("B", -2.0, "buy"),    # buy 跌 → 未命中
            _item("C", -3.0, "sell"),   # sell 跌 → 命中
            _item("D", 1.0, "sell"),    # sell 涨 → 未命中
            _item("E", None, "buy"),    # 无净值数据 → 跳过
            _item("F", 2.0, "hold"),    # hold → 不统计
        ]
        stats = ReviewService._signal_hit_stats(items)
        assert stats["buy_total"] == 2 and stats["buy_hits"] == 1
        assert stats["sell_total"] == 2 and stats["sell_hits"] == 1
        assert stats["hit_rate"] == 50.0
        assert "excess" not in stats   # 基准缺失时不硬造超额口径

    def test_no_signals(self):
        stats = ReviewService._signal_hit_stats([_item("A", 1.0, "hold")])
        assert stats["hit_rate"] is None

    def test_excess_rate_only_when_benchmark_given(self):
        items = [
            _item("A", 5.0, "buy"),     # 涨 5% 但跑输基准 8% → 绝对命中、超额未命中
            _item("B", -2.0, "buy"),    # 跌 → 两个口径都未命中
            _item("C", 9.0, "sell"),    # 涨 9% 且跑赢基准 → 两个口径都未命中（确实卖错了）
            _item("D", -3.0, "sell"),   # 跌且跑输基准 → 两个口径都命中
        ]
        stats = ReviewService._signal_hit_stats(items, 8.0)
        ex = stats["excess"]
        assert stats["buy_total"] == 2 and stats["buy_hits"] == 1
        assert stats["sell_total"] == 2 and stats["sell_hits"] == 1
        assert stats["hit_rate"] == 50.0
        assert ex["buy_total"] == 2 and ex["buy_hits"] == 0
        assert ex["sell_total"] == 2 and ex["sell_hits"] == 1
        assert ex["hit_rate"] == 25.0
        assert ex["benchmark_growth_pct"] == 8.0

    def test_bull_market_absolute_rate_is_beta(self):
        """上涨市里绝对口径恒真（这正是 Q11 要把同向率改成双口径的原因）"""
        items = [_item(f"F{i}", 1.0, "buy") for i in range(4)]
        stats = ReviewService._signal_hit_stats(items, 6.0)
        assert stats["hit_rate"] == 100.0        # 全都涨
        assert stats["excess"]["hit_rate"] == 0.0  # 但全都跑输基准


# ── summary 文本生成 ──────────────────────────────────────────────────

class TestSummaryMd:
    def test_full_report(self):
        from backend.schemas.analysis import ReviewReport
        r = ReviewReport(
            start_date="2026-08-01", end_date="2026-08-28",
            fund_count=3, portfolio_growth_pct=2.5,
            benchmark_growth_pct=1.0, excess_pct=1.5,
            best=_item("B", 5.0, "buy"), worst=_item("C", -3.0, "sell"),
            items=[_item("B", 5.0, "buy"), _item("C", -3.0, "sell")],
            signal_stats={
                "buy_total": 1, "buy_hits": 1, "sell_total": 1, "sell_hits": 0,
                "hit_rate": 50.0,
                "excess": {"buy_total": 1, "buy_hits": 0, "sell_total": 1,
                           "sell_hits": 1, "hit_rate": 50.0, "benchmark_growth_pct": 1.0},
            },
            caliber={"nav_adjusted": True, "bench_div_yield_pct": 2.7, "lines": [
                "> 净值口径：场外基金分红复权（前复权）、场内 ETF 前复权；组合按基金池等权买入持有",
                "> 基准口径：沪深300 价格指数 + 股息 2.7%/年（按区间交易日折算）",
                "> 计息口径：满仓假设，不涉及现金利息",
            ]},
        )
        md = ReviewService._build_summary_md(r)
        # Q11-C：三行口径头固定在最前，且基准不再被称作"沪深300 价格回报"
        assert md.index("净值口径") < md.index("一句话总结") < md.index("跑赢基准")
        assert "股息 2.7%/年" in md
        assert "跑赢基准 1.5pp" in md
        assert "信号复盘（绝对口径）：区间首日信号命中率 **50.0%**" in md
        assert "信号复盘（超额口径，基准 +1.00%）：命中率 **50.0%**" in md
        assert "| 基金 | 区间涨跌" in md
        assert "不构成投资建议" in md

    def test_insufficient_data(self):
        from backend.schemas.analysis import ReviewReport
        r = ReviewReport(
            start_date="a", end_date="b", fund_count=0,
            items=[], signal_stats={},
        )
        md = ReviewService._build_summary_md(r)
        assert "有效净值数据不足" in md


# ── 路由参数校验 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_rejects_bad_range(db_session):
    from backend.services.review_service import ReviewService
    svc = ReviewService(db_session)
    with pytest.raises(ValueError):
        await svc.review("2026-08-28", "2026-08-01")
    with pytest.raises(ValueError):
        await svc.review("2020-01-01", "2026-08-28")


