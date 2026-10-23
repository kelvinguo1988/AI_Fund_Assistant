"""工具分组（2026-10-23）

目的：工具定义每轮重发，24 个工具 7363 字符、3 轮对话累计约 3.5 万字符。
按问题类型只暴露相关工具可省 58~82%。

但实测发现一个反直觉的事实，决定了本设计必须保守：
- 只给 3 个工具时，模型把 3 个**全调用了**（比不分组更费）；
- 给全量 24 个时，模型只调 4 个且都切题。
所以**选错组的代价高于省下的 token** → 置信不足必须回退全量。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.ai_tools.registry import (  # noqa: E402
    TOOL_GROUPS, group_tools, pick_group,
)


class TestPickGroup:
    @pytest.mark.parametrize("prompt,expect", [
        ("我想买004011持有3天会亏吗", "trade"),
        ("跨境ETF有溢价吗", "trade"),
        ("我的组合分散得好吗", "portfolio"),
        ("帮我看看持仓集中度", "portfolio"),
        ("大盘现在什么情况", "market"),
        ("004011这只基金表现如何", "fund"),
    ])
    def test_classifies_common_questions(self, prompt, expect):
        assert pick_group(prompt) == expect

    def test_ambiguous_question_falls_back(self):
        """「买入机会」同时命中交易与信号 → 不猜，回退全量"""
        assert pick_group("今天有什么买入机会") is None

    def test_small_talk_falls_back(self):
        assert pick_group("你好") is None
        assert pick_group("") is None
        assert pick_group(None) is None

    def test_never_returns_unknown_group(self):
        for q in ("买基金", "组合", "大盘", "净值", "随便说点什么"):
            g = pick_group(q)
            assert g is None or g in TOOL_GROUPS


class TestGroupTools:
    def test_every_group_is_subset_of_registry(self):
        """分组里不能出现未注册的工具名（删工具后分组表不能失效）"""
        from backend.ai_tools.registry import _REGISTRY
        import backend.ai_tools  # noqa: F401

        for group, names in TOOL_GROUPS.items():
            for n in names:
                assert n in _REGISTRY, f"组 {group} 含未注册工具 {n}"

    def test_group_tools_filters_unknown(self):
        got = group_tools("trade")
        assert got is not None and all(isinstance(x, str) for x in got)

    def test_unknown_group_returns_none(self):
        assert group_tools("no_such_group") is None
        assert group_tools(None) is None

    def test_group_is_smaller_than_full(self):
        from backend.ai_tools.registry import tool_specs
        import backend.ai_tools  # noqa: F401

        full = len(tool_specs(None))
        for group in TOOL_GROUPS:
            names = group_tools(group)
            assert len(tool_specs(names)) < full, f"{group} 组没有比全量小"


class TestGroupingSavings:
    def test_trade_group_saves_significantly(self):
        import json

        from backend.ai_tools.registry import tool_specs
        import backend.ai_tools  # noqa: F401

        full = len(json.dumps(tool_specs(None), ensure_ascii=False))
        grp = len(json.dumps(tool_specs(group_tools("trade")), ensure_ascii=False))
        assert grp < full * 0.5, f"分组仅省 {(1-grp/full)*100:.0f}%，收益不明显"

    def test_key_group_has_core_tools(self):
        """关键组必须包含该场景最核心的工具，否则模型会绕路"""
        assert "get_trade_cost" in TOOL_GROUPS["trade"]
        assert "get_portfolio_correlation" in TOOL_GROUPS["portfolio"]
        assert "get_latest_signals" in TOOL_GROUPS["signal"]
        assert "get_fund_nav_history" in TOOL_GROUPS["fund"]
        assert "get_market_snapshot" in TOOL_GROUPS["market"]


class TestRunnerWiring:
    def test_runner_uses_grouping_when_unrestricted(self):
        """allowed_tools 为空时走分组；显式指定时不覆盖调用方意图"""
        import inspect
        from backend.ai.agent_runner import AgentRunner

        src = inspect.getsource(AgentRunner.run_stream)
        assert "pick_group" in src and "group_tools" in src
        assert "if allowed is None" in src, "显式 allowed_tools 不能被分组覆盖"
