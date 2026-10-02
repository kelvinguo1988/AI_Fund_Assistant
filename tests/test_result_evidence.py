"""新行自证契约 — 2026-10-02 NAS 287 行历史取证的可机检版

取证事实（`/api/analysis/export`，20 个分析日 / 池 14 只）：
- `original_score` / `dynamic_*_threshold` / `quality_warnings` / `pool_size` /
  `factor_coverage` / `data_valid` 在全部历史行 **0/287 填充**；
- `factor_scores` 只存 name/raw_value/score/direction，**不带权重** → 用当前因子表
  重算 Σ(score×weight) 有 44 行恒差 **+0.3**，根因是趋势一致性在
  `excess_persistence==1 且 score>=1` 时被 boost 到 `trend_consistency_boost_weight=0.8`
  （`quality_filter.apply_factor_corrections`），这个修正项只活在内存里、从未落库；
- `market_valuation`（权重 0.8）215/215 行恒 0 分 = 死权重；无效代码 968049（四源无数据）
  却因"缺数据 0 值进截面排名"拿到过 moderate_buy 2.05。

因此本文件锁死一件事：**新落库的每一行必须自带可复算证据**（有效权重 + 地基列），
否则历史永远只能自证"文案与分数一致"，证不了"口径与配置一致"。
"""

import json
import re
from datetime import date

import pytest
from sqlalchemy import select

from backend.engines.factor_engine import FactorScoreResult
from backend.engines.quality_filter import QualityFilterResult, apply_factor_corrections
from backend.engines.scoring_engine import compute_with_quality_filter
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.services.analysis_service import AnalysisService

TREND_BOOST_WEIGHT = 0.8   # quality_filter.DEFAULT 里的 trend_consistency_boost_weight
TREND_BASE_WEIGHT = 0.5    # 因子表配置权重


def _factors():
    return [
        FactorScoreResult("trend_consistency", "趋势一致性", 1.0, 1.0, "positive"),
        FactorScoreResult("short_momentum", "短期动量", 0.11, 0.5, "positive"),
        FactorScoreResult(
            "market_valuation", "大盘估值分位", 0.0, 0.0, "negative", data_valid=False
        ),
    ]


def _active_factors():
    return [
        {"code": "trend_consistency", "weight": TREND_BASE_WEIGHT},
        {"code": "short_momentum", "weight": 1.2},
        {"code": "market_valuation", "weight": 0.8},
    ]


async def _mk_fund(db, code="004011"):
    fund = Fund(code=code, name="自证契约测试基金", fund_type="otc", status="active")
    db.add(fund)
    await db.flush()
    return fund


def _score_and_weights(excess_persistence: int):
    """走真实修正链，返回 (corrected_scores, corrected_weights, original_score)"""
    scores, weights = apply_factor_corrections(
        _factors(), _active_factors(), 1.0, excess_persistence
    )
    qf = QualityFilterResult(fund_code="004011")
    signal = compute_with_quality_filter(
        factor_scores=scores, factor_weights=weights, quality_result=qf
    )
    return scores, weights, signal


# ── ① 落库权重 = 实际生效权重，且行内可复算 ───────────────────────────

@pytest.mark.asyncio
async def test_stored_row_is_recomputable(db_session):
    """Σ(落库 score × 落库 weight) 必须等于落库 original_score（含 boost 那一项）"""
    fund = await _mk_fund(db_session)
    scores, weights, signal = _score_and_weights(excess_persistence=1)
    # 前置：修正链确实把趋势权重抬到 0.8
    assert weights[0] == pytest.approx(TREND_BOOST_WEIGHT)

    await AnalysisService(db_session)._save_result(
        fund, signal, scores, factor_weights=weights,
        extra_fields={"pool_size": 13, "factor_coverage": 0.77},
    )
    await db_session.commit()

    row = (await db_session.execute(select(AnalysisResult))).scalars().one()
    stored = json.loads(row.factor_scores)
    assert stored["trend_consistency"]["weight"] == pytest.approx(TREND_BOOST_WEIGHT), \
        "落库权重必须是修正后的生效权重，否则复算永远差 boost 那 0.3"
    assert stored["market_valuation"]["data_valid"] is False

    recomputed = round(sum(v["score"] * v["weight"] for v in stored.values()), 2)
    assert recomputed == pytest.approx(row.original_score, abs=0.011), (
        f"复算 {recomputed} ≠ 落库 original_score {row.original_score}："
        "权重/分值/原始分三者已不同代，该行不可自证"
    )
    # 地基列同样落库（历史上这五列全 NULL）
    assert (row.pool_size, row.factor_coverage) == (13, 0.77)
    assert row.dynamic_buy_threshold == pytest.approx(1.5)
    assert row.dynamic_sell_threshold == pytest.approx(-1.5)


