"""§3 影子评分层 + 口径分歧报表

裁定（2026-10-02）：**先影子，达标再切**。第二批 2C 那组改动会实质改变每日买卖数量，
而库里只有十来个信号日，改完无法证明变好 —— 所以新口径先只写 `analysis_results.shadow_*`，
生产列口径一字不动，分歧比例连续 5 个交易日 <15% 才谈切换。

本文件守的三件事：
1. 影子层**永不影响生产**：变体抛异常/注册表为空/开关关闭，都只表现为"这一行没有影子"；
2. 零上游请求：影子是纯 Python 加权，配置读取一轮一次 DB；
3. 分歧报表**只数有影子对照的行**，且把小样本、变体切换、覆盖率噪声如实写进 caveats。
"""

import json
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.data_sources.base import FundData
from backend.engines.factor_engine import FactorScoreResult
from backend.engines.quality_filter import QUALITY_CONFIG, QualityFilter
from backend.engines.shadow_scoring import (
    DEFAULT_SHADOW_ENABLED,
    SHADOW_ENABLED_CONFIG_KEY,
    SHADOW_VARIANT_CONFIG_KEY,
    ShadowContext, ShadowSignal, available_variants, compute_shadow, factor_coverage,
    load_shadow_config, register_variant, resolve_variant, save_shadow_config,
    unregister_variant, VARIANTS,
)
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.models.system_config import SystemConfig
import backend.services.analysis_service as asis
from backend.services.shadow_report_service import ShadowReportService, _bucket_of

TODAY = date(2026, 10, 2)


@pytest.fixture(autouse=True)
def _clean_variant_registry():
    """变体注册表隔离：用例里注册的桩变体不得泄漏到别的用例（模块级 dict）

    进入用例前也清空 —— 内置的 `caliber_2c` 在 import 时就注册了，而本文件测的是
    "注册表为空 / 由桩变体驱动"的层行为（空注册表、回落首个、开关关闭），
    留着一个真变体这些断言就全成了假阴性。注册表本身的内容由
    `tests/test_shadow_2c_variant.py` 针对真口径单独测。
    """
    from backend.engines import shadow_scoring as ss

    saved = dict(ss.VARIANTS)
    saved_order = list(available_variants())
    saved_desc = dict(ss.VARIANT_DESCRIPTIONS)
    ss.VARIANTS.clear()
    ss._VARIANT_ORDER.clear()
    ss.VARIANT_DESCRIPTIONS.clear()
    yield
    ss.VARIANTS.clear()
    ss.VARIANTS.update(saved)
    ss._VARIANT_ORDER[:] = saved_order
    ss.VARIANT_DESCRIPTIONS.clear()
    ss.VARIANT_DESCRIPTIONS.update(saved_desc)


def _ctx(**kw) -> ShadowContext:
    """一个能过类型检查的最小上下文（signal=None 时用哨兵，变体测试不消费它）"""
    from backend.engines.scoring_engine import SignalResult
    args = dict(
        fund_code="004011",
        factor_scores=[FactorScoreResult(factor_code="momentum", factor_name="动量",
                                         raw_value=0.5, score=0.5, direction="positive")],
        factor_weights=[1.0],
        active_factors=[{"code": "momentum", "weight": 1.0}],
        qf_result=None,
        thresholds_json="",
        quality_cfg={},
        signal=SignalResult(weighted_score=1.0, raw_score=1.0, signal_direction="buy",
                            signal_strength="moderate_buy", operation_advice="",
                            equity_ratio=0.5),
    )
    args.update(kw)
    return ShadowContext(**args)


# ═══════════════════════════════════════════════════════════════════════
# 1. 注册表与变体选择
# ═══════════════════════════════════════════════════════════════════════

class TestRegistry:
    def test_register_keeps_declaration_order(self):
        @register_variant("b_first")
        def _b(ctx):
            return None

        @register_variant("a_second")
        def _a(ctx):
            return None
        assert available_variants() == ["b_first", "a_second"]
        unregister_variant("b_first")
        assert available_variants() == ["a_second"]

    def test_same_name_overwrites(self):
        @register_variant("dup")
        def _v1(ctx):
            return None

        @register_variant("dup")
        def _v2(ctx):
            return None
        assert available_variants() == ["dup"]
        assert VARIANTS["dup"] is _v2

    def test_resolve_falls_back_to_first_registered(self):
        @register_variant("only")
        def _v(ctx):
            return None
        # 配置写错名字不能让整个影子层静默停摆：回落注册表首个，并把原值留在 requested_variant
        assert resolve_variant("typo_name") == "only"
        assert resolve_variant(None) == "only"
        assert resolve_variant("") == "only"

    def test_empty_registry_resolves_none(self):
        assert resolve_variant("whatever") is None
        assert available_variants() == []


# ═══════════════════════════════════════════════════════════════════════
# 2. factor_coverage（Q4 口径：有效权重和 / 总权重）
# ═══════════════════════════════════════════════════════════════════════

