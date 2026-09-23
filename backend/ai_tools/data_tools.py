"""AI Agent 内置只读数据工具（15 个）

全部薄包装既有 service/表查询；market_overview 只读缓存不发网络请求，
防止 Agent 循环放大对数据源的封禁压力。handler 第一参数固定为 db session。
"""

import json
import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from backend.ai_tools.registry import tool
from backend.models.analysis_result import AnalysisResult
from backend.models.backtest_result import BacktestResult
from backend.models.factor import Factor
from backend.models.fund import Fund
from backend.models.fund_holding import FundHolding
from backend.models.system_config import SystemConfig

logger = logging.getLogger(__name__)

_OBJ = {"type": "object", "properties": {}, "additionalProperties": False}


def _props(**kwargs) -> dict:
    return {"type": "object", "properties": kwargs, "additionalProperties": False}


def _code_param(desc: str) -> dict:
    return {"type": "string", "description": desc}


async def _fund_by_code(db, code: str):
    return (await db.execute(select(Fund).where(Fund.code == str(code).strip()))).scalars().first()


def _scores_dict(raw) -> dict:
    try:
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


@tool(
    "list_fund_pool",
    "列出基金池：代码/名称/类型(otc场外|etf)/分类标签/赛道暴露标签/星标与状态。",
    _props(status={"type": "string", "enum": ["active", "all"], "description": "默认 active"}),
)
async def t_list_fund_pool(db, status: str = "active") -> list[dict]:
    stmt = select(Fund)
    if status != "all":
        stmt = stmt.where(Fund.status == "active")
    funds = (await db.execute(stmt.order_by(Fund.code))).scalars().all()
    return [
        {
            "code": f.code, "name": f.name, "fund_type": f.fund_type,
            "tags": f.tags or "", "exposure_tags": f.exposure_tags or "",
            "starred": bool(f.starred), "status": f.status,
        }
        for f in funds
    ]


@tool(
    "get_latest_signals",
    "获取基金池最新一批分析信号：评分(±8.5)/方向/强度/建议/修正前原始分/动态阈值/质量警告。",
    _OBJ,
)
async def t_get_latest_signals(db) -> list[dict]:
    latest = (await db.execute(select(func.max(AnalysisResult.analysis_date)))).scalar()
    if latest is None:
        return []
    rows = (await db.execute(
        select(AnalysisResult, Fund)
        .join(Fund, AnalysisResult.fund_id == Fund.id)
        .where(AnalysisResult.analysis_date == latest)
        .order_by(AnalysisResult.weighted_score.desc())
    )).all()
    return [
        {
            "date": str(r.analysis_date), "code": f.code, "name": f.name,
            "score": r.weighted_score, "direction": r.signal_direction,
            "strength": r.signal_strength, "advice": r.operation_advice or "",
            "original_score": r.original_score,
            "dynamic_buy_threshold": r.dynamic_buy_threshold,
            "dynamic_sell_threshold": r.dynamic_sell_threshold,
            "quality_warnings": json.loads(r.quality_warnings) if r.quality_warnings else None,
        }
        for r, f in rows
    ]


@tool(
    "get_signal_history",
    "单只基金近 N 天信号序列（日期/评分/方向/强度/警告），用于趋势与翻转(whipsaw)判断。",
    _props(code=_code_param("基金代码，如 004011"),
           days={"type": "integer", "description": "回溯天数，默认 30，上限 250"}),
)
async def t_get_signal_history(db, code: str, days: int = 30) -> list[dict]:
    fund = await _fund_by_code(db, code)
    if not fund:
        raise ValueError(f"基金不存在: {code}")
    since = date.today() - timedelta(days=min(int(days), 250))
    rows = (await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.fund_id == fund.id, AnalysisResult.analysis_date >= since)
        .order_by(AnalysisResult.analysis_date)
    )).scalars().all()
    return [
        {
            "date": str(r.analysis_date), "score": r.weighted_score,
            "direction": r.signal_direction, "strength": r.signal_strength,
            "quality_warnings": json.loads(r.quality_warnings) if r.quality_warnings else None,
        }
        for r in rows
    ]