@pytest.mark.asyncio
async def test_boost_is_not_persisted_when_condition_absent(db_session):
    """excess_persistence=0 时不得抬权重 —— 落库权重要如实反映本轮口径"""
    fund = await _mk_fund(db_session)
    scores, weights, signal = _score_and_weights(excess_persistence=0)
    assert weights[0] == pytest.approx(TREND_BASE_WEIGHT)

    await AnalysisService(db_session)._save_result(
        fund, signal, scores, factor_weights=weights
    )
    await db_session.commit()
    stored = json.loads((await db_session.execute(select(AnalysisResult))).scalars().one().factor_scores)
    assert stored["trend_consistency"]["weight"] == pytest.approx(TREND_BASE_WEIGHT)
    assert round(sum(v["score"] * v["weight"] for v in stored.values()), 2) == pytest.approx(
        signal.original_score, abs=0.011
    )


# ── ② 生产写入路径必须真的把权重传下去（源码级机检，防"纸面防线"）────

def test_scoring_path_passes_effective_weights_to_save():
    """`_score_and_store` → `_save_result` 必须带 factor_weights

    只测落库函数的话，调用方漏传参数照样红不了 —— 这一条锁的是链路本身。
    """
    import inspect
    import backend.services.analysis_service as mod

    src = inspect.getsource(mod.AnalysisService._score_and_store)
    call = re.search(r"return await self\._save_result\((.*?)\n        \)", src, re.S)
    assert call and "factor_weights=corrected_weights" in call.group(1), (
        "评分链路没把 corrected_weights 传给落库 → 行又变成不可复算的历史行"
    )


def test_evidence_fields_are_written_on_every_save():
    """地基字段与影子/池元数据不得再退回 NULL 写入路径"""
    import inspect
    import backend.services.analysis_service as mod

    body = inspect.getsource(mod.AnalysisService._save_result)
    for key in ("original_score", "dynamic_buy_threshold", "dynamic_sell_threshold",
                "quality_warnings", "\"weight\""):
        assert key in body, f"_save_result 不再写 {key} —— 历史行取证缺口重新出现"


# ── ③ 导出/导入回环与旧行兼容 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_import_roundtrip_keeps_weight(db_session):
    fund = await _mk_fund(db_session)
    scores, weights, signal = _score_and_weights(excess_persistence=1)
    svc = AnalysisService(db_session)
    await svc._save_result(fund, signal, scores, factor_weights=weights,
                           extra_fields={"pool_size": 13, "factor_coverage": 0.77})
    await db_session.commit()

    payload = await svc.export_analysis()
    item = payload.items[0]
    assert item.factor_scores["trend_consistency"]["weight"] == pytest.approx(TREND_BOOST_WEIGHT)

    # 覆盖导入后仍自证（回环不吞权重）
    row = (await db_session.execute(select(AnalysisResult))).scalars().one()
    row.factor_scores = json.dumps({"trend_consistency": {"score": 0}})
    await db_session.commit()
    imported = await svc.import_analysis(payload, overwrite=True)
    assert imported.updated == 1
    stored = json.loads((await db_session.execute(select(AnalysisResult))).scalars().one().factor_scores)
    assert stored["trend_consistency"]["weight"] == pytest.approx(TREND_BOOST_WEIGHT)


@pytest.mark.asyncio
async def test_legacy_row_without_weight_stays_readable(db_session):
    """NAS 上那 287 行没有 weight 键：读出必须是 None，不能补成 0 分假装能复算"""
    from backend.routers.analysis import _result_to_out

    fund = await _mk_fund(db_session, code="008715")
    legacy = {
        "trend_consistency": {"name": "趋势一致性", "raw_value": 1.0, "score": 1.0,
                              "direction": "positive"},
    }
    row = AnalysisResult(
        fund_id=fund.id, analysis_date=date(2026, 9, 24), weighted_score=4.55,
        signal_direction="buy", signal_strength="moderate_buy",
        operation_advice="综合评分 4.55（原始 4.55 + 偏置 +0.0）", equity_ratio=0.7,
        factor_scores=json.dumps(legacy),
    )
    db_session.add(row)
    await db_session.commit()

    out = _result_to_out(row, fund)
    assert out.factor_scores[0].weight is None
    assert out.original_score is None   # 旧行确实不可复算，报表据此标注
    # 缺样本数时口径文案必须说明"不能跨期比较"，而不是假装可比
    from backend.engines.scoring_engine import score_caliber_note
    assert "未记录" in score_caliber_note(out.pool_size)


# ── ④ 取证发现的两个死口径：市场因子恒 0 分、缺数据基金仍进截面排名 ──

def test_market_valuation_zero_score_is_flagged_not_silent():
    """215/215 历史行 market_valuation 恒 0 分：必须能被识别为「没参与评分」"""
    from backend.engines.shadow_scoring import factor_coverage

    scores, weights, _ = _score_and_weights(excess_persistence=1)
    cov = factor_coverage(scores, weights)
    # market_valuation data_valid=False → 覆盖率必须把它从分子剔掉
    assert cov == pytest.approx((TREND_BOOST_WEIGHT + 1.2) / (TREND_BOOST_WEIGHT + 1.2 + 0.8),
                                abs=5e-5)  # factor_coverage 落库保留 4 位小数
    assert cov < 1.0, "覆盖率恒 1 说明缺数据因子仍按 0 分占权重，死权重会被读成'正常中性'"