class TestFactorCoverage:
    def _fs(self, valid: bool, code: str = "f") -> FactorScoreResult:
        return FactorScoreResult(factor_code=code, factor_name=code, raw_value=0.1,
                                 score=0.2, direction="positive", data_valid=valid)

    def test_all_valid_is_one(self):
        scores = [self._fs(True, "a"), self._fs(True, "b")]
        assert factor_coverage(scores, [3.0, 2.0]) == 1.0

    def test_invalid_factor_discounts_by_weight(self):
        scores = [self._fs(True, "a"), self._fs(False, "b")]
        assert factor_coverage(scores, [3.0, 2.0]) == 0.6

    def test_zero_total_weight_is_none_not_zero(self):
        """总权重 0 = 配置坏了；覆盖 0% = 数据缺失。两者在报表里解释完全不同"""
        assert factor_coverage([self._fs(False)], [0.0]) is None
        assert factor_coverage([], []) is None

    def test_negative_weights_use_absolute(self):
        scores = [self._fs(True, "a"), self._fs(False, "b")]
        assert factor_coverage(scores, [-5.0, 5.0]) == 0.5

    def test_dirty_weight_skipped(self):
        scores = [self._fs(True, "a")]
        assert factor_coverage(scores, [1.0, "乱码", None]) == 1.0

    def test_missing_score_entry_counts_invalid(self):
        assert factor_coverage([], [1.0, 1.0]) == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 3. compute_shadow：三条"没有影子"的路径 + 生产隔离
# ═══════════════════════════════════════════════════════════════════════

class TestComputeShadow:
    def test_disabled_returns_none(self):
        @register_variant("v")
        def _v(ctx):
            return ShadowSignal(score=9.9, direction="buy")
        assert compute_shadow(_ctx(), enabled=False) == (None, None)

    def test_empty_registry_returns_none(self):
        assert compute_shadow(_ctx(), enabled=True) == (None, None)

    def test_variant_exception_is_swallowed(self):
        @register_variant("boom")
        def _v(ctx):
            raise RuntimeError("变体内部炸了")
        name, sig = compute_shadow(_ctx(), enabled=True, variant="boom")
        assert (name, sig) == (None, None)     # 只表现为"这一行没有影子"，不上抛

    def test_variant_returning_none_is_not_an_error(self):
        @register_variant("na")
        def _v(ctx):
            return None
        assert compute_shadow(_ctx(), enabled=True) == (None, None)

    def test_success_path(self):
        @register_variant("plus_one")
        def _v(ctx):
            return ShadowSignal(score=ctx.signal.weighted_score + 1.0, direction="sell",
                                strength="moderate_sell", detail={"why": "测试"})
        name, sig = compute_shadow(_ctx(), enabled=True)
        assert name == "plus_one"
        assert sig.score == 2.0 and sig.direction == "sell"
        assert sig.detail == {"why": "测试"}

    def test_variant_sees_shared_inputs(self):
        """变体拿到的是质量过滤修正后的同一份因子/权重/池大小 —— 2C 靠这些复算"""
        seen = {}

        @register_variant("spy")
        def _v(ctx):
            seen.update(codes=[f.factor_code for f in ctx.factor_scores],
                        weights=list(ctx.factor_weights), pool=ctx.pool_size,
                        thresholds=ctx.thresholds_json)
            return None
        compute_shadow(_ctx(factor_weights=[2.5], thresholds_json="[]", pool_size=42),
                       enabled=True)
        assert seen == {"codes": ["momentum"], "weights": [2.5], "pool": 42,
                        "thresholds": "[]"}


# ═══════════════════════════════════════════════════════════════════════
# 4. 配置读写（system_config KV，回滚只需 enabled=0）
# ═══════════════════════════════════════════════════════════════════════

class TestShadowConfig:
    @pytest.mark.asyncio
    async def test_defaults_without_rows(self, db_session):
        cfg = await load_shadow_config(db_session)
        assert cfg["enabled"] is DEFAULT_SHADOW_ENABLED
        assert cfg["registered"] == []
        assert cfg["variant"] == ""      # 注册表为空 = 没有可用口径，不假装在跑

    @pytest.mark.asyncio
    async def test_save_roundtrip(self, db_session):
        @register_variant("caliber_x")
        def _v(ctx):
            return None
        out = await save_shadow_config(db_session, enabled=False)
        assert out["enabled"] is False
        row = (await db_session.execute(
            select(SystemConfig).where(SystemConfig.config_key == SHADOW_ENABLED_CONFIG_KEY)
        )).scalars().one()
        assert row.config_value == "0"

        out = await save_shadow_config(db_session, enabled=True, variant="caliber_x")
        assert out["enabled"] is True and out["variant"] == "caliber_x"

    @pytest.mark.asyncio
    async def test_unregistered_variant_rejected(self, db_session):
        @register_variant("real")
        def _v(ctx):
            return None
        with pytest.raises(ValueError):
            await save_shadow_config(db_session, variant="ghost")
        # 拒绝后什么都没写
        assert (await db_session.execute(
            select(SystemConfig).where(SystemConfig.config_key == SHADOW_VARIANT_CONFIG_KEY)
        )).scalars().first() is None

    @pytest.mark.asyncio
    async def test_dirty_enabled_value_treated_as_on(self, db_session):
        """只有显式 0/false/off/no 才算关闭：脏值不能意外关掉防线之外的东西"""
        db_session.add(SystemConfig(config_key=SHADOW_ENABLED_CONFIG_KEY, config_value="ture"))
        await db_session.commit()
        assert (await load_shadow_config(db_session))["enabled"] is True


