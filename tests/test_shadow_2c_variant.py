"""2C 影子口径 `caliber_2c` 的逐项取证（§5.1 裁定：Q2+Q3+Q4+Q1 先入影子，生产不切）

这里的期望值全部**手算**：每个用例只动一条口径，算式写在断言注释里，
这样"实现和生产差多少"是可核对的数字，而不是跑出来的快照。

覆盖四件事：
- Q2 市场因子退出加权和，且参考权重**成对**下移（只清权重不改 ref = 换个后门降门槛）；
- Q3 趋势移出加权和改乘性位 + 簇上限（含"默认参数恰好不裁当前配置"这条压线事实）；
- Q4 覆盖率折算与 skip（缺数据的基金在新口径里应当**不出记录**，不是"观望"）；
- 红线：变体不得原地改与生产共用的因子输入，也不得发任何请求。
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import select

from backend.data_sources.base import FundData
from backend.engines.factor_engine import FactorScoreResult
from backend.engines.quality_filter import QUALITY_CONFIG, QualityFilter, QualityFilterResult
from backend.engines.scoring_engine import SignalResult
from backend.engines.shadow_scoring import (
    DIRECTION_SKIP, ShadowContext, VARIANTS, available_variants, compute_shadow,
    resolve_variant, variant_descriptions,
)
from backend.engines.shadow_variants import (
    DESCRIPTION, P_CLUSTER_CAP_BASE, P_CLUSTER_CAP_PCT, P_MIN_COVERAGE,
    P_TREND_FACTOR, TREND_FACTOR, VARIANT_NAME, caliber_2c, effective_params,
)
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
import backend.services.analysis_service as isis
from backend.routers.analysis import get_shadow_config
from backend.services.shadow_report_service import ShadowReportService

# 真实配置口径：11 因子总权重 8.3，市场三因子 1.8，动量簇（含趋势）3.4
UNIVERSE = [
    ("short_momentum", 1.2), ("mid_momentum", 1.2), ("momentum_accel", 0.5),
    (TREND_FACTOR, 0.5),
    ("inv_volatility", 1.0), ("return_risk_ratio", 0.8),
    ("drawdown_recovery", 0.8), ("macd_signal", 0.5),
    ("market_valuation", 0.8), ("market_sentiment", 0.5), ("market_fund_flow", 0.5),
]
UNIVERSE_TOTAL = 8.3
TODAY = date(2026, 10, 2)


def _items(codes_weights, scores=None, raws=None, invalid=()):
    """按 (code, weight) 列表造评分项；scores/raws 缺省为 1.0 / 0.5"""
    scores = scores or {}
    raws = raws or {}
    return [
        FactorScoreResult(
            factor_code=code, factor_name=code,
            raw_value=raws.get(code, 0.5),
            score=scores.get(code, 1.0),
            direction="positive",
            data_valid=code not in invalid,
        )
        for code, _ in codes_weights
    ]


def _ctx(items, weights, cfg=None, active=None, bias=0.0, buy_th=1.5, sell_th=-1.5,
         otc=None, drift=False, thresholds_json="", prod_original=None, pool=40):
    active = active if active is not None else [
        {"code": fs.factor_code, "weight": w} for fs, w in zip(items, weights)
    ]
    # 默认把 ref 设成配置总权重：structure 项恒为 1，用例就能只看被测的那一条口径
    merged = dict(QUALITY_CONFIG, threshold_ref_total_weight=sum(
        float(f.get("weight", 0.0)) for f in active))
    merged.update(cfg or {})
    qf = QualityFilterResult(fund_code="000001", institution_bias=bias,
                             dynamic_buy_threshold=buy_th, dynamic_sell_threshold=sell_th,
                             drift_triggered=drift)
    signal = SignalResult(weighted_score=0.0, raw_score=0.0, signal_direction="hold",
                          signal_strength="hold", operation_advice="", equity_ratio=0.5,
                          original_score=prod_original if prod_original is not None else 0.0)
    return ShadowContext(
        fund_code="000001", factor_scores=items, factor_weights=list(weights),
        active_factors=active, qf_result=qf, thresholds_json=thresholds_json,
        quality_cfg=merged, signal=signal, pool_size=pool, otc_status=otc,
    )


def _run(**kw):
    sig = caliber_2c(_ctx(**kw))
    assert sig is not None
    return sig


# ═══════════════════════════════════════════════════════════════════════
# 1. 注册：内置变体挂载即生效（§3 的"切"只剩改配置）
# ═══════════════════════════════════════════════════════════════════════

class TestRegistration:
    def test_imported_and_resolves_by_default(self):
        assert VARIANT_NAME in VARIANTS
        assert available_variants()[0] == VARIANT_NAME
        # 配置留空 = 用注册表首个，2C 就是当前的默认对照口径
        assert resolve_variant(None) == VARIANT_NAME
        assert resolve_variant("") == VARIANT_NAME

    def test_description_registered_for_ui(self):
        assert variant_descriptions()[VARIANT_NAME] == DESCRIPTION

    def test_compute_shadow_end_to_end(self):
        name, sig = compute_shadow(_ctx(items=_items(UNIVERSE),
                                        weights=[w for _, w in UNIVERSE]))
        assert name == VARIANT_NAME and sig is not None


# ═══════════════════════════════════════════════════════════════════════
# 2. Q2 市场三因子退出加权和 + 参考权重成对下移
# ═══════════════════════════════════════════════════════════════════════

class TestQ2MarketFactors:
    def test_fund_with_only_market_factors_has_no_shadow(self):
        """市场三因子是全池同分的常数：单独看它们新口径总分为 0；
        若一只基金的因子**只有**市场和趋势，新口径没有任何可比权重 → 不写影子列"""
        codes = [("market_valuation", 0.8), ("market_sentiment", 0.5),
                 ("market_fund_flow", 0.5), (TREND_FACTOR, 0.5)]
        assert caliber_2c(_ctx(items=_items(codes), weights=[0.8, 0.5, 0.5, 0.5])) is None

    def test_non_market_factors_unaffected(self):
        """短动量 1.0 分 × 权重 1.2 → 影子总分 1.2，市场侧不贡献"""
        codes = [("short_momentum", 1.2), ("market_valuation", 0.8)]
        sig = _run(items=_items(codes, scores={"short_momentum": 1.0,
                                               "market_valuation": 1.0}),
                   weights=[1.2, 0.8])
        assert sig.score == pytest.approx(1.2)
        assert sig.detail["contributions"]["market_valuation"] == pytest.approx(0.0)
        assert sig.detail["removed_weights"]["market"] == pytest.approx(0.8)

    def test_ref_moves_down_in_pair(self):
        """ref 成对下移：8.3 −（市场 1.8 + 趋势 0.5）= 6.0，与新口径总权重对齐"""
        sig = _run(items=_items(UNIVERSE), weights=[w for _, w in UNIVERSE],
                   cfg={"threshold_ref_total_weight": 8.3},
                   active=[{"code": c, "weight": w} for c, w in UNIVERSE])
        assert sig.detail["threshold_ref"] == {"prod": 8.3, "shadow": 6.0}
        assert sig.detail["total_weight"]["shadow"] == pytest.approx(6.0)
        # 数据齐全 + 未被簇上限裁到 ⇒ 门槛一字不动（分歧只来自口径，不来自门槛）
        assert sig.detail["threshold_structure"] == pytest.approx(1.0)
        assert sig.detail["threshold_factor"] == pytest.approx(1.0)

    def test_market_weights_come_from_config_not_corrections(self):
        """ref 下移量按**配置**权重记账：质量过滤改了工作权重也不串味"""
        codes = [("short_momentum", 1.2), ("market_valuation", 0.8)]
        items = _items(codes)
        sig = caliber_2c(_ctx(items=items, weights=[1.2, 2.0],          # 市场被抬到 2.0
                               active=[{"code": c, "weight": w} for c, w in codes],
                               cfg={"threshold_ref_total_weight": 2.0}))
        # 移出量按配置 0.8 记账，而不是按工作权重 2.0
        assert sig.detail["removed_weights"]["market"] == pytest.approx(0.8)


# ═══════════════════════════════════════════════════════════════════════
# 3. Q3 趋势移出加权和 → 乘性位；动量簇上限
# ═══════════════════════════════════════════════════════════════════════

class TestQ3TrendGate:
    def test_trend_excluded_from_weighted_sum(self):
        codes = [("short_momentum", 1.0), (TREND_FACTOR, 0.5)]
        sig = _run(items=_items(codes), weights=[1.0, 0.5])
        assert sig.score == pytest.approx(1.0)      # 旧口径会是 1.5
        assert sig.detail["removed_weights"]["trend_consistency"] == pytest.approx(0.5)

    def test_sign_disagreement_multiplies_score(self):
        """趋势 raw=0（mom20 与 mom60 反号）→ 动量部分 ×0.8：−0.6 → −0.48"""
        codes = [("short_momentum", 1.0), (TREND_FACTOR, 0.5)]
        items = _items(codes, scores={"short_momentum": -0.6, TREND_FACTOR: 0.0},
                       raws={TREND_FACTOR: 0.0})
        sig = _run(items=items, weights=[1.0, 0.5],
                   cfg={P_CLUSTER_CAP_PCT: 0.0, "threshold_ref_total_weight": 0.0})
        assert sig.score == pytest.approx(-0.48)
        assert sig.detail["trend_gate"] == {
            "applied": True, "factor": 0.8, "raw_value": 0.0,
            "scope": ["mid_momentum", "momentum_accel", "short_momentum"],
            "momentum_sum": -0.6,
        }

    def test_gate_only_reaches_momentum_cluster(self):
        """乘性位只收动量簇：波动率/绝对分不该被趋势打架牵连（否则分歧归因分不清是谁干的）"""
        codes = [("short_momentum", 1.0), ("inv_volatility", 1.0), (TREND_FACTOR, 0.5)]
        items = _items(codes, scores={"short_momentum": -0.6, "inv_volatility": 1.0},
                       raws={TREND_FACTOR: 0.0})
        sig = _run(items=items, weights=[1.0, 1.0, 0.5],
                   cfg={P_CLUSTER_CAP_PCT: 0.0, "threshold_ref_total_weight": 0.0})
        # 动量 −0.6×0.8 = −0.48，波动率 +1.0 原样 → 0.52
        assert sig.score == pytest.approx(0.52)
        assert sig.detail["contributions"]["inv_volatility"] == 1.0
        assert sig.detail["contributions"]["short_momentum"] == pytest.approx(-0.48)

    def test_missing_trend_data_is_not_punished(self):
        """data_valid=False 时 raw 同样是 0.0：缺数据的新基金不能被乘性位惩罚"""
        codes = [("short_momentum", 1.0), (TREND_FACTOR, 0.5)]
        items = _items(codes, scores={"short_momentum": -0.6},
                       raws={TREND_FACTOR: 0.0}, invalid={TREND_FACTOR})
        sig = _run(items=items, weights=[1.0, 0.5],
                   cfg={P_CLUSTER_CAP_PCT: 0.0, "threshold_ref_total_weight": 0.0})
        assert sig.score == pytest.approx(-0.6)
        assert sig.detail["trend_gate"]["applied"] is False

    def test_trend_aligned_does_not_boost(self):
        """乘性位只收不加：符号一致（raw=+1）时分值不变"""
        codes = [("short_momentum", 1.0), (TREND_FACTOR, 0.5)]
        items = _items(codes, scores={"short_momentum": -0.6}, raws={TREND_FACTOR: 1.0})
        sig = _run(items=items, weights=[1.0, 0.5],
                   cfg={P_CLUSTER_CAP_PCT: 0.0, "threshold_ref_total_weight": 0.0})
        assert sig.score == pytest.approx(-0.6)

    def test_gate_disabled_by_param(self):
        for gate in (0.0, 1.0, 2.0):
            codes = [("short_momentum", 1.0), (TREND_FACTOR, 0.5)]
            items = _items(codes, scores={"short_momentum": -0.6}, raws={TREND_FACTOR: 0.0})
            sig = _run(items=items, weights=[1.0, 0.5],
                       cfg={P_CLUSTER_CAP_PCT: 0.0, P_TREND_FACTOR: gate,
                            "threshold_ref_total_weight": 0.0})
            assert sig.score == pytest.approx(-0.6), gate


class TestQ3ClusterCap:
    def test_cap_trims_cluster_proportionally(self):
        """上限 = 50% × 4.0 = 2.0：动量簇 4.0 裁到 2.0，每只权重减半"""
        codes = [("short_momentum", 2.0), ("mid_momentum", 2.0),
                 ("inv_volatility", 1.0)]
        sig = _run(items=_items(codes), weights=[2.0, 2.0, 1.0],
                   cfg={P_CLUSTER_CAP_PCT: 50.0, P_CLUSTER_CAP_BASE: 4.0,
                        "threshold_ref_total_weight": 0.0})
        # 动量 2.0（裁后）+ 波动率簇 1.0（未触线）= 3.0，全部 score=1.0
        assert sig.score == pytest.approx(3.0)
        assert sig.detail["cluster_cap"]["applied"] == {
            "momentum": {"before": 4.0, "after": 2.0}}
        assert sig.detail["total_weight"]["shadow"] == pytest.approx(3.0)

    def test_default_cap_does_not_trim_current_config(self):
        """压线事实：默认 35% × 8.3 = 2.905，趋势移出后动量簇只剩 2.9 → 不裁。
        簇上限是结构性防线（防止后续把 short/mid 加权调大），不是当前调参工具。"""
        sig = _run(items=_items(UNIVERSE), weights=[w for _, w in UNIVERSE])
        assert sig.detail["cluster_cap"]["applied"] is None
        assert sig.detail["total_weight"]["shadow"] == pytest.approx(6.0)

    def test_cap_pct_zero_disables(self):
        codes = [("short_momentum", 2.0), ("mid_momentum", 2.0)]
        sig = _run(items=_items(codes), weights=[2.0, 2.0],
                   cfg={P_CLUSTER_CAP_PCT: 0.0, "threshold_ref_total_weight": 0.0})
        assert sig.detail["cluster_cap"]["applied"] is None
        assert sig.score == pytest.approx(4.0)

    def test_cap_binding_lifts_no_bar(self):
        """簇上限裁掉的权重同样从 ref 扣除：门槛不因裁剪而收紧（structure 仍 1）"""
        codes = [("short_momentum", 2.0), ("mid_momentum", 2.0)]
        sig = _run(items=_items(codes), weights=[2.0, 2.0],
                   active=[{"code": c, "weight": 2.0} for c, _ in codes],
                   cfg={P_CLUSTER_CAP_PCT: 50.0, P_CLUSTER_CAP_BASE: 8.0})   # cap=4.0 不裁
        assert sig.detail["threshold_structure"] == pytest.approx(1.0)
        codes2 = [("short_momentum", 3.0), ("mid_momentum", 3.0)]
        sig2 = _run(items=_items(codes2), weights=[3.0, 3.0],
                    active=[{"code": c, "weight": 3.0} for c, _ in codes2],
                    cfg={P_CLUSTER_CAP_PCT: 50.0, P_CLUSTER_CAP_BASE: 8.0})   # cap=4.0，裁掉 2.0
        # 总权重 6.0 → 4.0，ref 6.0 → 4.0：structure 保持 1，分数按 4.0 计
        assert sig2.detail["cluster_cap"]["applied"]["momentum"] == {
            "before": 6.0, "after": 4.0}
        assert sig2.score == pytest.approx(4.0)
        assert sig2.detail["threshold_structure"] == pytest.approx(1.0)


# ═══════════════════════════════════════════════════════════════════════
# 4. Q4 覆盖率折算与 skip
# ═══════════════════════════════════════════════════════════════════════

class TestQ4Coverage:
    def test_cleared_weights_do_not_dilute_coverage(self):
        """被清零的市场/趋势不占分母：市场因子缺数据不该拉低新口径覆盖率"""
        codes = [("short_momentum", 2.0), ("market_valuation", 0.8)]
        items = _items(codes, invalid={"market_valuation"})
        sig = _run(items=items, weights=[2.0, 0.8])
        assert sig.detail["coverage"]["value"] == pytest.approx(1.0)
        assert sig.direction == "buy"

    def test_half_missing_data_halves_thresholds(self):
        """两因子等权、其一缺数据 → 覆盖率 0.5 → 买入阈值 1.5→0.75、卖出 −1.5→−0.75"""
        codes = [("macd_signal", 1.0), ("drawdown_recovery", 1.0)]
        items = _items(codes, scores={"macd_signal": 0.8, "drawdown_recovery": 0.0},
                       invalid={"drawdown_recovery"})
        sig = _run(items=items, weights=[1.0, 1.0], buy_th=1.5, sell_th=-1.5,
                   cfg={P_MIN_COVERAGE: 0.4})
        assert sig.detail["coverage"]["value"] == pytest.approx(0.5)
        assert sig.detail["thresholds"]["shadow"] == [0.75, -0.75]
        # 0.8 ≥ 0.75 → 新口径喊买（旧口径 0.8 < 1.5 只能观望）
        assert sig.direction == "buy"

    def test_low_coverage_yields_skip_not_hold(self):
        """覆盖率 0.4 < 下限 0.6 → 不出方向：新口径下这只基金本轮不落库"""
        codes = [("macd_signal", 1.0), ("drawdown_recovery", 1.0)]
        items = _items(codes, scores={"macd_signal": 0.8, "drawdown_recovery": 0.0},
                       invalid={"drawdown_recovery"}, raws={"macd_signal": 0.5})
        sig = _run(items=items, weights=[1.0, 1.5], cfg={P_MIN_COVERAGE: 0.6})
        # 有效 1.0 / 总 2.5 = 0.4
        assert sig.detail["coverage"]["value"] == pytest.approx(0.4)
        assert sig.direction == DIRECTION_SKIP and sig.strength == DIRECTION_SKIP
        assert sig.detail["coverage"]["rejected"] is True
        assert "不落库" in sig.detail["coverage"]["note"]

    def test_min_coverage_zero_disables_converstion_and_skip(self):
        codes = [("macd_signal", 1.0), ("drawdown_recovery", 1.5)]
        items = _items(codes, scores={"macd_signal": 0.8}, invalid={"drawdown_recovery"})
        sig = _run(items=items, weights=[1.0, 1.5],
                   cfg={P_MIN_COVERAGE: 0.0, "threshold_ref_total_weight": 0.0})
        assert sig.detail["threshold_factor"] == pytest.approx(1.0)
        assert sig.direction == "buy"
        assert "关闭" in sig.detail["coverage"]["note"]

    def test_ref_disabled_branch_falls_back_to_coverage(self):
        """ref 机制未配置时只按覆盖率折算（否则两口径的差里混进"参考权重没开"）"""
        codes = [("macd_signal", 1.0), ("drawdown_recovery", 1.0)]
        items = _items(codes, scores={"macd_signal": 0.2}, invalid={"drawdown_recovery"})
        sig = _run(items=items, weights=[1.0, 1.0],
                   cfg={"threshold_ref_total_weight": 0.0, P_MIN_COVERAGE: 0.4})
        assert sig.detail["threshold_factor"] == pytest.approx(0.5)
        assert sig.detail["threshold_structure"] is None

    def test_full_universe_has_unit_factor(self):
        sig = _run(items=_items(UNIVERSE), weights=[w for _, w in UNIVERSE])
        assert sig.detail["threshold_factor"] == pytest.approx(1.0)
        assert sig.detail["thresholds"]["shadow"] == sig.detail["thresholds"]["prod"]


# ═══════════════════════════════════════════════════════════════════════
# 5. Q1 五档：只渲染措辞，与动态方向的冲突要留痕
# ═══════════════════════════════════════════════════════════════════════

class TestQ1Tier:
    def _one(self, score, buy_th=1.5):
        codes = [("short_momentum", 1.0)]
        return _run(items=_items(codes, scores={"short_momentum": score}),
                    weights=[1.0], buy_th=buy_th, sell_th=-1.5,
                    cfg={P_CLUSTER_CAP_PCT: 0.0, "threshold_ref_total_weight": 0.0})

    def test_tier_renders_label_and_advice(self):
        tier = self._one(2.0).detail["tier"]
        assert tier["label"] == "适度加仓"
        assert "2.0" in tier["advice"] and "70%" in tier["advice"]

    def test_direction_above_tier_wins_and_conflict_is_recorded(self):
        """分数 2.0 但买入阈值 3.0：动态判观望、档位说加仓 → 冲突必须留痕（Q1-B）"""
        sig = self._one(2.0, buy_th=3.0)
        assert sig.direction == "hold"
        assert sig.detail["tier"]["label"] == "适度加仓"
        assert sig.detail["tier"]["conflict_with_dynamic"] is True

    def test_no_conflict_when_agreeing(self):
        assert self._one(2.0).detail["tier"]["conflict_with_dynamic"] is False


# ═══════════════════════════════════════════════════════════════════════
# 6. 可执行性降级 / 偏置 / 钳位 / 输入不可变
# ═══════════════════════════════════════════════════════════════════════

class TestExecutionAndHygiene:
    def test_paused_subscription_downgrades_shadow_buy(self):
        # 新口径也喊买（2.0 ≥ 1.5）但场外暂停申购 → 记成 hold：
        # 不能把这条分歧洗成"两口径一致"，生产那边同样会降级，两边都降级才是真一致
        codes = [("short_momentum", 2.0)]
        sig = _run(items=_items(codes), weights=[2.0],
                   otc={"purchase": "暂停申购"},
                   cfg={P_CLUSTER_CAP_PCT: 0.0, "threshold_ref_total_weight": 0.0})
        assert sig.direction == "hold"
        assert sig.strength == "hold"
        assert sig.detail["otc_downgraded"] is True

    def test_limit_large_keeps_buy(self):
        codes = [("short_momentum", 2.0)]
        sig = _run(items=_items(codes), weights=[2.0], otc={"purchase": "限大额"},
                   cfg={P_CLUSTER_CAP_PCT: 0.0, "threshold_ref_total_weight": 0.0})
        assert sig.direction == "buy"
        assert sig.detail["otc_downgraded"] is False

    def test_no_otc_status_keeps_buy(self):
        codes = [("short_momentum", 2.0)]
        sig = _run(items=_items(codes), weights=[2.0],
                   cfg={P_CLUSTER_CAP_PCT: 0.0, "threshold_ref_total_weight": 0.0})
        assert sig.direction == "buy"
        assert sig.detail["otc_downgraded"] is False

    def test_institution_bias_added_after_sum(self):
        codes = [("short_momentum", 1.0)]
        sig = _run(items=_items(codes), weights=[1.0], bias=0.5,
                   cfg={P_CLUSTER_CAP_PCT: 0.0, "threshold_ref_total_weight": 0.0})
        assert sig.detail["score"]["shadow_original"] == pytest.approx(1.0)
        assert sig.score == pytest.approx(1.5)
        assert sig.detail["score"]["bias"] == pytest.approx(0.5)

    def test_score_clamped_like_production(self):
        codes = [("short_momentum", 10.0), ("mid_momentum", 10.0)]
        sig = _run(items=_items(codes), weights=[10.0, 10.0],
                   cfg={P_CLUSTER_CAP_PCT: 0.0, "threshold_ref_total_weight": 0.0})
        assert sig.score == pytest.approx(8.5)

    def test_shared_inputs_not_mutated(self):
        """与生产共用同一份因子对象：原地改权重就是把影子泄漏进生产"""
        items = _items(UNIVERSE)
        weights = [w for _, w in UNIVERSE]
        before_items = [(f.factor_code, f.score, f.raw_value, f.data_valid) for f in items]
        caliber_2c(_ctx(items=items, weights=weights))
        assert weights == [w for _, w in UNIVERSE]
        assert [(f.factor_code, f.score, f.raw_value, f.data_valid) for f in items] == before_items

    def test_empty_scores_returns_none(self):
        assert caliber_2c(_ctx(items=[], weights=[])) is None

    def test_detail_is_json_serialisable(self):
        """落库前 json.dumps —— 不能因为一个 numpy 值把整行影子打掉"""
        sig = _run(items=_items(UNIVERSE), weights=[w for _, w in UNIVERSE])
        assert json.loads(json.dumps(sig.detail, ensure_ascii=False))["variant_desc"]


# ═══════════════════════════════════════════════════════════════════════
# 7. 参数越界钳位（配置写错不能让口径静默变形）
# ═══════════════════════════════════════════════════════════════════════

class TestParamClamping:
    def test_out_of_range_pct_clamped(self):
        codes = [("short_momentum", 1.0)]
        sig = _run(items=_items(codes), weights=[1.0],
                   cfg={P_CLUSTER_CAP_PCT: 500.0, "threshold_ref_total_weight": 0.0})
        assert sig.detail["params"][P_CLUSTER_CAP_PCT] == 100.0
        assert sig.detail["params_clamped"][0]["key"] == P_CLUSTER_CAP_PCT
        assert sig.detail["params_clamped"][0]["raw"] == 500.0

    def test_negative_min_coverage_clamped_to_zero(self):
        codes = [("short_momentum", 1.0)]
        sig = _run(items=_items(codes), weights=[1.0],
                   cfg={P_MIN_COVERAGE: -3.0, "threshold_ref_total_weight": 0.0})
        assert sig.detail["params"][P_MIN_COVERAGE] == 0.0

    def test_effective_params_reports_flags(self):
        params = effective_params({P_CLUSTER_CAP_PCT: 999.0})
        by_key = {p["key"]: p for p in params}
        assert by_key[P_CLUSTER_CAP_PCT]["out_of_range"] is True
        assert by_key[P_CLUSTER_CAP_PCT]["value"] == 100.0
        assert by_key[P_CLUSTER_CAP_PCT]["default"] == pytest.approx(35.0)
        assert by_key[P_TREND_FACTOR]["governs"] == "Q3 趋势乘性位"


# ═══════════════════════════════════════════════════════════════════════
# 8. 落库与报表：真变体跑通链路，skip 行进分歧比例
# ═══════════════════════════════════════════════════════════════════════

async def _mk_fund(db, code: str) -> Fund:
    f = Fund(code=code, name=f"基金{code}", fund_type="otc", status="active")
    db.add(f)
    await db.flush()
    return f


async def _mk_row(db, code, d, old_dir, old_score, new_dir, new_score, cov=1.0):
    fund = await _mk_fund(db, code)
    db.add(AnalysisResult(
        fund_id=fund.id, analysis_date=d, weighted_score=old_score,
        signal_direction=old_dir, signal_strength="hold", operation_advice="",
        equity_ratio=0.5, factor_scores="{}", original_score=old_score,
        dynamic_buy_threshold=1.5, dynamic_sell_threshold=-1.5,
        shadow_variant=VARIANT_NAME if new_dir else None,
        shadow_direction=new_dir, shadow_score=new_score, factor_coverage=cov,
        pool_size=20,
    ))
    await db.flush()


class TestProductionIsolation:
    """红线取证：真 2C 变体挂上之后，生产列必须与"影子关闭"那一轮一字不差

    这是 §3 唯一不能靠读代码相信的命题 —— 变体和生产共用同一份 corrected 因子对象，
    任何原地修改都会顺着 `_save_result` 写进生产列。
    """

    PROD_COLUMNS = ("weighted_score", "signal_direction", "signal_strength",
                    "operation_advice", "equity_ratio", "original_score",
                    "dynamic_buy_threshold", "dynamic_sell_threshold",
                    "factor_coverage", "quality_warnings")

    def _cfg(self, shadow_enabled: bool) -> isis._AnalysisConfig:
        active = [{"code": code, "name": code, "weight": w, "direction": "positive",
                   "params": "{}", "signal_rules": []} for code, w in UNIVERSE]
        return isis._AnalysisConfig(
            active_factors=active, thresholds_json="",
            qf=QualityFilter(config=dict(QUALITY_CONFIG, threshold_ref_total_weight=8.3)),
            regime_snapshot=None, regime_factors=active,
            shadow_enabled=shadow_enabled,
            shadow_variant=VARIANT_NAME if shadow_enabled else None,
            pool_size=len(active),
        )

    @pytest.mark.asyncio
    async def test_real_variant_writes_shadow_only(self, db_session, monkeypatch):
        monkeypatch.setattr(isis, "beijing_today", lambda: TODAY)
        hist = [1.0 + i * 0.01 for i in range(80)]

        async def _run_once(code: str, enabled: bool):
            fund = await _mk_fund(db_session, code)
            scores = [FactorScoreResult(factor_code=c, factor_name=c, raw_value=0.5,
                                        score=1.0, direction="positive")
                      for c, _ in UNIVERSE]
            await isis.AnalysisService(db_session)._score_and_store(
                fund, self._cfg(enabled), scores,
                FundData(code=code, name=f"基金{code}", date="", close=hist[-1],
                         close_history=hist), [])
            await db_session.commit()
            return (await db_session.execute(
                select(AnalysisResult).where(AnalysisResult.fund_id == fund.id)
            )).scalars().one()

        off = await _run_once("003000", False)
        on = await _run_once("003001", True)

        assert {c: getattr(off, c) for c in self.PROD_COLUMNS} == \
               {c: getattr(on, c) for c in self.PROD_COLUMNS}
        # 关掉影子时六键显式 NULL，不能留上一轮的对照
        assert off.shadow_direction is None and off.shadow_variant is None
        assert on.shadow_variant == VARIANT_NAME
        # 全池满格的极端输入：市场 1.8 + 趋势 0.5 不进加权和 → 8.3 变 6.0
        assert on.shadow_score == pytest.approx(6.0)
        assert on.shadow_direction == off.signal_direction
        detail = json.loads(on.shadow_detail)
        assert detail["removed_weights"] == {"market": 1.8, "trend_consistency": 0.5}
        assert detail["pool_size"] == 11


class TestReportWithRealVariant:
    @pytest.mark.asyncio
    async def test_skip_rows_counted_and_called_out(self, db_session):
        d = TODAY
        # 4 行：1 行翻转、1 行 skip（也算分歧）、2 行一致
        await _mk_row(db_session, "002000", d, "buy", 2.0, "hold", 0.4)
        await _mk_row(db_session, "002001", d, "hold", 0.3, DIRECTION_SKIP, 0.3, cov=0.4)
        await _mk_row(db_session, "002002", d, "hold", 0.2, "hold", 0.2)
        await _mk_row(db_session, "002003", d, "sell", -2.0, "sell", -2.0)
        await db_session.commit()

        rep = await ShadowReportService(db_session).build(days=10)
        day = rep["daily"][0]
        assert day["shadow_rows"] == 4
        assert day["divergent"] == 2
        assert day["skip_rows"] == 1
        assert day["divergence_pct"] == pytest.approx(50.0)
        assert any("skip" in c and "记录消失" in c for c in rep["caveats"])

    @pytest.mark.asyncio
    async def test_no_skip_rows_no_skip_caveat(self, db_session):
        await _mk_row(db_session, "002100", TODAY, "buy", 2.0, "buy", 2.0)
        await db_session.commit()
        rep = await ShadowReportService(db_session).build(days=10)
        assert rep["daily"][0]["skip_rows"] == 0
        assert not any("记录消失" in c for c in rep["caveats"])


class TestEndpointSurfacing:
    @pytest.mark.asyncio
    async def test_shadow_config_lists_variant_and_params(self, db_session):
        resp = await get_shadow_config(db=db_session)
        data = resp.data
        assert VARIANT_NAME in data["registered"]
        assert data["variant"] == VARIANT_NAME
        assert data["variant_descriptions"][VARIANT_NAME] == DESCRIPTION
        keys = {p["key"] for p in data["caliber_params"]}
        assert keys == {P_CLUSTER_CAP_PCT, P_CLUSTER_CAP_BASE, P_TREND_FACTOR, P_MIN_COVERAGE}
        by_key = {p["key"]: p for p in data["caliber_params"]}
        assert by_key[P_CLUSTER_CAP_PCT]["value"] == pytest.approx(35.0)
        assert by_key[P_MIN_COVERAGE]["governs"].startswith("Q4")

    @pytest.mark.asyncio
    async def test_empty_registry_reason_says_2c_not_loaded(self, db_session):
        """注册表空时的解释不能停在"尚未实现" —— 2C 已内置，空表意味着导入链没走到"""
        from backend.engines import shadow_scoring as ss
        saved = (dict(ss.VARIANTS), list(ss._VARIANT_ORDER), dict(ss.VARIANT_DESCRIPTIONS))
        ss.VARIANTS.clear(); ss._VARIANT_ORDER.clear(); ss.VARIANT_DESCRIPTIONS.clear()
        try:
            await _mk_fund(db_session, "002200")
            db_session.add(AnalysisResult(
                fund_id=1, analysis_date=TODAY, weighted_score=1.0,
                signal_direction="buy", signal_strength="hold", operation_advice="",
                equity_ratio=0.5, factor_scores="{}"))
            await db_session.commit()
            rep = await ShadowReportService(db_session).build(days=10)
            text = " ".join(rep["caveats"])
            assert "2C" in text and "没被注册进来" in text
        finally:
            ss.VARIANTS.clear(); ss.VARIANT_DESCRIPTIONS.clear()
            ss.VARIANTS.update(saved[0]); ss._VARIANT_ORDER[:] = saved[1]
            ss.VARIANT_DESCRIPTIONS.update(saved[2])
