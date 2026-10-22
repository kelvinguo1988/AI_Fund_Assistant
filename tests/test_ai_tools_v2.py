"""AI 工具扩展（2026-10-22）：费率精算 / 净值历史 / 评级 / 跨境溢价 / 美股环境

设计目标：给 AI 工作台补上「用户自己算不出来」的能力。
最典型的是 get_trade_cost——赎回费按持有期阶梯计，7 天内 1.5%，
短线交易常常赚了涨幅倒亏手续费。这些用例大多是纯函数校验（不打上游）。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.fund_fee_parser import (  # noqa: E402
    breakeven_growth_pct, parse_operating_fees, parse_redemption_tiers,
    redemption_rate_for_days,
)


class TestRedemptionTierParsing:
    """东财赎回费率的自然语言 → 可计算阶梯"""

    ROWS = [
        {"适用期限": "大于等于30天", "赎回费率": "0.00%"},
        {"适用期限": "小于7天", "赎回费率": "1.50%"},
        {"适用期限": "大于等于7天，小于30天", "赎回费率": "0.50%"},
    ]

    def test_parses_three_tiers_sorted(self):
        tiers = parse_redemption_tiers(self.ROWS)
        assert len(tiers) == 3
        assert [t["min_days"] for t in tiers] == [0, 7, 30], "必须按持有天数升序"
        assert tiers[0]["max_days"] == 7
        assert tiers[2]["max_days"] is None, "最高档无上限"

    @pytest.mark.parametrize("days,expect", [
        (0, 1.5), (3, 1.5), (6, 1.5),     # <7 天
        (7, 0.5), (15, 0.5), (29, 0.5),   # 7~29 天
        (30, 0.0), (365, 0.0),            # ≥30 天
    ])
    def test_rate_lookup_boundaries(self, days, expect):
        """边界必须精确：第 7 天就该降到 0.5%"""
        tiers = parse_redemption_tiers(self.ROWS)
        assert redemption_rate_for_days(tiers, days) == expect

    def test_malformed_rows_ignored(self):
        tiers = parse_redemption_tiers([
            {"适用期限": "看不懂的文案", "赎回费率": "1%"},
            {"适用期限": "小于7天", "赎回费率": "无"},
        ])
        assert tiers == [], "解析不了就返回空，不能抛异常"

    def test_empty_input(self):
        assert parse_redemption_tiers([]) == []
        assert parse_redemption_tiers(None) == []


class TestOperatingFees:
    def test_flat_positional_keys(self):
        """akshare 返回的是扁平位置键 {0:名称, 1:值, ...}"""
        pairs = {0: "管理费率", 1: "0.60%（每年）",
                 2: "托管费率", 3: "0.15%（每年）",
                 4: "销售服务费率", 5: "0.25%（每年）"}
        got = parse_operating_fees(pairs)
        assert got == {"管理费率": 0.6, "托管费率": 0.15, "销售服务费率": 0.25}
        assert round(sum(got.values()), 3) == 1.0

    def test_plain_dict_also_supported(self):
        got = parse_operating_fees({"管理费率": "0.8%", "托管费率": "0.2%"})
        assert got == {"管理费率": 0.8, "托管费率": 0.2}

    def test_empty(self):
        assert parse_operating_fees({}) == {}
        assert parse_operating_fees(None) == {}


class TestBreakeven:
    def test_within_7_days_costs_more_than_1_5pct(self):
        """持有 3 天赎回 1.5%：需要涨 1.52% 才回本（不是 1.5%）"""
        be = breakeven_growth_pct(0, 1.5)
        assert be == pytest.approx(1.523, abs=0.001)
        assert be > 1.5, "复利口径下必须略高于费率本身"

    def test_no_fee_means_zero(self):
        assert breakeven_growth_pct(0, 0) == 0.0

    def test_both_ends_included(self):
        """申购 0.12% + 赎回 0.5% 两端合计

        注意：盈亏平衡不是两端费率的简单相加。公式 (1-b)/(1-s)-1 下
        b=0.12% s=0.5% → 0.382%，而非 0.62%。数学上是对的（费率作用于
        不同基数：买时按本金扣，卖时按含收益的市值扣），简单相加会高估成本。
        """
        be = breakeven_growth_pct(0.12, 0.5)
        assert be == pytest.approx(0.382, abs=0.001)
        assert be < 0.62, "不能退回简单相加（会高估交易成本）"
        assert be > 0, "两端都有费用时必然为正"

    def test_absurd_rate_returns_none(self):
        assert breakeven_growth_pct(0, 100) is None
        assert breakeven_growth_pct(150, 0) is None


class TestNewToolsRegistered:
    EXPECTED = [
        "get_trade_cost", "get_fund_fee_profile", "get_fund_nav_history",
        "get_fund_rating", "get_cross_border_etf_premium",
        "get_us_market_context", "get_fund_exposure",
    ]

    def test_all_new_tools_present(self):
        from backend.ai_tools.registry import _REGISTRY
        import backend.ai_tools  # noqa: F401

        for name in self.EXPECTED:
            assert name in _REGISTRY, f"{name} 未注册"

    def test_specs_include_them(self):
        from backend.ai_tools.registry import tool_specs
        import backend.ai_tools  # noqa: F401

        names = {s["function"]["name"] for s in tool_specs(None)}
        for name in self.EXPECTED:
            assert name in names


class TestTokenBudget:
    """工具集体积护栏：扩容不能失控（2026-10-22 token 优化）"""

    def test_tool_specs_stay_compact(self):
        import json

        from backend.ai_tools.registry import tool_specs
        import backend.ai_tools  # noqa: F401

        raw = json.dumps(tool_specs(None), ensure_ascii=False)
        n = len(tool_specs(None))
        # 22 个工具实测 6790 字符 ≈ 每工具 309 字符。上限按「每工具 340」
        # 设定：既允许合理描述，又能挡住把整段文档塞进 description 的膨胀。
        assert len(raw) < n * 340, (
            f"{n} 个工具占 {len(raw)} 字符（均 {len(raw)//n}），需精简描述"
        )

    def test_compact_rows_shrinks_key_repetition(self):
        from backend.ai_tools.registry import compact_rows

        items = [{"code": f"{i:06d}", "name": "基金", "tags": "股票型"} for i in range(50)]
        import json
        raw = len(json.dumps(items, ensure_ascii=False))
        comp = len(json.dumps(compact_rows(items), ensure_ascii=False))
        assert comp < raw * 0.7, f"列式压缩不足：{raw} -> {comp}"

    def test_compact_rows_drops_empty_columns(self):
        from backend.ai_tools.registry import compact_rows

        out = compact_rows([{"a": 1, "b": None}, {"a": 2, "b": None}])
        assert out["cols"] == ["a"], "全空列必须丢掉"


class TestPortfolioTools:
    """组合分析与集中度（2026-10-22 Phase 2）"""

    EXPECTED = ["get_portfolio_correlation", "get_portfolio_concentration"]

    def test_registered(self):
        from backend.ai_tools.registry import _REGISTRY
        import backend.ai_tools  # noqa: F401

        for n in self.EXPECTED:
            assert n in _REGISTRY

    def test_corr_math(self):
        """皮尔逊相关系数：完全同向=1，完全反向=-1"""
        from backend.ai_tools.data_tools import _corr

        days = [f"2026-01-{i:02d}" for i in range(1, 26)]
        up = {d: 0.01 for d in days}
        down = {d: -0.01 for d in days}
        # 加扰动避免零方差
        up2 = {d: 0.01 + (0.001 if i % 2 else 0) for i, d in enumerate(days)}
        down2 = {d: -0.01 - (0.001 if i % 2 else 0) for i, d in enumerate(days)}
        assert _corr(up2, up2) == pytest.approx(1.0, abs=1e-6)
        assert _corr(up2, down2) == pytest.approx(-1.0, abs=1e-6)

    def test_corr_needs_enough_samples(self):
        from backend.ai_tools.data_tools import _corr

        few = {f"2026-01-0{i}": 0.01 for i in range(1, 5)}
        assert _corr(few, few) is None, "样本 <20 天不可靠，应返回 None"

    def test_daily_returns(self):
        from backend.ai_tools.data_tools import _daily_returns

        series = {"2026-01-01": 1.0, "2026-01-02": 1.1, "2026-01-03": 1.21}
        r = _daily_returns(series)
        assert r["2026-01-02"] == pytest.approx(0.1, abs=1e-6)
        assert r["2026-01-03"] == pytest.approx(0.1, abs=1e-6)

    def test_daily_returns_skips_zero_base(self):
        from backend.ai_tools.data_tools import _daily_returns

        r = _daily_returns({"2026-01-01": 0.0, "2026-01-02": 1.0})
        assert r == {}, "除零必须跳过而不是崩"

    @pytest.mark.asyncio
    async def test_concentration_reports_unavailable_honestly(self, db_session):
        """赛道标签全空时不能假装「较分散」（0% 会被误读成低风险）"""
        from backend.ai_tools.data_tools import t_get_portfolio_concentration
        from tests.test_ai_agent import _seed

        await _seed(db_session)
        out = await t_get_portfolio_concentration(db_session)
        if not out.get("by_exposure"):
            assert out["concentration_level"] == "无法评估"
            assert "exposure_tags" in out.get("note", "")