# ═══════════════════════════════════════════════════════════════════════
# 5. 落库链路：shadow_* / pool_size / factor_coverage 写入与清除
# ═══════════════════════════════════════════════════════════════════════

def _mk_analysis_cfg(shadow_enabled: bool, variant=None,
                     pool_size: int = 7, factors=None) -> asis._AnalysisConfig:
    active = factors if factors is not None else [
        {"code": "momentum", "name": "动量", "weight": 1.0,
         "direction": "positive", "params": "{}", "signal_rules": []}]
    return asis._AnalysisConfig(
        active_factors=active, thresholds_json="",
        qf=QualityFilter(config=dict(QUALITY_CONFIG)),
        regime_snapshot=None, regime_factors=active,
        shadow_enabled=shadow_enabled, shadow_variant=variant, pool_size=pool_size,
    )


def _fd_no_date(code: str) -> FundData:
    """无 as-of 日期的净值：Q9 不判新鲜度，两轮跑的输入完全一致，才能对比生产列"""
    hist = [1.0 + i * 0.01 for i in range(40)]
    return FundData(code=code, name=f"基金{code}", date="", close=hist[-1],
                    close_history=hist)


def _score() -> list[FactorScoreResult]:
    return [FactorScoreResult(factor_code="momentum", factor_name="动量",
                              raw_value=0.5, score=0.5, direction="positive")]


async def _mk_fund(db, code: str) -> Fund:
    f = Fund(code=code, name=f"基金{code}", fund_type="otc", status="active")
    db.add(f)
    await db.flush()
    return f


