"""P0 诊断地基测试：original_score/动态阈值/quality_warnings 落库与透出

背景：这三字段此前只在 trigger 当次响应返回、从不落库（analysis_export 里
全 NULL 的根因），AI Agent 因子/质量过滤历史有效性回算（factor_audit）依赖
它们，本次补列并打通 落库→查询→导出→导入 全链路。
"""

import json
from datetime import date

import pytest
from sqlalchemy import select

from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.engines.scoring_engine import SignalResult


def _mk_signal(original_score=1.2345, buy=1.5, sell=-1.5, warnings=None):
    return SignalResult(
        weighted_score=2.0,
        raw_score=2.1,
        signal_direction="buy",
        signal_strength="moderate_buy",
        operation_advice="test",
        equity_ratio=0.7,
        original_score=original_score,
        dynamic_buy_threshold=buy,
        dynamic_sell_threshold=sell,
        quality_warnings=warnings or [],
    )


async def _mk_fund(db):
    fund = Fund(code="004011", name="测试基金", fund_type="otc", status="active")
    db.add(fund)
    await db.flush()
    return fund


class TestDiagPersistRoundtrip:
    @pytest.mark.asyncio
    async def test_signal_result_has_sell_threshold_default(self):
        s = _mk_signal()
        assert s.dynamic_sell_threshold == -1.5

    @pytest.mark.asyncio
    async def test_save_result_persists_diag_fields(self, db_session):
        from backend.services.analysis_service import AnalysisService

        fund = await _mk_fund(db_session)
        svc = AnalysisService(db_session)
        out = await svc._save_result(fund, _mk_signal(warnings=["规模冲击", "风格漂移"]), [])
        await db_session.commit()

        row = (await db_session.execute(select(AnalysisResult))).scalars().one()
        assert row.original_score == pytest.approx(1.2345)
        assert row.dynamic_buy_threshold == pytest.approx(1.5)
        assert row.dynamic_sell_threshold == pytest.approx(-1.5)
        assert json.loads(row.quality_warnings) == ["规模冲击", "风格漂移"]
        # 当次响应同样携带
        assert out.quality_warnings == ["规模冲击", "风格漂移"]
        assert out.dynamic_sell_threshold == pytest.approx(-1.5)

    @pytest.mark.asyncio
    async def test_no_warnings_stored_as_null(self, db_session):
        from backend.services.analysis_service import AnalysisService

        fund = await _mk_fund(db_session)
        await AnalysisService(db_session)._save_result(fund, _mk_signal(), [])
        await db_session.commit()
        row = (await db_session.execute(select(AnalysisResult))).scalars().one()
        assert row.quality_warnings is None

    @pytest.mark.asyncio
    async def test_rerun_same_day_overwrites_diag_fields(self, db_session):
        from backend.services.analysis_service import AnalysisService

        fund = await _mk_fund(db_session)
        svc = AnalysisService(db_session)
        await svc._save_result(fund, _mk_signal(original_score=1.0, warnings=["旧警告"]), [])
        await db_session.commit()
        await svc._save_result(fund, _mk_signal(original_score=3.0, warnings=["新警告"]), [])
        await db_session.commit()

        rows = (await db_session.execute(select(AnalysisResult))).scalars().all()
        assert len(rows) == 1
        assert rows[0].original_score == pytest.approx(3.0)
        assert json.loads(rows[0].quality_warnings) == ["新警告"]

    @pytest.mark.asyncio
    async def test_result_to_out_surfaces_fields(self, db_session):
        from backend.services.analysis_service import AnalysisService
        from backend.routers.analysis import _result_to_out

        fund = await _mk_fund(db_session)
        await AnalysisService(db_session)._save_result(
            fund, _mk_signal(warnings=["警告A"], sell=-2.0), []
        )
        await db_session.commit()
        row = (await db_session.execute(select(AnalysisResult))).scalars().one()
        out = _result_to_out(row, fund)
        assert out.original_score == pytest.approx(1.2345)
        assert out.dynamic_sell_threshold == pytest.approx(-2.0)
        assert out.quality_warnings == ["警告A"]

    @pytest.mark.asyncio
    async def test_export_import_carries_diag_fields(self, db_session):
        from backend.services.analysis_service import AnalysisService

        fund = await _mk_fund(db_session)
        await AnalysisService(db_session)._save_result(
            fund, _mk_signal(warnings=["导出警告"]), []
        )
        await db_session.commit()

        payload = await AnalysisService(db_session).export_analysis()
        item = payload.items[0]
        assert item.original_score == pytest.approx(1.2345)
        assert item.quality_warnings == ["导出警告"]
        assert item.dynamic_sell_threshold == pytest.approx(-1.5)

        # 导入覆盖路径同样回写（先清空再 overwrite 导入）
        row = (await db_session.execute(select(AnalysisResult))).scalars().one()
        row.quality_warnings = None
        row.original_score = None
        await db_session.commit()

        svc = AnalysisService(db_session)
        result = await svc.import_analysis(payload, overwrite=True)
        assert result.updated == 1
        row = (await db_session.execute(select(AnalysisResult))).scalars().one()
        assert row.original_score == pytest.approx(1.2345)
        assert json.loads(row.quality_warnings) == ["导出警告"]


