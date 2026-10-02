"""2C 影子变体 `caliber_2c` —— 评分结构新口径（Q2 / Q3 / Q4 / Q1），只写影子列

对应 `docs/QUANT_DECISIONS_2026-10.md` §5.1 的裁定，四项一起构成"新口径"，因为它们互相
依赖（Q2 改总权重 → Q4 的覆盖率分母、Q3 的簇上限基准都要跟着变），拆开算出来的分歧数
没有解释力。逐项口径：

**Q2 市场三因子退出加权和**：`market_*` 三只权重清零（`raw_value/score` 仍由生产落库，
本变体只是不让它们进总分），择时职责完全交给动态阈值。同时**参考总权重成对下移**：
移出多少配置权重，`threshold_ref_total_weight` 就下移多少（市场 1.8 + 趋势 0.5 ⇒
8.3 → 6.0）。这两件必须同时做，只清权重不改 ref 会让 `scale = total/ref` 掉到 0.78，
等于把买卖门槛一起缩，市场顺风的影响从"加分"变成"降门槛"，换了个后门进来。
同一个成对原则也管住 Q3：趋势移出加权和之后参考权重同样跟着下移，否则"去重"顺带
把门槛紧了 7.7%，分歧报表里就分不清是口径变的还是门槛变的。

**Q3 动量簇去重**：`trend_consistency` 与 short/mid 是同一组输入的确定性函数，从加权和
移出改成**乘性位**（一正一负 → short/mid/accel 三项之和 ×0.8，其余因子不受牵连）；
`momentum_accel` 留在簇内受簇上限约束。
簇上限 = `cluster_cap_pct` × `cluster_cap_base_weight`（35% × 8.3 ≈ 2.9）。基准与 ref
**故意解耦**：若跟着 ref 走，"去掉市场因子"会连带把动量上限从 2.9 压到 2.275，两件事
被耦合在一起，分歧归因就分不清是谁干的。
正交化（accel 对 short/mid 截面回归取残差）需要**全池**输入，`ShadowContext` 是单只
基金的上下文，本变体不做 —— 按 Q3 建议档留作第二步。

**Q4 覆盖率折算**：`data_valid=False` 的因子占的权重不再白给（生产按满权重求和，
缺数据的基金理论满分只有 2.5 却要够 1.5 的门槛）。折算走阈值侧（裁定 B）：阈值乘
`有效权重和 / 总权重`；覆盖率低于 `min_coverage`（裁定 C）时本变体**不给方向**，
`shadow_direction="skip"` —— 新口径下这只基金本轮不落库，"没有记录"和"观望"是两件事，
把 skip 洗成 hold 会系统性低估这批改动的影响面。

**Q1 五档阈值**：方向仍由动态阈值决定，五档只渲染措辞与权益仓位（裁定 B）。这四项里
只有 Q1 不改变方向，所以它的影子证据放在 `detail.tier`：档位文案、以及**档位方向与
动态方向是否冲突**（冲突数就是"必须标注'仅供参考'的那批"）。

红线：本模块**不发任何请求**，输入全部来自生产路径已经算好的东西（质量过滤修正后的
因子分值与权重、动态阈值、偏置、申购状态），输出只进 `analysis_results.shadow_*`。
`signal` 只读，禁止原地改 `factor_scores/factor_weights`（与生产共用同一对象）。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from backend.engines.quality_filter import apply_otc_trade_constraint, determine_signal
from backend.engines.scoring_engine import DEFAULT_THRESHOLDS, SignalResult
from backend.engines.shadow_scoring import (
    DIRECTION_SKIP, ShadowContext, ShadowSignal, register_variant,
)

logger = logging.getLogger(__name__)

VARIANT_NAME = "caliber_2c"
DESCRIPTION = "2C 新评分结构：市场因子退出加权和 + 动量簇上限/趋势乘性位 + 覆盖率折算 + 五档只渲染文案"

# 因子分值钳位与生产同一常数（`scoring_engine.compute`）
SCORE_CLAMP = 8.5

# Q2：市场三因子（只读 regime 快照，全池同分）
MARKET_FACTORS = frozenset({"market_valuation", "market_sentiment", "market_fund_flow"})
# Q3：从加权和移出、改乘性位的因子
TREND_FACTOR = "trend_consistency"
# Q3：簇归属（未在表内的因子不参与簇上限）
FACTOR_CLUSTERS: dict[str, frozenset[str]] = {
    "momentum": frozenset({"short_momentum", "mid_momentum", "momentum_accel", TREND_FACTOR}),
    "volatility": frozenset({"inv_volatility", "return_risk_ratio"}),
    "market": MARKET_FACTORS,
    "absolute": frozenset({"drawdown_recovery", "macd_signal"}),
}
# Q3：乘性位的作用范围 = 动量簇去掉 trend（trend 本身已不在加权和里）
GATED_FACTORS: frozenset[str] = FACTOR_CLUSTERS["momentum"] - {TREND_FACTOR}
# 2C 的参数键（QUALITY_CONFIG 默认值 + `quality_filter_config` JSON 可覆盖）
P_CLUSTER_CAP_PCT = "shadow_2c_cluster_cap_pct"
P_CLUSTER_CAP_BASE = "shadow_2c_cluster_cap_base_weight"
P_TREND_FACTOR = "shadow_2c_trend_disagree_factor"
P_MIN_COVERAGE = "shadow_2c_min_coverage"

# 取值区间：越界一律钳到边界并在 `detail.params_clamped` 里留痕。
# 这四个键走的是 `PUT /api/system/quality-config`，那条链只校验"是数字"，
# 写成 500 或 -1 会静默把整个口径改掉 —— 影子报表看到的全零分歧会被读成
# "两口径一致"，那比崩溃更坏。钳位 + 留痕是把错误关在本口径内部。
PARAM_SPEC: list[dict] = [
    {"key": P_CLUSTER_CAP_PCT, "label": "单簇权重上限（%）", "min": 0.0, "max": 100.0,
     "governs": "Q3 动量簇去重", "note": "0 = 关闭簇上限"},
    {"key": P_CLUSTER_CAP_BASE, "label": "簇上限基准总权重", "min": 0.1, "max": 100.0,
     "governs": "Q3 动量簇去重", "note": "与阈值参考权重解耦；上限 = pct% × 此值"},
    {"key": P_TREND_FACTOR, "label": "趋势分歧乘数", "min": 0.0, "max": 2.0,
     "governs": "Q3 趋势乘性位", "note": "<1 才生效（收分）；≥1 或 0 = 关闭乘性位"},
    {"key": P_MIN_COVERAGE, "label": "有效权重覆盖率下限", "min": 0.0, "max": 1.0,
     "governs": "Q4 覆盖率剔除", "note": "低于此值影子方向记 skip；0 = 关闭"},
]
_PARAM_RANGE = {s["key"]: (s["min"], s["max"]) for s in PARAM_SPEC}


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def read_params(cfg: dict) -> tuple[dict, list[dict]]:
    """取 2C 参数（缺键回落 QUALITY_CONFIG 默认值）并钳位，返回 `(值, 越界记录)`"""
    from backend.engines.quality_filter import QUALITY_CONFIG

    values: dict[str, float] = {}
    clamped: list[dict] = []
    for spec in PARAM_SPEC:
        key = spec["key"]
        raw = cfg.get(key, QUALITY_CONFIG.get(key))
        val = _f(raw)
        low, high = _PARAM_RANGE[key]
        if not (low <= val <= high):
            fixed = min(max(val, low), high)
            clamped.append({"key": key, "raw": val, "used": fixed,
                            "range": [low, high]})
            logger.warning("影子口径2C 参数 %s=%s 越界（合法区间 %s~%s），按 %s 计算",
                           key, val, low, high, fixed)
            val = fixed
        values[key] = val
    return values, clamped


def effective_params(merged_cfg: dict) -> list[dict]:
    """供 GET /shadow-config 展示：生效值 + 默认值 + 这项管什么"""
    from backend.engines.quality_filter import QUALITY_CONFIG

    values, clamped = read_params(merged_cfg)
    out = []
    for spec in PARAM_SPEC:
        key = spec["key"]
        item = dict(spec)
        item["value"] = values[key]
        item["default"] = _f(QUALITY_CONFIG.get(key))
        item["out_of_range"] = any(c["key"] == key for c in clamped)
        out.append(item)
    return out


def _cluster_of(code: str) -> Optional[str]:
    for name, members in FACTOR_CLUSTERS.items():
        if code in members:
            return name
    return None


def _tier_of(thresholds_json: Optional[str], score: float) -> dict:
    """按五档取渲染结果（Q1-B：只提供措辞与权益仓位，方向仍由动态阈值决定）"""
    tiers = DEFAULT_THRESHOLDS
    if thresholds_json:
        try:
            data = json.loads(thresholds_json)
            if isinstance(data, list) and len(data) >= 3:
                tiers = data
        except (json.JSONDecodeError, TypeError):
            pass
    for tier in tiers:
        if score >= _f(tier.get("min_score"), -1e9):
            return tier
    return tiers[-1] if tiers else DEFAULT_THRESHOLDS[-1]


@register_variant(VARIANT_NAME, DESCRIPTION)
def caliber_2c(ctx: ShadowContext) -> Optional[ShadowSignal]:
    """2C 新口径：重算加权分与方向，生产结果只读不改"""
    scores = list(ctx.factor_scores)
    if not scores:
        return None

    # 权重按**评分项**取（与生产 `compute()` 的 zip 口径一致）：
    # `qf_result` 被前置否决时 corrected_weights 退回 active_factors 长度，
    # 与评分项个数可能对不上，逐位缺失记 0 而不是抛 IndexError 把整只基金的影子打掉。
    raw_weights = list(ctx.factor_weights)
    weights = [_f(raw_weights[i]) if i < len(raw_weights) else 0.0 for i in range(len(scores))]

    cfg = ctx.quality_cfg or {}
    params, params_clamped = read_params(cfg)
    prod_total_weight = sum(_f(f.get("weight", 1.0)) for f in (ctx.active_factors or []))
    ref_prod = _f(cfg.get("threshold_ref_total_weight"))

    # ── Q2：市场因子权重清零（用配置权重算 ref 的下移量，与质量过滤的权重提升解耦）──
    market_config_weight = sum(
        _f(f.get("weight", 1.0)) for f in (ctx.active_factors or [])
        if f.get("code") in MARKET_FACTORS
    )
    w = [0.0 if fs.factor_code in MARKET_FACTORS else weights[i]
         for i, fs in enumerate(scores)]
    # ── Q3：趋势一致性移出加权和 ──
    trend_weight = sum(abs(w[i]) for i, fs in enumerate(scores) if fs.factor_code == TREND_FACTOR)
    trend_config_weight = sum(
        _f(f.get("weight", 1.0)) for f in (ctx.active_factors or [])
        if f.get("code") == TREND_FACTOR
    )
    w = [0.0 if fs.factor_code == TREND_FACTOR else w[i] for i, fs in enumerate(scores)]

    # ── Q3：簇上限（基准权重与 ref 解耦，见模块文档）──
    cap_pct = params[P_CLUSTER_CAP_PCT]
    cap_base = params[P_CLUSTER_CAP_BASE]
    cap_applied: dict[str, dict[str, float]] = {}
    cap_trim = 0.0
    if cap_pct > 0 and cap_base > 0:
        cap = cap_pct / 100.0 * cap_base
        for name in FACTOR_CLUSTERS:
            idx = [i for i, fs in enumerate(scores) if _cluster_of(fs.factor_code) == name]
            before = sum(abs(w[i]) for i in idx)
            if before > cap > 0:
                ratio = cap / before
                for i in idx:
                    w[i] = w[i] * ratio
                cap_trim += before - cap
                cap_applied[name] = {"before": round(before, 3), "after": round(cap, 3)}

    total_shadow = sum(abs(x) for x in w)
    if total_shadow <= 0:
        logger.info("caliber_2c: 新口径总权重为 0（因子全部被清零？），本行不写影子列")
        return None

    # ── 加权求和 + Q3 乘性位 ──
    contribs = {scores[i].factor_code: round(scores[i].score * w[i], 4) for i in range(len(scores))}
    factor_sum = sum(scores[i].score * w[i] for i in range(len(scores)))

    trend_gate: dict = {"applied": False}
    gate = params[P_TREND_FACTOR]
    trend_fs = next((fs for fs in scores if fs.factor_code == TREND_FACTOR), None)
    # 只在"两只脚真的打架"时收：raw_value=0 即 mom20 与 mom60 符号相反；
    # 缺数据的基金 raw 也是 0.0，必须先看 data_valid，否则等于惩罚新基金
    if trend_fs is not None and trend_fs.data_valid and abs(_f(trend_fs.raw_value)) < 1e-9 and 0 < gate < 1:
        # 只乘动量簇三项：趋势打架不该顺带把低波动/绝对分也收掉，否则分歧报表里
        # "新口径改了什么"和"别的因子被牵连"分不开
        momentum_sum = sum(scores[i].score * w[i] for i in range(len(scores))
                           if scores[i].factor_code in GATED_FACTORS)
        factor_sum += momentum_sum * (gate - 1.0)
        for code in GATED_FACTORS:
            if code in contribs:
                contribs[code] = round(contribs[code] * gate, 4)
        trend_gate = {
            "applied": True, "factor": gate, "raw_value": 0.0,
            "scope": sorted(GATED_FACTORS), "momentum_sum": round(momentum_sum, 4),
        }

    original_shadow = round(max(-SCORE_CLAMP, min(SCORE_CLAMP, factor_sum)), 2)
    bias = _f(ctx.qf_result.institution_bias)
    adjusted_shadow = round(max(-SCORE_CLAMP, min(SCORE_CLAMP, original_shadow + bias)), 2)

    # ── Q4：有效权重覆盖率（分母是新口径真正参与评分的权重，被清零的市场/趋势不占分母）──
    valid_shadow = sum(abs(w[i]) for i, fs in enumerate(scores) if fs.data_valid)

    # ── 阈值：结构项（口径移出的权重）× 覆盖率项（这只基金缺的数据）──
    # 生产 `compute_dynamic_thresholds` 的 scale 用的是 `active_factors` 的配置权重和，
    # 且整函数对 scale 齐次（base、各 increment、低估下限都乘 scale），所以新口径的阈值
    # = 生产阈值 × 一个比例即可，不需要（也拿不到）市场环境快照重算一遍。
    # Q2 的"权重与参考权重成对改"原则在这里推广为：**本口径从加权和移出/裁掉多少配置权重，
    # 参考权重就下移多少**（市场 1.8 + 趋势 0.5 + 簇上限裁掉的部分）。于是数据齐全、
    # 没被簇上限裁到的基金 threshold_factor 恒为 1 —— 2C 里唯一动门槛的只剩 Q4 覆盖率，
    # 分歧报表里的分数差才只反映口径而不是门槛。
    ref_shadow = ref_prod - (market_config_weight + trend_config_weight + cap_trim)
    config_shadow_total = prod_total_weight - (market_config_weight + trend_config_weight + cap_trim)
    min_coverage = params[P_MIN_COVERAGE]
    coverage = round(valid_shadow / total_shadow, 4) if total_shadow > 0 else 1.0
    structure: Optional[float] = None
    if ref_prod > 0 and prod_total_weight > 0 and ref_shadow > 0 and config_shadow_total > 0:
        structure = (config_shadow_total / ref_shadow) * (ref_prod / prod_total_weight)
        threshold_factor = structure * coverage
    else:
        # ref 机制未启用：生产的阈值是绝对值（不随总权重折算），
        # 新口径只按覆盖率折算，否则两个口径的差里混进"参考权重没开"这一层解释
        threshold_factor = coverage if min_coverage > 0 else 1.0

    buy_th = _f(ctx.qf_result.dynamic_buy_threshold, 1.5) * threshold_factor
    sell_th = _f(ctx.qf_result.dynamic_sell_threshold, -1.5) * threshold_factor

    low_coverage = min_coverage > 0 and coverage < min_coverage
    otc_downgraded = False
    if low_coverage:
        direction, strength = DIRECTION_SKIP, DIRECTION_SKIP
    else:
        direction, strength, _ = determine_signal(
            adjusted_shadow, buy_th, sell_th, bool(ctx.qf_result.drift_triggered), cfg,
        )
        # 与生产同口径的可执行性降级：暂停申购/封闭期 → 买入降观望。
        # 不套这一步会把"新口径也喊买但场外买不到"记成一致，而把真实分歧留到明天
        if direction == "buy" and ctx.otc_status:
            probe = SignalResult(
                weighted_score=adjusted_shadow, raw_score=original_shadow,
                signal_direction=direction, signal_strength=strength,
                operation_advice="", equity_ratio=0.5,
            )
            probe = apply_otc_trade_constraint(probe, ctx.otc_status, cfg)
            if probe.signal_direction != direction:
                otc_downgraded = True
                direction, strength = probe.signal_direction, probe.signal_strength

    # ── Q1：五档只提供措辞（方向以上为准，冲突即"档位说加仓但动态阈值判观望"）──
    tier = _tier_of(ctx.thresholds_json, adjusted_shadow)
    tier_info = {
        "label": tier.get("label", ""),
        "direction": tier.get("signal_direction", ""),
        "equity_ratio": tier.get("equity_ratio"),
        "advice": str(tier.get("operation_advice", "")).format(
            score=adjusted_shadow,
            equity_pct=int(_f(tier.get("equity_ratio"), 0.5) * 100),
        ),
        "conflict_with_dynamic": str(tier.get("signal_direction", "")) != direction,
    }

    detail = {
        "variant_desc": DESCRIPTION,
        "params": {k: params[k] for k in _PARAM_RANGE},
        "params_clamped": params_clamped or None,
        "removed_weights": {
            "market": round(market_config_weight, 3),
            "trend_consistency": round(trend_weight, 3),
        },
        "cluster_cap": {
            "pct": cap_pct, "base_weight": cap_base,
            "applied": cap_applied or None,
        },
        "total_weight": {
            "prod": round(prod_total_weight, 3),
            "shadow": round(total_shadow, 3),
            "shadow_valid": round(valid_shadow, 3),
        },
        "threshold_ref": {"prod": round(ref_prod, 3), "shadow": round(ref_shadow, 3)},
        "threshold_factor": round(threshold_factor, 4),
        # 折算拆成两项，读报表时能分清"门槛变了"是口径结构造成的还是这只基金缺数据造成的
        "threshold_structure": None if structure is None else round(structure, 4),
        "threshold_coverage": coverage,
        "thresholds": {
            "prod": [_f(ctx.qf_result.dynamic_buy_threshold, 1.5),
                     _f(ctx.qf_result.dynamic_sell_threshold, -1.5)],
            "shadow": [round(buy_th, 3), round(sell_th, 3)],
        },
        "coverage": {
            "value": coverage, "min": min_coverage,
            "rejected": low_coverage,
            "note": ("覆盖率低于下限，新口径本轮不落库（shadow_direction=skip）"
                     if low_coverage else "阈值按有效权重折算" if min_coverage > 0 else "覆盖率折算已关闭"),
        },
        "trend_gate": trend_gate,
        "otc_downgraded": otc_downgraded,
        "score": {
            "prod_original": _f(ctx.signal.original_score),
            "shadow_original": original_shadow,
            "bias": bias,
            "shadow_adjusted": adjusted_shadow,
        },
        "contributions": contribs,
        "tier": tier_info,
        "pool_size": ctx.pool_size,
    }

    return ShadowSignal(
        score=adjusted_shadow,
        direction=direction,
        strength=strength,
        detail=detail,
    )