@tool(
    "get_factor_snapshot",
    "指定日期（默认最新）全池因子分：{基金代码:{因子代码:分值}}。分值为截面标准化后的 ±1 桶值。",
    _props(analysis_date={"type": "string", "description": "YYYY-MM-DD，可空=最新一日"}),
)
async def t_get_factor_snapshot(db, analysis_date: str = "") -> dict:
    stmt = select(AnalysisResult)
    if analysis_date:
        stmt = stmt.where(AnalysisResult.analysis_date == date.fromisoformat(analysis_date))
    else:
        latest = (await db.execute(select(func.max(AnalysisResult.analysis_date)))).scalar()
        if latest is None:
            return {}
        stmt = stmt.where(AnalysisResult.analysis_date == latest)
    rows = (await db.execute(stmt)).scalars().all()
    fund_ids = {r.fund_id for r in rows}
    funds = {}
    if fund_ids:
        funds = {f.id: f.code for f in (await db.execute(select(Fund).where(Fund.id.in_(fund_ids)))).scalars().all()}
    return {
        funds.get(r.fund_id, str(r.fund_id)): {
            code: (val.get("score") if isinstance(val, dict) else val)
            for code, val in _scores_dict(r.factor_scores).items()
        }
        for r in rows
    }


@tool(
    "get_factor_history",
    "单只基金某因子的时序（raw_value 与 score），用于判断该因子对这只票是否在恶化。",
    _props(code=_code_param("基金代码"),
           factor={"type": "string", "description": "因子代码，如 momentum_6m / inv_volatility"},
           days={"type": "integer", "description": "回溯天数，默认 60，上限 250"}),
)
async def t_get_factor_history(db, code: str, factor: str, days: int = 60) -> list[dict]:
    fund = await _fund_by_code(db, code)
    if not fund:
        raise ValueError(f"基金不存在: {code}")
    since = date.today() - timedelta(days=min(int(days), 250))
    rows = (await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.fund_id == fund.id, AnalysisResult.analysis_date >= since)
        .order_by(AnalysisResult.analysis_date)
    )).scalars().all()
    out = []
    for r in rows:
        val = _scores_dict(r.factor_scores).get(factor)
        if val is None:
            continue
        out.append({
            "date": str(r.analysis_date),
            "score": val.get("score") if isinstance(val, dict) else val,
            "raw_value": val.get("raw_value") if isinstance(val, dict) else None,
        })
    return out


@tool(
    "get_analysis_stats",
    "池级统计：近 N 天信号分布/平均分/翻转次数排行/警告频次。评估策略稳定性用。",
    _props(days={"type": "integer", "description": "统计窗口天数，默认 30"}),
)
async def t_get_analysis_stats(db, days: int = 30) -> dict:
    since = date.today() - timedelta(days=int(days))
    rows = (await db.execute(
        select(AnalysisResult, Fund.code)
        .join(Fund, AnalysisResult.fund_id == Fund.id)
        .where(AnalysisResult.analysis_date >= since)
        .order_by(Fund.code, AnalysisResult.analysis_date)
    )).all()
    dist: dict[str, int] = {}
    warn_dist: dict[str, int] = {}
    scores: list[float] = []
    code_series: dict[str, list] = {}
    for r, code in rows:
        dist[r.signal_direction] = dist.get(r.signal_direction, 0) + 1
        scores.append(r.weighted_score)
        for w in (json.loads(r.quality_warnings) if r.quality_warnings else []):
            key = w.split("：")[0][:20]
            warn_dist[key] = warn_dist.get(key, 0) + 1
        code_series.setdefault(code, []).append((str(r.analysis_date), r.signal_direction))
    from backend.ai.factor_audit import whipsaw_counts
    return {
        "rows": len(rows), "window_days": days,
        "signal_distribution": dist,
        "score_avg": round(sum(scores) / len(scores), 2) if scores else None,
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "warning_frequency": warn_dist,
        "top_whipsaw": whipsaw_counts(code_series)[:10],
    }


