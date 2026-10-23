"""AI 点评生成（2026-10-22）

三个设计要点的回归护栏：
1. 模型输出是脏的（围栏、前后废话、少括号）也要能解析，否则点评等于白生成；
2. 只点评「有事可说」的（非 hold / 带警告），hold 且无警告的跳过省额度；
3. 生成失败必须静默——点评是增强信息，不能让分析流程报错。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.ai_comment_service import (  # noqa: E402
    MAX_FUNDS, parse_comments,
)


class TestParseComments:
    def test_plain_json_array(self):
        got = parse_comments('[{"code":"004011","comment":"动量强"}]')
        assert got == {"004011": "动量强"}

    def test_markdown_fence(self):
        """模型最爱套 ```json 围栏"""
        got = parse_comments('```json\n[{"code":"A1","comment":"x"}]\n```')
        assert got == {"A1": "x"}

    def test_chatter_around_json(self):
        """前后带解释文字也要能捞出 JSON"""
        got = parse_comments('好的，结果如下：\n[{"code":"B2","comment":"y"}]\n以上。')
        assert got == {"B2": "y"}

    def test_object_wrapped(self):
        got = parse_comments('{"comments":[{"code":"C3","comment":"z"}]}')
        assert got == {"C3": "z"}

    def test_unparseable_returns_empty(self):
        """解析不出返回空 dict，由调用方按未生成处理，不抛异常"""
        assert parse_comments("我无法回答") == {}
        assert parse_comments("") == {}
        assert parse_comments(None) == {}

    def test_skips_rows_without_code_or_text(self):
        got = parse_comments('[{"code":"","comment":"x"},{"code":"D4"},{"code":"E5","comment":"ok"}]')
        assert got == {"E5": "ok"}

    def test_truncates_long_comment(self):
        """单条点评截断到 200 字，防止模型写长篇把推送撑爆"""
        long = "很长的点评" * 100
        got = parse_comments(f'[{{"code":"F6","comment":"{long}"}}]')
        assert len(got["F6"]) <= 200


class TestCommentScope:
    def test_max_funds_capped(self):
        assert MAX_FUNDS <= 50, "单次点评上限过大，费用与耗时都不可控"


class TestAiCommentPersistence:
    """字段与迁移：点评必须能落库，前端才读得到"""

    def test_model_has_comment_columns(self):
        from backend.models.analysis_result import AnalysisResult

        cols = {c.name for c in AnalysisResult.__table__.columns}
        for c in ("ai_comment", "ai_comment_model", "ai_comment_at"):
            assert c in cols, f"{c} 缺失"

    def test_output_schema_exposes_comment(self):
        from backend.schemas.analysis import AnalysisResultOut

        fields = set(AnalysisResultOut.model_fields)
        assert "ai_comment" in fields, "前端读不到 ai_comment"

    def test_migration_present(self):
        """老库升级时必须有 ALTER TABLE，否则线上会 no such column"""
        import inspect
        from backend import database

        src = inspect.getsource(database)
        assert "ADD COLUMN ai_comment" in src


class TestSilentFailure:
    @pytest.mark.asyncio
    async def test_model_error_returns_error_not_raise(self, db_session):
        """模型挂了不能抛异常，否则分析流程跟着崩"""
        from backend.services.ai_comment_service import generate_comments

        class BoomProvider:
            model_name = "boom"

            async def chat(self, **kwargs):
                raise RuntimeError("model down")

        out = await generate_comments(db_session, BoomProvider())
        assert out.get("error"), "应返回 error 而不是抛异常"


class TestAllAnalysisPathsWired:
    """分析有三条独立路径，漏挂任何一条都会让点评静默缺失（线上踩过）

    1. POST /analysis/trigger         手动点「开始分析」
    2. POST /analysis/trigger-stream  前端 SSE 触发的分析
    3. 定时调度 task_scheduler        每日自动分析（推送前必须生成，否则推送无点评）
    """

    def test_manual_trigger_wired(self):
        import inspect
        from backend.routers import analysis

        src = inspect.getsource(analysis.trigger_analysis)
        assert "_spawn_ai_comments()" in src, "手动触发路径漏挂点评"

    def test_stream_trigger_wired(self):
        import inspect
        from backend.routers import analysis

        src = inspect.getsource(analysis.trigger_analysis_stream)
        assert "_spawn_ai_comments()" in src, "SSE 触发路径漏挂点评"

    def test_scheduler_wired_before_push(self):
        """调度路径必须在推送前同步生成，否则推送的 AI 段永远为空"""
        import inspect
        from backend.scheduler import task_scheduler

        src = inspect.getsource(task_scheduler)
        assert "generate_comments" in src, "定时任务未接入点评"
        # 位置校验：点评代码要出现在推送段之前
        idx_ai = src.find("调度内 AI 点评")
        idx_push = src.find("推送段")
        assert idx_ai != -1 and idx_push != -1
        assert idx_ai < idx_push, "点评必须在推送之前生成"