class TestPersistenceWiring:
    @pytest.mark.asyncio
    async def test_shadow_columns_written_and_production_untouched(
        self, db_session, monkeypatch
    ):
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)

        @register_variant("shift")
        def _v(ctx):
            return ShadowSignal(score=ctx.signal.weighted_score - 3.0,
                                direction="sell", detail={"delta": -3.0})

        # 同一只基金跑两轮：影子关闭 → 打开，生产列必须一字不差
        svc = asis.AnalysisService(db_session)
        fund_off = await _mk_fund(db_session, "000100")
        await svc._score_and_store(
            fund_off, _mk_analysis_cfg(False), _score(), _fd_no_date("000100"), [])
        await db_session.commit()
        row_off = (await db_session.execute(
            select(AnalysisResult).where(AnalysisResult.fund_id == fund_off.id)
        )).scalars().one()

        svc2 = asis.AnalysisService(db_session)
        fund_on = await _mk_fund(db_session, "000200")
        await svc2._score_and_store(
            fund_on, _mk_analysis_cfg(True, pool_size=12), _score(), _fd_no_date("000200"), [])
        await db_session.commit()
        row_on = (await db_session.execute(
            select(AnalysisResult).where(AnalysisResult.fund_id == fund_on.id)
        )).scalars().one()

        assert (row_off.weighted_score, row_off.signal_direction) == \
               (row_on.weighted_score, row_on.signal_direction)
        assert row_off.operation_advice == row_on.operation_advice
        assert row_off.shadow_direction is None and row_off.shadow_score is None

        assert row_on.shadow_variant == "shift"
        assert row_on.shadow_direction == "sell"
        assert row_on.shadow_score == pytest.approx(row_on.weighted_score - 3.0)
        assert json.loads(row_on.shadow_detail) == {"delta": -3.0}
        assert row_on.pool_size == 12
        assert row_on.factor_coverage == 1.0

    @pytest.mark.asyncio
    async def test_crashing_variant_leaves_production_row(self, db_session, monkeypatch):
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)

        @register_variant("boom")
        def _v(ctx):
            raise KeyError("因子缺失")

        svc = asis.AnalysisService(db_session)
        fund = await _mk_fund(db_session, "000300")
        out = await svc._score_and_store(
            fund, _mk_analysis_cfg(True), _score(), _fd_no_date("000300"), [])
        await db_session.commit()
        assert out is not None                      # 整轮分析不能被打挂
        row = (await db_session.execute(
            select(AnalysisResult).where(AnalysisResult.fund_id == fund.id)
        )).scalars().one()
        assert row.shadow_direction is None
        assert row.shadow_variant is None
        assert row.pool_size == 7                   # 元数据与影子成败无关，照常落库

    @pytest.mark.asyncio
    async def test_disabling_clears_stale_shadow_on_same_day(self, db_session, monkeypatch):
        """同日重跑：关掉开关后不能留着上一轮的影子冒充本轮对照"""
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)

        @register_variant("shift")
        def _v(ctx):
            return ShadowSignal(score=0.0, direction="hold")

        svc = asis.AnalysisService(db_session)
        fund = await _mk_fund(db_session, "000400")
        await svc._score_and_store(fund, _mk_analysis_cfg(True), _score(), _fd_no_date("000400"), [])
        await db_session.commit()
        await svc._score_and_store(fund, _mk_analysis_cfg(False), _score(), _fd_no_date("000400"), [])
        await db_session.commit()

        rows = (await db_session.execute(
            select(AnalysisResult).where(AnalysisResult.fund_id == fund.id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].shadow_direction is None and rows[0].shadow_score is None
        assert rows[0].shadow_detail is None

    @pytest.mark.asyncio
    async def test_coverage_reflects_missing_factor_data(self, db_session, monkeypatch):
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)
        svc = asis.AnalysisService(db_session)
        fund = await _mk_fund(db_session, "000500")
        factors = [
            {"code": "momentum", "name": "动量", "weight": 1.0, "direction": "positive",
             "params": "{}", "signal_rules": []},
            {"code": "valuation", "name": "估值", "weight": 1.0, "direction": "positive",
             "params": "{}", "signal_rules": []},
        ]
        scores = [
            FactorScoreResult(factor_code="momentum", factor_name="动量", raw_value=0.5,
                              score=0.5, direction="positive"),
            FactorScoreResult(factor_code="valuation", factor_name="估值", raw_value=0.0,
                              score=0.0, direction="positive", data_valid=False),
        ]
        await svc._score_and_store(
            fund, _mk_analysis_cfg(False, factors=factors), scores,
            _fd_no_date("000500"), [])
        await db_session.commit()
        row = (await db_session.execute(
            select(AnalysisResult).where(AnalysisResult.fund_id == fund.id)
        )).scalars().one()
        # 覆盖率按修正后权重算：两因子等权，一半缺数据 → 0.5
        assert row.factor_coverage == pytest.approx(0.5)


class TestPoolSizeFromRunPaths:
    """pool_size 必须来自真实截面样本数（跳过的那只不算），否则 Q5 标注是假的"""

    class _Engine:
        def __init__(self):
            pass

        def calculate_all(self, fund_data, factors):
            return _score()

        def normalize_cross_sectional(self, all_results, factors):
            return all_results

    class _Source:
        def __init__(self, codes):
            self._codes = set(codes)

        async def get_fund_data(self, code, fund_type=None):
            if code not in self._codes:
                raise RuntimeError("无此基金")
            return _fd_no_date(code)

    @pytest.mark.asyncio
    async def test_batch_path_sets_pool_size(self, db_session, monkeypatch):
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)
        monkeypatch.setattr(asis, "factor_engine", self._Engine())
        cfg = _mk_analysis_cfg(False, pool_size=None)
        monkeypatch.setattr(asis.AnalysisService, "_load_analysis_config",
                            lambda self: _const(cfg))

        svc = asis.AnalysisService(db_session)
        svc.data_source = self._Source(["000600", "000601"])
        seen: list[int] = []

        async def _cap(self, fund, c, **kw):
            seen.append(c.pool_size)
            return None
        monkeypatch.setattr(asis.AnalysisService, "_score_and_store", _cap)

        await _mk_fund(db_session, "000600")
        await _mk_fund(db_session, "000601")
        await svc.run_analysis()
        assert seen == [2, 2]

    @pytest.mark.asyncio
    async def test_streaming_path_counts_only_scored_funds(self, db_session, monkeypatch):
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)
        monkeypatch.setattr(asis, "factor_engine", self._Engine())
        cfg = _mk_analysis_cfg(False, pool_size=None)
        monkeypatch.setattr(asis.AnalysisService, "_load_analysis_config",
                            lambda self: _const(cfg))

        svc = asis.AnalysisService(db_session)
        svc.data_source = self._Source(["000700"])          # 000701 取数失败
        seen: list[int] = []

        async def _cap(self, fund, c, **kw):
            seen.append(c.pool_size)
            return None
        monkeypatch.setattr(asis.AnalysisService, "_score_and_store", _cap)

        await _mk_fund(db_session, "000700")
        await _mk_fund(db_session, "000701")
        async for _ in svc.run_analysis_streaming():
            pass
        assert seen == [1]


async def _const(value):
    return value


# ═══════════════════════════════════════════════════════════════════════
# 6. 分歧报表
# ═══════════════════════════════════════════════════════════════════════

# D1~D5 = 2026-09-21~09-25（周一~周五），D6 = 09-28（周一，跳过 09-26/27 周末）。
# 测试内存库的 `holiday_calendar` 为空，交易日按"周一至周五"退化口径判定，
# 所以这段日期在基础用例里是**连续的 6 个交易日**；真日历（中秋 9/25 休市）的影响
# 由 `TestStableDaysUseTradingCalendar` 用插行/不插行的对照专门守。
D1, D2, D3, D4, D5, D6 = [
    date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23),
    date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28),
]