@tool(
    "get_backtest_summary",
    "自动回测汇总（backtest_results 表）：净值/策略收益、超额、回撤、信号胜率与有效性。",
    _props(code={"type": "string", "description": "基金代码，可空=全池按有效性排序"},
           limit={"type": "integer", "description": "最多返回条数，默认 20"}),
)
async def t_get_backtest_summary(db, code: str = "", limit: int = 20) -> list[dict]:
    stmt = select(BacktestResult, Fund.code, Fund.name).join(Fund, BacktestResult.fund_id == Fund.id)
    if code:
        stmt = stmt.where(Fund.code == str(code).strip())
    rows = (await db.execute(stmt.order_by(BacktestResult.avg_effectiveness.desc().nullslast())
                             .limit(min(int(limit), 100)))).all()
    return [
        {
            "code": fcode, "name": fname, "period": b.period,
            "total_nav_return": b.total_nav_return, "total_strategy_return": b.total_strategy_return,
            "excess_return": b.excess_return, "max_drawdown": b.max_drawdown,
            "signal_count": b.signal_count, "avg_effectiveness": b.avg_effectiveness,
            "updated_at": str(b.updated_at) if getattr(b, "updated_at", None) else None,
        }
        for b, fcode, fname in rows
    ]


@tool(
    "get_market_snapshot",
    "市场环境三件套：市场概况缓存(资金流/板块/北向/涨跌家数，只读缓存不发网络)、指数估值分位、市场环境快照。",
    _OBJ,
)
async def t_get_market_snapshot(db) -> dict:
    from backend.services.fund_cache_service import get_cached_json
    out: dict = {"market_cache": None, "market_cache_updated_at": None}
    cache, updated_at = await get_cached_json(db, "market_summary")
    if cache:
        # 板块排行只保留前 5，控上下文
        if isinstance(cache.get("sector_flow"), list):
            cache = dict(cache)
            cache["sector_flow"] = cache["sector_flow"][:5]
        out["market_cache"] = cache
        out["market_cache_updated_at"] = updated_at
    else:
        out["market_cache_note"] = "缓存为空（今日尚未刷新），Agent 不代为触发拉取"
    try:
        from backend.services.index_valuation_service import IndexValuationService
        out["index_valuations"] = await IndexValuationService.get_valuations()
    except Exception as e:
        out["index_valuations_error"] = str(e)[:100]
    try:
        from backend.services.market_regime_service import MarketRegimeService
        snap = await MarketRegimeService().get_snapshot()
        out["regime"] = snap.model_dump()
    except Exception as e:
        out["regime_error"] = str(e)[:100]
    return out


@tool(
    "get_otc_trade_status",
    "场外基金申购/赎回状态（开放申购/暂停申购/限大额/封闭期），买入建议可执行性核查。",
    _props(codes={"type": "string", "description": "逗号分隔基金代码，空=返回状态概览统计"}),
)
async def t_get_otc_trade_status(db, codes: str = "") -> dict:
    from backend.services.index_valuation_service import OtcTradeStatusService
    status_map = await OtcTradeStatusService.get_status_map()
    if not status_map:
        return {"available": False, "note": "申购状态数据源暂不可用（熔断/封禁中），无法核查可执行性"}
    wanted = [c.strip() for c in codes.split(",") if c.strip()]
    if wanted:
        return {"available": True, "statuses": {c: status_map.get(c, {"unknown": "不在全市场快照中"}) for c in wanted}}
    buy_counts: dict[str, int] = {}
    for v in status_map.values():
        key = str(v.get("purchase") or "未知")
        buy_counts[key] = buy_counts.get(key, 0) + 1
    return {"available": True, "total": len(status_map), "status_distribution": buy_counts,
            "note": "传 codes 参数查询具体基金"}


@tool(
    "get_holding_overlap",
    "池内基金最新季报重仓股重叠排行（抱团风险）。",
    _props(limit={"type": "integer", "description": "返回条数，默认 15"}),
)
async def t_get_holding_overlap(db, limit: int = 15) -> dict:
    from sqlalchemy.orm import aliased
    funds = (await db.execute(select(Fund).where(Fund.status == "active"))).scalars().all()
    if not funds:
        return {"funds_count": 0, "overlaps": []}
    # 内外层须不同表实例：同模型自关联会令子查询丢掉 FROM（SQLite misuse of aggregate）
    fh_latest = aliased(FundHolding)
    latest_quarter = (
        select(func.max(fh_latest.quarter_label))
        .where(fh_latest.fund_id == FundHolding.fund_id)
        .correlate(FundHolding)
        .scalar_subquery()
    )
    rows = (await db.execute(
        select(
            FundHolding.stock_code,
            func.max(FundHolding.stock_name).label("stock_name"),
            func.count(func.distinct(FundHolding.fund_id)).label("funds_count"),
            func.sum(FundHolding.ratio).label("total_ratio"),
        )
        .where(
            FundHolding.fund_id.in_([f.id for f in funds]),
            FundHolding.quarter_label == latest_quarter,
        )
        .group_by(FundHolding.stock_code)
        .order_by(func.count(func.distinct(FundHolding.fund_id)).desc())
        .limit(min(int(limit), 50))
    )).all()
    return {
        "funds_count": len(funds),
        "overlaps": [
            {"stock_code": r.stock_code, "stock_name": r.stock_name,
             "funds_count": r.funds_count,
             "total_ratio": round(float(r.total_ratio), 2) if r.total_ratio else None}
            for r in rows
        ],
    }


