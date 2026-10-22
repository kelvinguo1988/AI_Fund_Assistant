"""AI 点评批量生成（2026-10-22）

背景：分析结果页是用户最常看的页面，但只有评分/方向/阈值这些数字，
缺少「为什么」的解读。本服务在分析完成后批量生成一句话点评并落库，
前端直接读取。

三个关键决策（都有实测依据）：
1. **批量而非逐只**：一次请求让模型读全部信号并输出 JSON 点评表。
   逐只调用 N 次会 N 倍消耗额度，而且模型看不到全池分布，
   无法判断「这只在池内属于什么位置」。
2. **只点评有事可说的**：`hold` 且无警告的基金点评价值低，跳过；
   只处理 buy/sell 与带质量警告的，通常 20~40 只，成本可控。
3. **失败静默**：点评是增强信息，生成失败不能让分析流程报错，
   前端据 ai_comment 为 NULL 显示「暂无点评」。

成本护栏：单次最多 MAX_FUNDS 只、输出 tokens 上限固定，
避免免费模型长尾把一次分析拖成十几秒。
"""

import asyncio
import json
import logging
import re
from typing import Optional

from sqlalchemy import select

from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.utils.timezone import now_beijing

logger = logging.getLogger(__name__)

MAX_FUNDS = 40          # 单次点评上限
MAX_TOKENS = 3000       # 输出上限（40 条点评约 1500 tokens）
VALID_DIRECTIONS = {"buy", "sell", "hold"}


def _strip_json_fence(text: str) -> str:
    """模型常把 JSON 包在 ```json 里；剥掉围栏再解析"""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        return m.group(1).strip()
    return t


def parse_comments(raw: str) -> dict[str, str]:
    """解析模型输出 → {code: comment}

    容错优先：免费模型经常多写解释、少个括号。解析不出就返回空 dict，
    由调用方按「未生成」处理，不抛异常。
    """
    text = _strip_json_fence(raw)
    # 只取最外层数组/对象
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            candidate = text[i:j + 1]
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            out: dict[str, str] = {}
            items = data if isinstance(data, list) else (
                data.get("comments") if isinstance(data, dict) else None
            )
            for item in (items or []):
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code") or "").strip()
                text_v = str(item.get("comment") or "").strip()
                if code and text_v:
                    out[code] = text_v[:200]
            if out:
                return out
    return {}


async def generate_comments(
    db, provider, analysis_date=None, max_funds: int = MAX_FUNDS
) -> dict:
    """为指定日期的分析结果批量生成点评并落库

    Args:
        db: AsyncSession
        provider: 已构造的 LLM Provider（沿用系统设置里的模型）
        analysis_date: 目标日期，None=最新一批
        max_funds: 单次上限

    Returns:
        {"date":..., "requested":N, "generated":N, "skipped":N, "error":...}
    """
    from sqlalchemy import func as _func

    if analysis_date is None:
        analysis_date = (await db.execute(
            select(_func.max(AnalysisResult.analysis_date))
        )).scalar()
    if analysis_date is None:
        return {"error": "无分析结果"}

    rows = (await db.execute(
        select(AnalysisResult, Fund)
        .join(Fund, AnalysisResult.fund_id == Fund.id)
        .where(AnalysisResult.analysis_date == analysis_date)
        .order_by(AnalysisResult.weighted_score.desc())
    )).all()
    if not rows:
        return {"date": str(analysis_date), "error": "该日期无分析结果"}

    # 只挑「有事可说」的：非 hold，或带质量警告
    picked = []
    for r, f in rows:
        warn = (r.quality_warnings or "").strip()
        if (r.signal_direction or "hold") == "hold" and not warn:
            continue
        picked.append((r, f))
    picked = picked[:max_funds]

    if not picked:
        return {"date": str(analysis_date), "requested": 0, "generated": 0,
                "skipped": len(rows), "note": "全部为 hold 且无警告，无需点评"}

    brief = [
        {
            "code": f.code,
            "name": f.name,
            "score": round(float(r.weighted_score or 0), 2),
            "dir": r.signal_direction,
            "strength": r.signal_strength,
            "tags": (f.tags or "")[:40],
            "warn": (r.quality_warnings or "")[:60],
        }
        for r, f in picked
    ]

    prompt = (
        "你是基金分析助手。下面是同日全部基金的信号（评分是**池内相对分**，"
        "只在当日池内可比，不是绝对质量）。请为每只写一句中文点评，要求：\n"
        "1) 25~45 字，说清「为什么是这个方向」+ 一句可执行提示；\n"
        "2) buy 类说清是靠什么强（动量/估值/资金），提示仓位或风险；\n"
        "3) sell 类说清是走弱还是过热，提示减仓节奏；\n"
        "4) 有 warn 的必须点出该风险；\n"
        "5) 不要复述分数，不要客套话。\n"
        "只输出 JSON 数组：[{\"code\":\"004011\",\"comment\":\"...\"}]\n\n"
        + json.dumps(brief, ensure_ascii=False)
    )

    try:
        raw = await provider.chat(
            system_prompt="你只输出 JSON，不要解释。",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=MAX_TOKENS,
            temperature=0.3,
        )
    except Exception as e:  # noqa: BLE001 - 点评失败不影响主流程
        logger.warning(f"AI 点评生成失败: {type(e).__name__}: {e}")
        return {"date": str(analysis_date), "error": f"模型调用失败: {type(e).__name__}"}

    comments = parse_comments(raw or "")
    if not comments:
        logger.warning(f"AI 点评解析失败，原始输出前 200 字: {(raw or '')[:200]}")
        return {"date": str(analysis_date), "error": "模型输出解析失败"}

    model_name = getattr(provider, "model_name", "") or ""
    now = now_beijing()
    written = 0
    for r, f in picked:
        text = comments.get(f.code)
        if not text:
            continue
        r.ai_comment = text
        r.ai_comment_model = model_name[:80]
        r.ai_comment_at = now
        written += 1
    try:
        await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"AI 点评落库失败: {e}")
        return {"date": str(analysis_date), "error": "落库失败"}

    logger.info(
        f"AI 点评已生成 {written}/{len(picked)} 只（{analysis_date}，模型 {model_name}）"
    )
    return {
        "date": str(analysis_date),
        "requested": len(picked),
        "generated": written,
        "skipped": len(rows) - len(picked),
        "model": model_name,
    }