async def _mk_row(db, code: str, d: date, old_dir: str, old_score: float,
                  new_dir=None, new_score=None, variant="caliber_2c",
                  original=None, buy_th=1.5, sell_th=-1.5, pool=30, cov=1.0):
    fund = await _mk_fund(db, code)
    row = AnalysisResult(
        fund_id=fund.id, analysis_date=d, weighted_score=old_score,
        signal_direction=old_dir, signal_strength="hold", operation_advice="",
        equity_ratio=0.5, factor_scores="{}",
        original_score=original, dynamic_buy_threshold=buy_th,
        dynamic_sell_threshold=sell_th, shadow_direction=new_dir,
        shadow_score=new_score, shadow_variant=variant if new_dir else None,
        pool_size=pool, factor_coverage=cov,
    )
    db.add(row)
    await db.flush()
    return row


class TestBucketOf:
    def test_thresholds_use_original_score(self):
        assert _bucket_of(2.0, 1.5, -1.5) == "above_buy"
        assert _bucket_of(-2.0, 1.5, -1.5) == "below_sell"
        assert _bucket_of(0.0, 1.5, -1.5) == "middle"

    def test_missing_any_input_is_unknown(self):
        assert _bucket_of(None, 1.5, -1.5) == "unknown"
        assert _bucket_of(1.0, None, -1.5) == "unknown"
        assert _bucket_of(1.0, 1.5, None) == "unknown"


