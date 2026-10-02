"""Q5（第二批 2D）：weighted_score 是**池内相对分**的标注链路

6 个截面 z 因子加权而来的分数，当日池内均值恒为 0：它只回答"这只在当天这批基金里排第几"，
不回答"这只基金好不好"。池子构成一变分数就变，所以同一个 2.5 分在 57 只池和 8 只池不是一
回事，也不能跨日比较。裁定口径 = 不改算法，**在每个出系统的面上显式标注 + 落池规模**。

这里锁住三件事：措辞只有一个定义点；前后端阈值/文案一致；API 与报告把标注真的带出去。
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.engines.scoring_engine import THIN_POOL_SIZE, score_caliber_note

REPO = Path(__file__).resolve().parents[1]


def json_factor_scores() -> str:
    import json
    return json.dumps({"short_momentum": {"score": 1.2, "raw_value": 0.03}})


def _row(**overrides) -> SimpleNamespace:
    base = dict(
        id=1, fund_id=2, analysis_date="2026-10-02", weighted_score=2.5,
        signal_direction="buy", signal_strength="moderate_buy",
        operation_advice="适度买入", equity_ratio=0.6,
        factor_scores=json_factor_scores(), created_at="2026-10-02T09:00:00",
        pool_size=57,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── 措辞单点 ─────────────────────────────────────────────────────────
class TestCaliberNoteWording:
    def test_normal_pool_states_sample_size(self):
        assert score_caliber_note(57) == "池内相对分（当日 57 只参与截面标准化）"

    def test_thin_pool_is_flagged(self):
        note = score_caliber_note(8)
        assert "池子偏薄" in note and "当日 8 只" in note

    def test_pool_size_at_boundary_is_not_thin(self):
        assert "偏薄" not in score_caliber_note(THIN_POOL_SIZE)

    @pytest.mark.parametrize("empty", [None, 0])
    def test_missing_sample_size_says_unknown(self, empty):
        note = score_caliber_note(empty)
        assert "未记录" in note and "不能跨期" in note

    def test_note_never_claims_absolute_quality(self):
        # 反向守卫：文案里不能出现"绝对分/收益率"这类会被读成绝对含义的字
        for size in (None, 5, 60):
            assert "绝对" not in score_caliber_note(size).replace("不能跨期/跨池比较", "")


# ── 前后端口径一致 ───────────────────────────────────────────────────
class TestFrontendParity:
    """前端 format.ts 是后端函数的镜像，值漂移会静默改变展示含义"""

    def test_frontend_thin_pool_constant_matches_backend(self):
        text = (REPO / "frontend/src/utils/format.ts").read_text(encoding="utf-8")
        m = re.search(r"export const THIN_POOL_SIZE = (\d+)", text)
        assert m, "format.ts 缺少 THIN_POOL_SIZE 定义"
        assert int(m.group(1)) == THIN_POOL_SIZE

    def test_frontend_helper_uses_same_wording(self):
        text = (REPO / "frontend/src/utils/format.ts").read_text(encoding="utf-8")
        assert "export const scoreCaliberNote" in text
        # 三个分支的关键词必须与后端逐字一致
        for kw in ("池内相对分", "参与截面标准化", "池子偏薄", "未记录"):
            assert kw in text, f"前端缺少措辞片段: {kw}"

    def test_api_schema_exposes_pool_size(self):
        src = (REPO / "backend/schemas/analysis.py").read_text(encoding="utf-8")
        assert re.search(r"pool_size: Optional\[int\]", src)


def _signal():
    from backend.engines.scoring_engine import SignalResult

    return SignalResult(
        weighted_score=2.5, raw_score=2.5, signal_direction="buy",
        signal_strength="moderate_buy", operation_advice="适度买入", equity_ratio=0.6,
    )


class TestResultToOutCarriesPoolSize:
    def test_pool_size_passed_through(self):
        from backend.routers.analysis import _result_to_out

        assert _result_to_out(_row()).pool_size == 57

    def test_legacy_row_is_none_not_zero(self):
        """0 会被读成'池子里没有基金'，NULL 才表示这批数据没记过样本数"""
        from backend.routers.analysis import _result_to_out

        assert _result_to_out(_row(pool_size=None)).pool_size is None


# ── 报告 / 推送文案 ──────────────────────────────────────────────────
class TestReportCarriesCaliber:
    def test_markdown_states_sample_size(self):
        from backend.engines.report_engine import ReportEngine

        md = ReportEngine().generate_markdown(
            fund_code="018994", fund_name="测试", analysis_date="2026-10-02",
            signal=_signal(), factor_scores=[],
            enabled_items=["weighted_score"], pool_size=57,
        )
        assert "综合评分" in md
        assert "池内相对分（当日 57 只参与截面标准化）" in md

    def test_markdown_without_sample_size(self):
        from backend.engines.report_engine import ReportEngine

        md = ReportEngine().generate_markdown(
            fund_code="x", fund_name="t", analysis_date="d",
            signal=_signal(), factor_scores=[],
            enabled_items=["weighted_score"],
        )
        assert "未记录" in md

    def test_top10_section_is_prefixed_with_caliber(self):
        """买卖前列表里的评分同样只在当日池内可比，标题前必须有一句口径"""
        from backend.engines.report_engine import ReportEngine
        from backend.schemas.market import MarketSummaryOut, SignalSummary

        ms = MarketSummaryOut(date="2026-10-02", signals=SignalSummary(total=57, buy_count=3))
        md = ReportEngine().generate_market_summary_markdown(
            ms, enabled_items=["signal_summary", "top_buy_sell"]
        )
        assert "池内相对分（当日 57 只参与截面标准化）" in md
        assert "只在当日池内可比" in md


class TestFeishuCardCaliber:
    def test_card_score_line_is_labeled(self):
        src = (REPO / "backend/push/feishu.py").read_text(encoding="utf-8")
        assert "-8.5 ~ +8.5，池内相对分" in src


# ── 落库来源必须是真的截面样本数 ─────────────────────────────────────
class TestPoolSizeIsRealSampleCount:
    def test_config_default_is_none(self):
        """未跑批量分析（单只重算/导入）时不能凭空造一个池规模"""
        from backend.services.analysis_service import _AnalysisConfig

        cfg = _AnalysisConfig(
            active_factors=[], thresholds_json="[]", qf=None,
            regime_snapshot=None, regime_factors=[],
        )
        assert cfg.pool_size is None

    def test_score_history_tool_advertises_caliber(self):
        """AI 取历史的工具正是最容易误用跨期分数处，描述里必须写明"""
        from backend.ai_tools import data_tools

        src = (REPO / "backend/ai_tools/data_tools.py").read_text(encoding="utf-8")
        assert src.count("池内相对分") >= 2
        assert "pool_size" in src
        assert data_tools is not None
