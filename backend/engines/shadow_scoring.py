"""影子评分层（§3，2026-10-02 第二批）— 新口径先只写影子列，生产信号一个字节都不动

第二批的共同风险是"改完无法证明变好"：库里样本量（十来个信号日）撑不起调参结论，
而 2C 那批改动（市场因子退加权和、动量簇去重、覆盖率归一、五档阈值）会实质改变
每日买卖数量。所以先并排跑两套口径：**生产列继续走旧口径**，新口径只写
`analysis_results.shadow_*`，分歧比例与分布迁移由 `shadow_report_service` 汇总，
达到 §3 判据后再把开关切到生产。

红线：影子只是**再算一次纯 Python 加权**，零上游请求、零额外数据源调用；
它更不能反过来影响生产 —— 变体里抛任何异常都只表现为"这一行没有影子列"，
生产信号照常落库（`compute_shadow` 是唯一入口，异常在此收敛）。

变体契约（2C 按此注册，勿另起口径）：
- 输入 `ShadowContext` 里的 `factor_scores/factor_weights` 是**质量过滤修正后**的同一份数据，
  与生产评分共用，变体不得原地修改它们（要改就复制）；
- 返回 `ShadowSignal` 或 `None`（None = 该变体对这只基金不适用，不写影子列）；
- `signal`（生产结果）只读，用于对比与 `detail` 记录。

`pool_size`（截面样本数，Q5）与 `factor_coverage`（有效权重和/总权重，Q4）在同一轮
落库：这两个数是影子对比的解释前提 —— 同一个 2.5 分，50 只池和 8 只池不是一回事，
因子只生效一半的 2.5 分和满覆盖的 2.5 分也不是。

详见 docs/QUANT_DECISIONS_2026-10.md §3 与 §5.1（裁定：先入影子，达标再切）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from backend.engines.factor_engine import FactorScoreResult
from backend.engines.quality_filter import QualityFilterResult
from backend.engines.scoring_engine import SignalResult

logger = logging.getLogger(__name__)

# ── 配置键（system_config KV，与 caliber_service 同一套读法）──
SHADOW_ENABLED_CONFIG_KEY = "shadow_scoring_enabled"
SHADOW_VARIANT_CONFIG_KEY = "shadow_variant"
DEFAULT_SHADOW_ENABLED = True
DEFAULT_SHADOW_VARIANT = ""      # 空 = 用注册表里的第一个变体

CONFIG_DESCRIPTIONS = {
    SHADOW_ENABLED_CONFIG_KEY: "影子评分：1=每轮分析并排计算新口径写 shadow_* 列（默认）/ 0=完全不算",
    SHADOW_VARIANT_CONFIG_KEY: "影子口径变体名（空=注册表第一个）；候选：caliber_2c（2C 新评分结构）",
}

# 切换判据（§3 写死，避免拍脑袋）
DIVERGENCE_THRESHOLD_PCT = 15.0
STABLE_DAYS_REQUIRED = 5

# 影子口径"本轮不落库"的标记方向：新口径判定数据覆盖率不足时应不产出记录，
# 而 `shadow_direction` 写 NULL 在本层的语义是"这一行没跑影子"。两件事必须分得开，
# 否则分歧报表会把"新口径要剔除的基金"当成"没有对照样本"排除掉，正好低估改动影响面。
DIRECTION_SKIP = "skip"


@dataclass
class ShadowContext:
    """一次影子计算能看到的全部输入（都来自生产路径已经算好的东西）"""

    fund_code: str
    factor_scores: list[FactorScoreResult]
    factor_weights: list[float]
    active_factors: list[dict]
    qf_result: QualityFilterResult
    thresholds_json: str
    quality_cfg: dict
    signal: SignalResult                      # 生产口径结果（只读）
    pool_size: Optional[int] = None
    # 场外申购状态（生产同一份）：影子要按同样规则把"买不到"的买入降观望，
    # 否则分歧里混进一撮可执行性差异，不是口径差异
    otc_status: Optional[dict] = None


@dataclass
class ShadowSignal:
    score: float
    direction: str
    strength: str = ""
    detail: dict = field(default_factory=dict)


VariantFn = Callable[[ShadowContext], Optional[ShadowSignal]]

# 注册表：变体名 → 计算函数。2C 在 `backend/engines/shadow_variants.py` 里注册，
# 未注册时本层不写影子列（分歧报表据此明确说"新口径尚未实现"，不假装在跑）
VARIANTS: dict[str, VariantFn] = {}
VARIANT_DESCRIPTIONS: dict[str, str] = {}
_VARIANT_ORDER: list[str] = []


def register_variant(name: str, description: str = "") -> Callable[[VariantFn], VariantFn]:
    """装饰器注册影子变体（同名重复注册 = 覆盖，供测试与热替换）

    `description` 是给分歧报表/UI 看的一句话口径说明 —— 影子可以并存多个变体，
    读数字的人需要知道这一列是哪套口径算出来的。
    """
    def _wrap(fn: VariantFn) -> VariantFn:
        if name not in VARIANTS:
            _VARIANT_ORDER.append(name)
        VARIANTS[name] = fn
        if description:
            VARIANT_DESCRIPTIONS[name] = description
        return fn
    return _wrap


def unregister_variant(name: str) -> None:
    VARIANTS.pop(name, None)
    VARIANT_DESCRIPTIONS.pop(name, None)
    if name in _VARIANT_ORDER:
        _VARIANT_ORDER.remove(name)


def available_variants() -> list[str]:
    return list(_VARIANT_ORDER)


def variant_descriptions() -> dict[str, str]:
    return dict(VARIANT_DESCRIPTIONS)


def resolve_variant(requested: Optional[str] = None) -> Optional[str]:
    """按配置挑选变体名；未注册的名字不算（回落到注册表首个，避免配置写错就静默停摆）"""
    wanted = str(requested if requested is not None else DEFAULT_SHADOW_VARIANT).strip()
    if wanted in VARIANTS:
        return wanted
    return _VARIANT_ORDER[0] if _VARIANT_ORDER else None


def factor_coverage(factor_scores: list[FactorScoreResult],
                    factor_weights: list[float]) -> Optional[float]:
    """有效权重和 / 总权重（Q4 口径）：data_valid=False 的因子占多少权重

    返回 None 表示总权重为 0（无 active 因子），与"覆盖 0%"是两回事：前者是配置坏了，
    后者是数据缺失。0.0 与 None 在分歧报表里的解释完全不同。
    """
    total = 0.0
    valid = 0.0
    for i, w in enumerate(factor_weights):
        try:
            weight = float(w)
        except (TypeError, ValueError):
            continue
        total += abs(weight)
        fs = factor_scores[i] if i < len(factor_scores) else None
        if fs is not None and getattr(fs, "data_valid", True):
            valid += abs(weight)
    if total <= 0:
        return None
    return round(valid / total, 4)


def compute_shadow(ctx: ShadowContext, enabled: bool = True,
                   variant: Optional[str] = None) -> tuple[Optional[str], Optional[ShadowSignal]]:
    """跑一次影子评分，返回 `(变体名, ShadowSignal)`；不写库、不碰生产结果

    三条"没有影子"的路径要分开看（都会返回 `(None, None)`）：开关关闭、注册表为空
    （新口径还没实现）、变体内部异常或不适用。异常只记 warning 不上抛 —— 影子层
    把生产分析整轮打挂是最坏结果。
    """
    if not enabled:
        return None, None
    name = resolve_variant(variant)
    if name is None:
        return None, None
    fn = VARIANTS[name]
    try:
        sig = fn(ctx)
    except Exception as e:
        logger.warning(f"影子评分变体 {name} 在 {ctx.fund_code} 上失败，本行不写影子列: "
                       f"{type(e).__name__}: {e}")
        return None, None
    if sig is None:
        return None, None
    return name, sig


# ── 配置读写 ──

async def load_shadow_config(db) -> dict:
    """生效的影子开关与变体：`{enabled: bool, variant: str, registered: [...]}`"""
    from sqlalchemy import select
    from backend.models.system_config import SystemConfig

    rows = (await db.execute(
        select(SystemConfig).where(SystemConfig.config_key.in_(
            [SHADOW_ENABLED_CONFIG_KEY, SHADOW_VARIANT_CONFIG_KEY]))
    )).scalars().all()
    kv = {r.config_key: (r.config_value or "").strip() for r in rows}

    raw_enabled = kv.get(SHADOW_ENABLED_CONFIG_KEY, "")
    enabled = DEFAULT_SHADOW_ENABLED
    if raw_enabled:
        enabled = raw_enabled.lower() not in ("0", "false", "off", "no")

    variant = resolve_variant(kv.get(SHADOW_VARIANT_CONFIG_KEY, DEFAULT_SHADOW_VARIANT))
    return {
        "enabled": enabled,
        "variant": variant or "",
        "requested_variant": kv.get(SHADOW_VARIANT_CONFIG_KEY, DEFAULT_SHADOW_VARIANT),
        "registered": available_variants(),
    }


async def save_shadow_config(db, enabled: Optional[bool] = None,
                             variant: Optional[str] = None) -> dict:
    """写开关/变体名；变体名必须已在注册表（否则 400 语义交给调用方）"""
    from sqlalchemy import select
    from backend.models.system_config import SystemConfig
    from backend.utils.timezone import now_beijing

    if variant is not None and variant.strip() and variant.strip() not in VARIANTS:
        raise ValueError(f"未注册的影子变体: {variant}（可用: {available_variants() or '无'}）")

    for key, value in (
        (SHADOW_ENABLED_CONFIG_KEY, None if enabled is None else ("1" if enabled else "0")),
        (SHADOW_VARIANT_CONFIG_KEY, None if variant is None else variant.strip()),
    ):
        if value is None:
            continue
        row = (await db.execute(
            select(SystemConfig).where(SystemConfig.config_key == key)
        )).scalars().first()
        if row:
            row.config_value = value
            row.updated_at = now_beijing()
        else:
            db.add(SystemConfig(config_key=key, config_value=value,
                                description=CONFIG_DESCRIPTIONS.get(key)))
    await db.commit()
    return await load_shadow_config(db)