@tool(
    "list_factors",
    "因子配置：代码/名称/权重/方向/标准化方式/启用状态（总权重与评分口径）。",
    _props(include_disabled={"type": "boolean", "description": "默认 false 只列启用因子"}),
)
async def t_list_factors(db, include_disabled: bool = False) -> dict:
    stmt = select(Factor)
    if not include_disabled:
        stmt = stmt.where(Factor.status == "active")
    factors = (await db.execute(stmt.order_by(Factor.sort_order))).scalars().all()
    return {
        "total_weight": round(sum(f.weight for f in factors), 2),
        "score_clamp": 8.5,
        "factors": [
            {"code": f.code, "name": f.name, "weight": f.weight,
             "direction": f.direction, "normalization": f.normalization, "status": f.status}
            for f in factors
        ],
    }


@tool(
    "get_scoring_config",
    "当前评分/质量过滤配置项（阈值、动态阈值增量、场外申购否决开关等，读 system_configs）。",
    _OBJ,
)
async def t_get_scoring_config(db) -> dict:
    rows = (await db.execute(select(SystemConfig))).scalars().all()
    wanted = ("scoring", "quality", "threshold", "otc_pause", "base_buy", "base_sell", "dynamic")
    return {
        c.config_key: (c.config_value[:500] if c.config_value else c.config_value)
        for c in rows
        if any(k in c.config_key.lower() for k in wanted)
    }


@tool(
    "get_review_report",
    "组合区间复盘报告（等权收益 vs 沪深300、最佳最差、信号同向率）。数据来自既有复盘引擎。",
    _props(start_date={"type": "string", "description": "YYYY-MM-DD"},
           end_date={"type": "string", "description": "YYYY-MM-DD，可空=今日"}),
)
async def t_get_review_report(db, start_date: str, end_date: str = "") -> dict:
    from backend.services.review_service import ReviewService
    report = await ReviewService(db).review(
        start_date, end_date or date.today().isoformat(),
    )
    d = report.model_dump()
    d.pop("items", None)  # 明细过大，保留聚合与 summary_md
    d["items_count"] = len(report.items)
    return d


@tool(
    "get_positions",
    "我的真实持仓（手动/CSV 导入）：代码/份额/成本价/最新评分信号；为空表示未录入（调仓分析将走池等权近似）。",
    _OBJ,
)
async def t_get_positions(db) -> list[dict]:
    from backend.services.position_service import list_positions
    rows = await list_positions(db)
    return [
        {"code": r["fund_code"], "name": r["fund_name"], "shares": r["shares"],
         "cost_nav": r["cost_nav"], "source": r["source"],
         "latest_score": r["latest_score"], "latest_signal": r["latest_signal"],
         "fund_type": r["fund_type"]}
        for r in rows
    ]


@tool(
    "get_error_stats",
    "近 N 天数据源错误日志统计（模块×类别计数），诊断数据断供原因。",
    _props(days={"type": "integer", "description": "默认 7 天"}),
)
async def t_get_error_stats(db, days: int = 7) -> dict:
    from backend.services import error_log_service
    logs = error_log_service.ErrorLogStore().query(
        limit=1000, since_ts=(date.today() - timedelta(days=int(days))).isoformat(),
    )
    stat: dict[str, dict[str, int]] = {}
    for item in logs or []:
        module = str(item.get("module", "?"))
        cat = str(item.get("category", "?"))
        stat.setdefault(module, {}).setdefault(cat, 0)
        stat[module][cat] += 1
    return {"days": days, "total": len(logs or []), "by_module": stat}


def registered_names() -> list[str]:
    from backend.ai_tools.registry import _REGISTRY
    return sorted(_REGISTRY.keys())