class TestDivergenceReport:
    @pytest.mark.asyncio
    async def test_no_shadow_rows_explains_why(self, db_session):
        @register_variant("caliber_2c")
        def _v(ctx):
            return None
        fund = await _mk_fund(db_session, "001000")
        db_session.add(AnalysisResult(
            fund_id=fund.id, analysis_date=D1, weighted_score=1.0, signal_direction="buy",
            signal_strength="hold", operation_advice="", equity_ratio=0.5,
            factor_scores="{}"))
        await db_session.commit()

        rep = await ShadowReportService(db_session).build(days=10)
        assert rep["daily"] == []
        assert rep["meets_ratio_criterion"] is False
        assert any("已注册变体" in c for c in rep["caveats"])
        assert "尚未" not in " ".join(rep["caveats"])     # 有变体时不能说"新口径尚未实现"
        assert "尚无影子对照数据" in rep["conclusion"]

    @pytest.mark.asyncio
    async def test_empty_registry_reason_mentions_2c(self, db_session):
        rep = await ShadowReportService(db_session).build(days=10)
        assert any("2C" in c for c in rep["caveats"])
        assert rep["registered_variants"] == []

    @pytest.mark.asyncio
    async def test_null_shadow_rows_excluded_from_ratio(self, db_session):
        """没跑影子的行不能当"新口径也同意"，否则分歧比例被系统性稀释"""
        await _mk_row(db_session, "001100", D1, "buy", 2.0, "buy", 2.0)
        await _mk_row(db_session, "001101", D1, "buy", 2.0, "buy", 2.0)
        await _mk_row(db_session, "001102", D1, "buy", 2.0, None, None)   # 无对照
        await _mk_row(db_session, "001103", D1, "sell", -2.0, "hold", -0.2)
        await _mk_row(db_session, "001104", D1, "hold", 0.0, None, None)
        await db_session.commit()

        rep = await ShadowReportService(db_session).build(days=10)
        day = rep["daily"][0]
        assert day["rows"] == 5 and day["shadow_rows"] == 3
        assert day["divergent"] == 1
        assert day["divergence_pct"] == pytest.approx(33.3)
        assert day["migration"] == {"sell->hold": 1}
        assert day["low_sample"] is True
        assert any("小样本" in c or "< 10 只" in c for c in rep["caveats"])

    @pytest.mark.asyncio
    async def test_stable_days_counts_trailing_and_criterion(self, db_session):
        """连续达标只从最近一天往回数，且按交易日历走（判据一的定义）"""
        # D1~D3 全体分歧（100%），D4~D6 全体一致 → 尾部连续 3 个交易日
        for d, new_dir in [(D1, "hold"), (D2, "hold"), (D3, "hold"),
                           (D4, "buy"), (D5, "buy"), (D6, "buy")]:
            for i in range(12):                       # 每天 12 只，避免小样本噪声
                await _mk_row(db_session, f"{d.day:02d}1{i:03d}", d, "buy", 2.0,
                              new_dir, 2.0 if new_dir == "buy" else 0.1)
        await db_session.commit()

        rep = await ShadowReportService(db_session).build(days=10)
        assert [x["date"] for x in rep["daily"]] == [d.isoformat() for d in [D1, D2, D3, D4, D5, D6]]
        assert all(x["trading_day"] for x in rep["daily"])   # 全是工作日（退化口径）
        assert rep["summary"]["stable_days"] == 3
        assert rep["summary"]["non_trading_rounds"] == 0
        assert rep["meets_ratio_criterion"] is False
        assert "判据一未满足" in rep["conclusion"]

        # 再补 2 个一致日（9/29 周二、9/30 周三）→ 尾部连续 5 个交易日 → 判据一满足
        for j, d in enumerate([date(2026, 9, 29), date(2026, 9, 30)]):
            for i in range(12):
                await _mk_row(db_session, f"7{j}{i:03d}", d, "buy", 2.0, "buy", 2.0)
        await db_session.commit()
        rep2 = await ShadowReportService(db_session).build(days=10)
        # 9/26、9/27 是周末：夹在 D6=9/28 之前也不打断连续（日历相邻而非日期相邻）
        assert rep2["summary"]["stable_days"] == 5
        assert rep2["meets_ratio_criterion"] is True
        assert "判据一已满足" in rep2["conclusion"]

    @pytest.mark.asyncio
    async def test_window_limits_to_recent_dates_with_shadow(self, db_session):
        for i in range(8):
            d = date(2026, 9, 1) + timedelta(days=i)
            await _mk_row(db_session, f"0{i:04d}", d, "buy", 2.0, "buy", 2.0)
        await db_session.commit()
        rep = await ShadowReportService(db_session).build(days=3)
        assert len(rep["daily"]) == 3
        assert rep["daily"][-1]["date"] == date(2026, 9, 8).isoformat()

    @pytest.mark.asyncio
    async def test_bucket_and_variant_stats(self, db_session):
        await _mk_row(db_session, "002000", D1, "buy", 2.0, "hold", 0.5,
                      original=3.0, buy_th=1.5, sell_th=-1.5)
        await _mk_row(db_session, "002001", D1, "hold", 0.4, "hold", 0.3,
                      original=0.4, buy_th=1.5, sell_th=-1.5)
        await _mk_row(db_session, "002002", D1, "sell", -2.0, "hold", -0.1,
                      original=-3.0, buy_th=1.5, sell_th=-1.5, variant="old_variant")
        await _mk_row(db_session, "002003", D1, "hold", 0.0, "buy", 1.8,
                      original=None)                      # 旧行阈值缺失
        await db_session.commit()

        rep = await ShadowReportService(db_session).build(days=5)
        buckets = rep["buckets"]
        assert buckets["above_buy"]["rows"] == 1 and buckets["above_buy"]["divergent"] == 1
        assert buckets["above_buy"]["migration"] == {"buy->hold": 1}
        assert buckets["middle"]["divergent"] == 0
        assert buckets["below_sell"]["avg_delta"] == pytest.approx(1.9)
        assert buckets["unknown"]["rows"] == 1

        variants = rep["variants"]
        assert variants["caliber_2c"]["rows"] == 3
        assert variants["old_variant"]["rows"] == 1
        assert variants["old_variant"]["first_date"] == D1.isoformat()
        # 多口径混在一个窗口里不可比，必须提醒
        assert any("多个影子变体" in c for c in rep["caveats"])

    @pytest.mark.asyncio
    async def test_aggregate_migration_and_summary(self, db_session):
        await _mk_row(db_session, "003000", D1, "buy", 3.0, "hold", 1.0)
        await _mk_row(db_session, "003001", D1, "buy", 2.5, "sell", -2.5)
        await _mk_row(db_session, "003002", D1, "sell", -3.0, "hold", -1.0)
        await db_session.commit()

        rep = await ShadowReportService(db_session).build(days=5)
        s = rep["summary"]
        assert s["shadow_rows"] == 3 and s["divergent"] == 3
        assert s["divergence_pct"] == 100.0
        assert s["migration"]["buy->hold"] == 1
        assert s["buy_to_other"] == 2 and s["sell_to_other"] == 1
        # 平均分差 = mean(new-old)
        assert rep["daily"][0]["avg_delta"] == pytest.approx(
            round(((1.0 - 3.0) + (-2.5 - 2.5) + (-1.0 + 3.0)) / 3, 3))
        assert rep["daily"][0]["big_delta"] == 3     # 三行 |Δ| 都 >= 0.5

    @pytest.mark.asyncio
    async def test_zero_divergence_is_flagged(self, db_session):
        """全零分歧要么是巧合要么是空实现，报表要提示看变体明细"""
        for k in range(5):                    # 5 个交易日 × 4 只：刚好凑够判据一的连续天数
            d = D1 + timedelta(days=k)
            for i in range(4):
                await _mk_row(db_session, f"006{k}{i:03d}", d, "buy", 2.0, "buy", 2.0)
        await db_session.commit()
        rep = await ShadowReportService(db_session).build(days=10)
        assert any("零分歧" in c for c in rep["caveats"])
        assert any("小样本" in c or "< 10 只" in c for c in rep["caveats"])
        assert rep["summary"]["stable_days"] == 5
        assert rep["meets_ratio_criterion"] is True

    @pytest.mark.asyncio
    async def test_criterion_two_never_fabricated(self, db_session):
        """判据二（超额不劣于旧口径）本层给不出，报表必须明写需人工"""
        await _mk_row(db_session, "004000", D1, "buy", 2.0, "buy", 2.0)
        await db_session.commit()
        rep = await ShadowReportService(db_session).build(days=5)
        assert any("判据二" in c and "不自动判定" in c for c in rep["caveats"])

    @pytest.mark.asyncio
    async def test_days_param_is_bounded_and_tolerant(self, db_session):
        rep = await ShadowReportService(db_session).build(days=99999)
        assert rep["window_days"] == 60
        rep2 = await ShadowReportService(db_session).build(days="abc")   # type: ignore[arg-type]
        assert rep2["window_days"] == 10
        rep3 = await ShadowReportService(db_session).build(days=-5)
        assert rep3["window_days"] == 1


