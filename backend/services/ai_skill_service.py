"""AI Skill 服务 — Skill 管理 + 系统提示词注入 + 数据占位符渲染

调用逻辑（2026-08-29 设计）：
1. 对话时取全部 enabled=True 的 skill，按 id 序拼接为系统提示词段落
2. skill 的 system_prompt 支持三个数据占位符，注入前渲染：
   - {{fund_pool}}     基金池及每只基金最新评分/信号（与基础上下文同源）
   - {{market_regime}} 市场环境快照（沪深300 PE 分位/涨跌家数比/两融 7 日变化）
   - {{fund:<id>}}     单基金最新分析详情（评分/信号/因子/操作建议）
   占位符渲染失败（数据缺失/ID 不存在）替换为"（数据暂不可用）"，不阻塞对话
3. 注入顺序：基础角色提示词 → 各 Skill 段落 → 系统数据上下文 → 会话上下文
"""

import json
import logging
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.ai_skill import AISkill
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund

logger = logging.getLogger(__name__)

# 占位符: {{fund_pool}} / {{market_regime}} / {{fund:123}}
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(fund_pool|market_regime|fund:\s*\d+)\s*\}\}")
_FALLBACK = "（数据暂不可用）"


async def get_enabled_skills(db: AsyncSession) -> list[AISkill]:
    """全部启用的 skill（按 id 序，保证注入顺序稳定）"""
    result = await db.execute(
        select(AISkill).where(AISkill.enabled == True).order_by(AISkill.id)  # noqa: E712
    )
    return list(result.scalars().all())


def build_skills_prompt(skills: list[AISkill]) -> str:
    """拼接 skill 段落（占位符由 render_placeholders 单独渲染）"""
    if not skills:
        return ""
    blocks = [f"## Skill: {sk.name}\n{sk.system_prompt}" for sk in skills]
    return "\n\n".join(blocks)


def extract_placeholders(prompt: str) -> list[str]:
    """提取 prompt 中出现的占位符（供调用方按需渲染，避免无用数据查询）"""
    return [m.group(1) for m in _PLACEHOLDER_RE.finditer(prompt or "")]


async def render_placeholder(
    token: str, db: AsyncSession
) -> str:
    """渲染单个占位符为数据文本"""
    try:
        if token == "fund_pool":
            return await _render_fund_pool(db)
        if token == "market_regime":
            return await _render_market_regime()
        if token.startswith("fund:"):
            fund_id = int(token.split(":", 1)[1].strip())
            return await _render_fund_detail(db, fund_id)
    except Exception as e:
        logger.warning(f"Skill 占位符渲染失败 {token}: {e}")
    return _FALLBACK


async def render_skill_prompts(
    skills: list[AISkill], db: AsyncSession
) -> list[tuple[str, str]]:
    """渲染全部 skill 的 system_prompt

    Returns:
        [(skill_name, rendered_prompt)] — 渲染失败的字段降级为 _FALLBACK
    """
    out: list[tuple[str, str]] = []
    for sk in skills:
        prompt = sk.system_prompt or ""
        placeholders = set(extract_placeholders(prompt))
        for token in placeholders:
            rendered = await render_placeholder(token, db)
            prompt = prompt.replace("{{" + token + "}}", rendered)
        out.append((sk.name, prompt))
    return out


# ── 占位符数据渲染 ────────────────────────────────────────────────────

async def _render_fund_pool(db: AsyncSession) -> str:
    """基金池及最新分析摘要"""
    funds = (await db.execute(select(Fund).where(Fund.status == "active"))).scalars().all()
    if not funds:
        return "（基金池为空）"
    lines = []
    for f in funds:
        ar = (await db.execute(
            select(AnalysisResult)
            .where(AnalysisResult.fund_id == f.id)
            .order_by(AnalysisResult.analysis_date.desc())
            .limit(1)
        )).scalars().first()
        if ar:
            lines.append(
                f"- {f.name}({f.code}): 评分={ar.weighted_score}, "
                f"方向={ar.signal_direction}, 强度={ar.signal_strength}"
            )
        else:
            lines.append(f"- {f.name}({f.code}): 暂无分析数据")
    return "【基金池及最新分析】\n" + "\n".join(lines)


async def _render_market_regime() -> str:
    """市场环境快照（估值分位/情绪/资金面）"""
    from backend.services.market_regime_service import MarketRegimeService
    snap = await MarketRegimeService().get_snapshot()
    lines = []
    if snap.valuation_percentile is not None:
        lines.append(f"- 沪深300 PE 分位: {snap.valuation_percentile:.0%}")
    if snap.adv_decline_ratio is not None:
        lines.append(
            f"- 涨跌家数比: {snap.adv_decline_ratio:+.2f} "
            f"(涨{snap.up_count}/跌{snap.down_count})"
        )
    if snap.margin_change_pct_7d is not None:
        lines.append(f"- 两融余额 7 日变化: {snap.margin_change_pct_7d:+.2%}")
    if not lines:
        return _FALLBACK
    return "【市场环境】\n" + "\n".join(lines)


async def _render_fund_detail(db: AsyncSession, fund_id: int) -> str:
    """单基金最新分析详情"""
    fund = (await db.execute(select(Fund).where(Fund.id == fund_id))).scalars().first()
    if fund is None:
        return _FALLBACK
    ar = (await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.fund_id == fund.id)
        .order_by(AnalysisResult.analysis_date.desc())
        .limit(1)
    )).scalars().first()
    if ar is None:
        return f"【{fund.name}({fund.code})】暂无分析数据"

    parts = [
        f"【{fund.name}({fund.code}) 最新分析】",
        f"- 评分: {ar.weighted_score}",
        f"- 信号: {ar.signal_direction}/{ar.signal_strength}",
        f"- 操作建议: {ar.operation_advice}",
    ]
    try:
        scores = json.loads(ar.factor_scores)
        brief = {k: {"评分": v.get("score"), "原始值": v.get("raw_value")}
                 for k, v in scores.items()}
        parts.append(f"- 因子评分: {json.dumps(brief, ensure_ascii=False)}")
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return "\n".join(parts)
