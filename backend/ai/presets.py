"""Agent 预置任务注册表 — 固定 prompt + Python 分析引擎上下文 + 工具白名单

从 backend/routers/ai_agent.py 抽出（P4），供两处消费：
- SSE 路由 /api/ai/agent/run：前端工作台一键任务（流式）
- 调度器 ai_daily_brief：run_collect 非流式收集最终文本 → 飞书推送

daily_brief 上下文为纯 Python 组装（最新信号分布/调仓四清单/因子诊断摘要/
市场环境快照），LLM 只做解读不改数字；引擎失败时降级为可用段落 + caveat，
简报任务不因单一引擎失败而整体中断。
"""

import json
import logging
from typing import Optional
from backend.utils.timezone import beijing_today

logger = logging.getLogger(__name__)


async def _factor_audit_context(db, params: dict) -> str:
    from backend.ai.factor_audit import FactorAuditService
    report = await FactorAuditService(db).audit(days=int(params.get("days", 90)))
    return json.dumps(report.to_dict(), ensure_ascii=False, default=str)


async def _rebalance_context(db, params: dict) -> str:
    from backend.ai.rebalance import RebalanceService
    # record_advice：Agent 任务产出的工单同时进 advice_log（自进化闭环的样本来源）
    report = await RebalanceService(db).analyze(
        window_days=int(params.get("window_days", 30)), record_advice=True)
    return json.dumps(report.to_dict(), ensure_ascii=False, default=str)


# ── 每日简报：纯 Python 组装结构化上下文 ─────────────────────────────