# ═══════════════════════════════════════════════════════════════════════
# 6b. 判据一的"连续 N 个交易日"必须按 A 股交易日历数
#     （2026-10-02 加固：休市日手点一轮也会落一行，旧实现按"有数据的日期"倒序计数，
#      连续 5 天可以全是周六 —— 攒样本这件事本身得先不能被刷）
# ═══════════════════════════════════════════════════════════════════════

async def _mk_day(db, d: date, prefix: str, n: int = 4, divergent: bool = False):
    """造一整轮：同一天 n 只基金，全体一致（divergent=False）或全体分歧"""
    for i in range(n):
        await _mk_row(
            db, f"{prefix}{d.strftime('%d')}{i:03d}", d, "buy", 2.0,
            "hold" if divergent else "buy", 0.1 if divergent else 2.0,
        )


async def _mk_off_day_rows(db, dates: list[date]):
    """往 holiday_calendar 落实休市日（is_off_day=True=休市；False=调休补班）"""
    from backend.models.holiday_calendar import HolidayCalendar

    for d in dates:
        db.add(HolidayCalendar(holiday_date=d.isoformat(), is_off_day=True,
                               holiday_name="测试休市"))
    await db.flush()


class TestStableDaysUseTradingCalendar:
    @pytest.mark.asyncio
    async def test_weekend_round_does_not_count_as_trading_day(self, db_session):
        """4 个达标交易日 + 1 个周六达标轮次 → 只算 4 天，且日表里如实标注非交易日"""
        for d in [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)]:
            await _mk_day(db_session, d, "w")
        await _mk_day(db_session, date(2026, 9, 26), "w")        # 周六手点一轮
        await db_session.commit()

        rep = await ShadowReportService(db_session).build(days=10)
        assert rep["summary"]["stable_days"] == 4
        assert rep["summary"]["non_trading_rounds"] == 1
        assert rep["meets_ratio_criterion"] is False
        by_date = {x["date"]: x for x in rep["daily"]}
        assert by_date["2026-09-26"]["trading_day"] is False      # 仍然展示，只是不进连续
        assert by_date["2026-09-24"]["trading_day"] is True
        assert any("非交易日" in c for c in rep["caveats"])

    @pytest.mark.asyncio
    async def test_holiday_calendar_is_consulted(self, db_session):
        """库里标了休市的工作日不能算交易日：同一批数据，插行前后结论必须不同"""
        for d in [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23),
                  date(2026, 9, 24), date(2026, 9, 25)]:
            await _mk_day(db_session, d, "h")
        await db_session.commit()

        base = await ShadowReportService(db_session).build(days=10)
        assert base["summary"]["stable_days"] == 5                 # 日历空 → 退化"周一至周五"

        await _mk_off_day_rows(db_session, [date(2026, 9, 25)])    # 2026 真日历：9/25 中秋休市
        await db_session.commit()
        rep = await ShadowReportService(db_session).build(days=10)
        assert rep["summary"]["stable_days"] == 4
        assert rep["summary"]["non_trading_rounds"] == 1
        assert {x["date"] for x in rep["daily"] if not x["trading_day"]} == {"2026-09-25"}

    @pytest.mark.asyncio
    async def test_missing_trading_day_breaks_streak(self, db_session):
        """漏跑一个真实交易日必须打断连续：9/21+9/22+9/24 读不成连续 3 天"""
        for d in [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 24)]:
            await _mk_day(db_session, d, "m")
        await db_session.commit()

        rep = await ShadowReportService(db_session).build(days=10)
        assert rep["summary"]["stable_days"] == 1

        await _mk_day(db_session, date(2026, 9, 23), "m")          # 补上缺的那天
        await db_session.commit()
        rep2 = await ShadowReportService(db_session).build(days=10)
        assert rep2["summary"]["stable_days"] == 4                 # 9/21~9/24 连成一片

    @pytest.mark.asyncio
    async def test_five_holiday_rounds_alone_never_satisfy_criterion(self, db_session):
        """判据一的最坏情形：五个轮次全是周末（长假期间天天手点）"""
        for k, d in enumerate([date(2026, 9, 12), date(2026, 9, 19), date(2026, 9, 26),
                               date(2026, 10, 3), date(2026, 10, 10)]):
            await _mk_day(db_session, d, "s")
        await db_session.commit()

        rep = await ShadowReportService(db_session).build(days=10)
        assert rep["summary"]["stable_days"] == 0
        assert rep["summary"]["non_trading_rounds"] == 5
        assert rep["meets_ratio_criterion"] is False
        assert all(not x["trading_day"] for x in rep["daily"])
        assert "判据一未满足" in rep["conclusion"]

    @pytest.mark.asyncio
    async def test_streak_reaching_window_edge_is_flagged(self, db_session):
        """days 截断了连续天数时报表要自认，不能把窗口内的 3 天当成真实连续 3 天"""
        for d in [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23),
                  date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28)]:
            await _mk_day(db_session, d, "e")
        await db_session.commit()

        short = await ShadowReportService(db_session).build(days=3)
        assert short["summary"]["stable_days"] == 3
        assert any("窗口边界" in c for c in short["caveats"])

        full = await ShadowReportService(db_session).build(days=10)
        assert full["summary"]["stable_days"] == 6
        assert not any("窗口边界" in c for c in full["caveats"])

    def test_calendar_helpers(self):
        """工具函数：周末/补班周六剔除，跨长假能回溯到上一个交易日"""
        from backend.services.shadow_report_service import (
            MAX_TRADING_BACKTRACK, is_a_share_trading_day, prev_trading_day,
        )

        national_day = frozenset(date(2026, 10, d) for d in range(1, 8))
        assert is_a_share_trading_day(date(2026, 10, 8), national_day) is True
        assert is_a_share_trading_day(date(2026, 10, 2), national_day) is False
        # 10/10 是调休补班周六：股市不开，`off_days` 里没有它也必须判休市（weekday 口径优先）
        assert is_a_share_trading_day(date(2026, 10, 10), frozenset()) is False
        assert prev_trading_day(date(2026, 10, 8), national_day) == date(2026, 9, 30)
        assert prev_trading_day(date(2026, 10, 12), frozenset()) == date(2026, 10, 9)
        # 日历异常（回溯上限内全是休市日）时返回 None，不无限回溯
        assert MAX_TRADING_BACKTRACK == 40
        all_off = frozenset(date(2026, 8, 1) + timedelta(days=k) for k in range(70))
        assert prev_trading_day(date(2026, 10, 8), all_off) is None