# ── data_valid 全链路透传（2026-10-01 审查 P1）────────────────────────

def _mk_factors():
    from backend.engines.factor_engine import FactorScoreResult
    return [
        FactorScoreResult("short_momentum", "短期动量", 0.11, 0.5, "positive"),
        FactorScoreResult(
            "market_valuation", "大盘估值分位", 0.0, 0.0, "negative", data_valid=False
        ),
    ]


class TestDataValidRoundtrip:
    @pytest.mark.asyncio
    async def test_saved_json_and_out_keep_flag(self, db_session):
        from backend.services.analysis_service import AnalysisService

        fund = await _mk_fund(db_session)
        out = await AnalysisService(db_session)._save_result(fund, _mk_signal(), _mk_factors())
        await db_session.commit()

        row = (await db_session.execute(select(AnalysisResult))).scalars().one()
        stored = json.loads(row.factor_scores)
        assert stored["short_momentum"]["data_valid"] is True
        assert stored["market_valuation"]["data_valid"] is False
        assert [fs.data_valid for fs in out.factor_scores] == [True, False]

    @pytest.mark.asyncio
    async def test_result_to_out_rebuilds_flag(self, db_session):
        from backend.routers.analysis import _result_to_out
        from backend.services.analysis_service import AnalysisService

        fund = await _mk_fund(db_session)
        await AnalysisService(db_session)._save_result(fund, _mk_signal(), _mk_factors())
        await db_session.commit()
        row = (await db_session.execute(select(AnalysisResult))).scalars().one()

        out = _result_to_out(row, fund)
        assert [fs.data_valid for fs in out.factor_scores] == [True, False]

    @pytest.mark.asyncio
    async def test_legacy_json_defaults_true(self, db_session):
        """改造前落库的 JSON 没有 data_valid 键，读出应视为有效而非全 False"""
        from backend.routers.analysis import _result_to_out

        fund = await _mk_fund(db_session)
        row = AnalysisResult(
            fund_id=fund.id,
            analysis_date=date(2026, 1, 1), weighted_score=1.0,
            signal_direction="buy", signal_strength="moderate_buy",
            operation_advice="x", equity_ratio=0.5,
            factor_scores=json.dumps({"short_momentum": {"name": "短期动量", "score": 0.5}}),
        )
        db_session.add(row)
        await db_session.commit()

        out = _result_to_out(row, fund)
        assert out.factor_scores[0].data_valid is True


def test_quality_correction_preserves_data_valid():
    """inv_volatility 修正重建 FactorScoreResult 时不得把 data_valid 洗回 True"""
    from backend.engines.factor_engine import FactorScoreResult
    from backend.engines.quality_filter import apply_factor_corrections

    scores = [
        FactorScoreResult(
            "inv_volatility", "波动率倒数", 0.0, 0.4, "positive", data_valid=False
        )
    ]
    active = [{"code": "inv_volatility", "weight": 1.0}]
    corrected, _ = apply_factor_corrections(scores, active, 0.5, 0)
    assert corrected[0].data_valid is False
    assert corrected[0].score == pytest.approx(0.4 * (0.5 + 0.5 * 0.5))


class TestAIFactorContext:
    def test_dict_shape_is_parsed(self):
        """旧实现对 {code: {...}} 做 float(dict) → 恒抛 TypeError，因子明细从未进过提示词"""
        from backend.services.ai_service import top_factor_lines_for_prompt

        raw = {
            "short_momentum": {"name": "短期动量", "score": 0.2, "data_valid": True},
            "macd_signal": {"name": "MACD信号", "score": -0.9, "data_valid": False},
        }
        lines = top_factor_lines_for_prompt(raw)
        assert lines == ["MACD信号=-0.90(数据不足)", "短期动量=+0.20"]

    def test_legacy_shapes(self):
        from backend.services.ai_service import top_factor_lines_for_prompt

        assert top_factor_lines_for_prompt({"a": 0.3, "b": -0.5}) == ["b=-0.50", "a=+0.30"]
        listed = top_factor_lines_for_prompt([
            {"factor_code": "x", "factor_name": "X因子", "score": 0.7},
            {"factor_code": "y", "factor_name": "Y因子", "score": 0.1, "data_valid": False},
        ])
        assert listed == ["X因子=+0.70", "Y因子=+0.10(数据不足)"]

    def test_limit_and_garbage(self):
        from backend.services.ai_service import top_factor_lines_for_prompt

        raw = {f"f{i}": {"name": f"因子{i}", "score": i / 10} for i in range(12)}
        assert len(top_factor_lines_for_prompt(raw)) == 5
        assert top_factor_lines_for_prompt({"a": {"score": "not-a-number"}}) == []
        assert top_factor_lines_for_prompt(None) == []