async def build_brief_payload(db) -> dict:
    """简报上下文：信号概况 + 调仓四清单摘要 + 因子诊断摘要 + 市场环境"""
    from datetime import date

    from sqlalchemy import select

    from backend.models.analysis_result import AnalysisResult
    from backend.models.fund import Fund
    from backend.engines.scoring_engine import score_caliber_note

    payload: dict = {"as_of": str(beijing_today()), "sections": {}}
    caveats: list[str] = []

    # ① 最新一轮信号分布与买卖前列
    funds = {f.id: f for f in (await db.execute(select(Fund).where(Fund.status == "active"))).scalars().all()}
    latest_date = (await db.execute(
        select(AnalysisResult.analysis_date).order_by(AnalysisResult.analysis_date.desc()).limit(1)
    )).scalars().first()
    overview: dict = {"analysis_date": str(latest_date) if latest_date else None,
                      "distribution": {}, "top_buy": [], "top_sell": []}
    if latest_date and funds:
        rows = (await db.execute(
            select(AnalysisResult).where(
                AnalysisResult.analysis_date == latest_date,
                AnalysisResult.fund_id.in_(list(funds)),
            )
        )).scalars().all()
        for r in rows:
            overview["distribution"][r.signal_direction] = overview["distribution"].get(r.signal_direction, 0) + 1
        ranked = sorted(rows, key=lambda r: r.weighted_score or 0.0, reverse=True)
        for r in ranked[:5]:
            f = funds.get(r.fund_id)
            overview["top_buy"].append({"code": f.code if f else None, "name": f.name if f else None,
                                        "score": r.weighted_score, "signal": r.signal_direction})
        for r in reversed(ranked[-5:]):
            f = funds.get(r.fund_id)
            overview["top_sell"].append({"code": f.code if f else None, "name": f.name if f else None,
                                         "score": r.weighted_score, "signal": r.signal_direction})
        # Q5：简报里的 score 是截面相对分，不写这句 LLM 会拿它当绝对质量跨日比较
        pools = [r.pool_size for r in rows if r.pool_size]
        caveats.append(f"signal_overview 里的 score 是{score_caliber_note(max(pools) if pools else None)}")
    else:
        caveats.append("最新分析数据缺失，请先运行分析任务")
    payload["sections"]["signal_overview"] = overview

    # ② 调仓四清单摘要（引擎口径与信号链路一致）
    try:
        from backend.ai.rebalance import RebalanceService
        rb = await RebalanceService(db).analyze(window_days=30, record_advice=True)
        rbd = rb.to_dict()
        payload["sections"]["rebalance"] = {
            "holdings_mode": rbd.get("holdings_mode"),
            "counts": {k: len(rbd.get(k) or []) for k in
                       ("sells", "buys", "buy_blocked", "watch", "swaps")},
            "sells": (rbd.get("sells") or [])[:5],
            "buys": (rbd.get("buys") or [])[:5],
            "watch": (rbd.get("watch") or [])[:5],
            "constraints": rbd.get("constraints"),
            "caveats": rbd.get("caveats") or [],
        }
    except Exception as e:
        logger.warning(f"简报-调仓引擎失败: {type(e).__name__}: {e}")
        caveats.append(f"调仓引擎失败（{type(e).__name__}），本节缺失")

    # ③ 因子诊断摘要（近 90 日 RankIC 首尾各 3）
    try:
        from backend.ai.factor_audit import FactorAuditService
        fa = await FactorAuditService(db).audit(days=90)
        ic = sorted(
            [x for x in fa.factor_ic if x["rank_ic_mean"] is not None],
            key=lambda x: -x["rank_ic_mean"],
        )
        payload["sections"]["factor_audit"] = {
            "window": f"{fa.window_start} ~ {fa.window_end}",
            # Q12：Top/Bottom 只是 RankIC 排序，必须连同采样口径与非显著警告一起给出，
            # 否则简报会把 4 个周期里的噪声读成"某因子失效"
            "ic_sampling": fa.ic_sampling,
            "top_factors": ic[:3], "bottom_factors": ic[-3:],
            "significant_factors": [x for x in ic if x.get("significant")],
            "caveats": fa.caveats[:5],
            "whipsaw_top": fa.whipsaw_top[:3],
        }
    except Exception as e:
        logger.warning(f"简报-因子诊断引擎失败: {type(e).__name__}: {e}")
        caveats.append(f"因子诊断失败（{type(e).__name__}），本节缺失")

    # ④ 市场环境快照（缓存优先，不打行情源）
    try:
        from backend.services.market_regime_service import MarketRegimeService
        snap = await MarketRegimeService().get_snapshot()
        payload["sections"]["market_regime"] = {
            "regime": getattr(snap, "regime", None),
            "valuation_percentile": snap.valuation_percentile,
            "valuation_sample_points": getattr(snap, "valuation_sample_points", None),
            "adv_decline_ratio": snap.adv_decline_ratio,
            "margin_change_pct_7d": snap.margin_change_pct_7d,
        }
    except Exception as e:
        logger.warning(f"简报-市场环境快照失败: {type(e).__name__}: {e}")
        caveats.append("市场环境快照不可用")

    # ⑤ 收益口径头（Q11-C）：简报里凡是引用区间收益/超额/回测数字，都必须带同一份口径
    try:
        from backend.services.caliber_service import caliber_head_lines, load_caliber
        policy = await load_caliber(db)
        payload["caliber"] = caliber_head_lines(
            policy,
            extra="信号与调仓清单为当日快照，不含区间收益",
            cash_line="回测未成交的现金按 0% 计息（不计息）",
        )
    except Exception as e:
        logger.warning(f"简报-收益口径读取失败: {type(e).__name__}: {e}")

    payload["caveats"] = caveats
    return payload


async def _daily_brief_context(db, params: dict) -> str:
    return json.dumps(await build_brief_payload(db), ensure_ascii=False, default=str)