# ═══════════════════════════════════════════════════════════════════════
# 7. 端点
# ═══════════════════════════════════════════════════════════════════════

class TestEndpoints:
    @pytest.mark.asyncio
    async def test_get_shadow_config_exposes_criteria(self, db_session):
        from backend.routers.analysis import get_shadow_config
        res = await get_shadow_config(db=db_session)
        assert res.data["enabled"] is True
        assert res.data["divergence_threshold_pct"] == 15.0
        assert res.data["stable_days_required"] == 5

    @pytest.mark.asyncio
    async def test_put_rejects_unknown_variant(self, db_session):
        from backend.routers.analysis import update_shadow_config
        with pytest.raises(HTTPException) as ei:
            await update_shadow_config({"variant": "nope"}, db=db_session)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_put_toggle_roundtrip(self, db_session):
        from backend.routers.analysis import update_shadow_config, get_shadow_config
        res = await update_shadow_config({"enabled": "0"}, db=db_session)
        assert res.data["enabled"] is False
        res2 = await update_shadow_config({"enabled": True}, db=db_session)
        assert res2.data["enabled"] is True
        res3 = await get_shadow_config(db=db_session)
        assert res3.data["enabled"] is True

    @pytest.mark.asyncio
    async def test_divergence_endpoint(self, db_session):
        from backend.routers.analysis import get_shadow_divergence
        await _mk_row(db_session, "005000", D1, "buy", 2.0, "hold", 0.4)
        await db_session.commit()
        res = await get_shadow_divergence(days=5, db=db_session)
        assert res.data["summary"]["divergent"] == 1
        assert res.data["daily"][0]["migration"] == {"buy->hold": 1}
        assert res.data["daily"][0]["trading_day"] is True     # D1=周一，测试库日历空 ⇒ 退化口径算交易日

    def test_both_shadow_routes_trigger_builtin_registration(self):
        """两个影子端点都要自己负责触发内置变体注册

        注册表是 import 时填充的：进程刚起、前端直接打开分歧卡（没先读
        `/shadow-config`）时，不导入就会把已经实现的 2C 口径报成"注册表为空"，
        读数字的人会以为新口径根本没上线。判据可机检（同 Q13 的做法）。
        """
        import inspect

        from backend.routers.analysis import get_shadow_config, get_shadow_divergence
        for fn in (get_shadow_config, get_shadow_divergence):
            assert "shadow_variants" in inspect.getsource(fn), fn.__name__