PRESET_TASKS: dict[str, dict] = {
    "factor_audit": {
        "default_prompt": (
            "以下是 Python 诊断引擎产出的因子与策略效果统计（RankIC/IR、评分五分位单调性、"
            "信号超额胜率、whipsaw、质量过滤对照）。请解读：①哪些因子有效/失效/建议调权或停用"
            "②评分与阈值是否需要调整③质量过滤是否产生了正贡献④数据口径风险。"
            "结论必须基于给定数字，不得重新计算或编造统计量。"
            "Q12 口径硬约束：factor_ic 里的 IR 是**非重叠周期**上的均值/标准差（看 days 与 "
            "n_days_valid 两个数），rank_ic_ir_annualized 才是可与他人比较的年化 IR；"
            "只有 rank_ic_q_bh < 0.05（同窗口多因子校正后）且 days ≥ 8 的因子才允许说"
            "'有效/建议调权重'，其余一律说'独立周期不足或未达显著，不能据此调权重'。"
        ),
        "context_builder": _factor_audit_context,
        "allowed_tools": ["get_latest_signals", "list_factors", "get_scoring_config",
                          "get_analysis_stats", "get_backtest_summary"],
        "max_rounds": 4,
    },
    "rebalance": {
        "default_prompt": (
            "以下是 Python 调仓引擎产出的四清单（减仓卖出/买入候选/同赛道换仓配对/观望待确认）"
            "与组合层约束（赛道集中度/QDII 权重/重仓双胞胎重合），口径与信号链路一致"
            "（动态阈值、场外申购否决、whipsaw 只比较相邻非 hold 信号）。请解读："
            "①卖出候选的佐证强度与执行顺序（先赎哪些、分几日）②买入/换仓去向是否最优、"
            "费率与赎回到账时滞等换仓代价③观望清单需要等待的确认条件④组合集中度风险如何拆解"
            "⑤若为等权近似模式，说明录入真实持仓后的差异。结论只能基于给定清单与工具查询结果，"
            "不得重算评分或编造数据；场外标的给出买入建议前必须用 get_otc_trade_status 复核申购状态。"
        ),
        "context_builder": _rebalance_context,
        "allowed_tools": ["get_positions", "get_latest_signals", "get_signal_history",
                          "get_otc_trade_status", "get_holding_overlap", "list_fund_pool",
                          "get_scoring_config", "get_factor_snapshot"],
        "max_rounds": 5,
    },
    "daily_brief": {
        "default_prompt": (
            "以下是今日基金池的结构化摘要（最新信号分布与买卖前5、调仓四清单摘要、"
            "近90日因子RankIC首尾各3、市场环境快照）。请生成一份 300 字以内的每日简报，"
            "Markdown 格式：①一句话总体判断（市场环境下池子信号基调）②值得行动的 1-3 条"
            "（结合调仓清单与申购可执行性）③1 条风险/观察提示。只使用给定数字与结论，"
            "不得重算或编造；某节缺失时在末尾注明。"
            "若正文引用了区间收益/超额/回测数字，必须在开头单独一行照抄 caliber 三行口径"
            "（净值口径 / 基准口径 / 计息口径），不改写其中数字。"
        ),
        "context_builder": _daily_brief_context,
        "allowed_tools": ["get_latest_signals", "get_positions", "get_otc_trade_status",
                          "list_fund_pool"],
        "max_rounds": 3,
    },
}


async def run_collect(db, task: str, params: Optional[dict] = None,
                      prompt: str = "", max_rounds: Optional[int] = None) -> str:
    """非流式运行预置任务，返回最终文本（调度器/简报用）。

    任何 error 事件 → 抛 RuntimeError（调用方决定静默或告警）。
    """
    from backend.ai.agent_runner import AgentRunner

    preset = PRESET_TASKS.get(task)
    if preset is None:
        raise ValueError(f"未知预置任务: {task}")
    user_prompt = (prompt or "").strip() or preset["default_prompt"]

    extra_system = ""
    builder = preset.get("context_builder")
    if builder is not None:
        extra_system = await builder(db, params or {})

    runner = AgentRunner(
        db,
        max_rounds=max_rounds or preset.get("max_rounds") or 8,
        allowed_tools=preset.get("allowed_tools"),
    )
    final_text = ""
    async for ev in runner.run_stream(user_prompt=user_prompt, extra_system=extra_system):
        if ev["type"] == "final":
            final_text = ev.get("content") or ""
        elif ev["type"] == "error":
            raise RuntimeError(f"Agent 任务 {task} 失败: {ev.get('message')}")
    if not final_text:
        raise RuntimeError(f"Agent 任务 {task} 无最终产出")
    return final_text
