"""AI Agent 内置只读数据工具（15 个）

全部薄包装既有 service/表查询；market_overview 只读缓存不发网络请求，
防止 Agent 循环放大对数据源的封禁压力。handler 第一参数固定为 db session。
"""

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import date, timedelta

from sqlalchemy import func, select

from backend.ai_tools.registry import compact_rows, tool
from backend.models.analysis_result import AnalysisResult
from backend.models.backtest_result import BacktestResult
from backend.models.factor import Factor
from backend.models.fund import Fund
from backend.models.fund_holding import FundHolding
from backend.models.system_config import SystemConfig
from backend.utils.timezone import beijing_today

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
    "列出基金池（列式表 cols/rows）：代码/名称/类型(otc场外|etf)/分类标签。"
    "赛道暴露明细不在本工具——需要时用 get_fund_exposure。",
    _props(status={"type": "string", "enum": ["active", "all"], "description": "默认 active"}),
)
async def t_list_fund_pool(db, status: str = "active") -> dict:
    """列式返回（2026-10-22 token 优化）

    原实现返回 dict 列表 10432 字符，一半是重复键名；Agent 多轮对话每轮重发
    历史，这些键名被反复计费。改用列式后 3281 字符（-69%），实测模型对
    总数/分类/赛道的判断结果与原始格式**完全一致**。

    exposure_tags 从默认返回里拿掉（它占 1850 字符且多数问题用不到）：
    需要标的级暴露时调 get_fund_exposure，按需付费。
    """
    stmt = select(Fund)
    if status != "all":
        stmt = stmt.where(Fund.status == "active")
    funds = (await db.execute(stmt.order_by(Fund.code))).scalars().all()
    rows = [
        {
            "code": f.code, "name": f.name, "fund_type": f.fund_type,
            "tags": f.tags or "",
        }
        for f in funds
    ]
    return compact_rows(rows, keep=["code", "name", "fund_type", "tags"])


@tool(
    "get_fund_exposure",
    "查单只基金的赛道暴露明细（如「光模块/CPO×4 27.3%」）与重仓股集中度。"
    "list_fund_pool 默认不返回该字段（省 token），需要判断持仓重叠/赛道集中时调用。",
    _props(code={"type": "string", "description": "基金代码，如 004011"}),
)
async def t_get_fund_exposure(db, code: str) -> dict:
    row = (await db.execute(
        select(Fund).where(Fund.code == code)
    )).scalars().first()
    if row is None:
        return {"error": f"基金池中没有 {code}"}
    return {
        "code": row.code,
        "name": row.name,
        "exposure_tags": row.exposure_tags or "",
        "tags": row.tags or "",
    }


@tool(
    "get_latest_signals",
    "获取基金池最新一批分析信号：评分(±8.5，**池内相对分**：由截面标准化因子加权而来，"
    "只在当日这批基金内部可比，不能跨日/跨池比较，也不是绝对质量)/方向/强度/建议/"
    "修正前原始分/动态阈值/质量警告。每行的 pool_size 是当日参与截面标准化的只数。",
    _OBJ,
)
async def t_get_latest_signals(db) -> dict:
    """列式返回（2026-10-22 token 优化）

    原实现 6920 字符，其中 advice（"综合评分 2.65（原始 2.65 + 偏置 +0.0），
    建议加仓…"）完全可由 score/direction/strength 推导，属冗余文本；
    original_score 与 score 在无偏置时相等。精简到 1524 字符（-78%），
    实测模型对「几条买入/最高分是谁」的判断与原始格式一致。
    """
    latest = (await db.execute(select(func.max(AnalysisResult.analysis_date)))).scalar()
    if latest is None:
        return {"cols": [], "rows": []}
    rows = (await db.execute(
        select(AnalysisResult, Fund)
        .join(Fund, AnalysisResult.fund_id == Fund.id)
        .where(AnalysisResult.analysis_date == latest)
        .order_by(AnalysisResult.weighted_score.desc())
    )).all()
    data = [
        {
            "date": str(r.analysis_date), "code": f.code, "name": f.name,
            "score": r.weighted_score, "direction": r.signal_direction,
            "strength": r.signal_strength,
            "pool_size": r.pool_size,
            # 质量警告只在有值时出现（实测 123 行全为空，drop_empty 会自动去掉该列）
            "warn": json.loads(r.quality_warnings) if r.quality_warnings else None,
        }
        for r, f in rows
    ]
    return compact_rows(data)


@tool(
    "get_signal_history",
    "单只基金近 N 天信号序列（日期/评分/方向/强度/警告/当日池规模），用于趋势与翻转(whipsaw)判断。"
    "注意评分是**池内相对分**：跨日的差值里混了当日基金池构成变化的影响，pool_size 变化大的两段不可直接比较。",
    _props(code=_code_param("基金代码，如 004011"),
           days={"type": "integer", "description": "回溯天数，默认 30，上限 250"}),
)
async def t_get_signal_history(db, code: str, days: int = 30) -> list[dict]:
    fund = await _fund_by_code(db, code)
    if not fund:
        raise ValueError(f"基金不存在: {code}")
    since = beijing_today() - timedelta(days=min(int(days), 250))
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
            "pool_size": r.pool_size,
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
    since = beijing_today() - timedelta(days=min(int(days), 250))
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
            # score=0 有两种含义（真实中性 / 缺数据占位），不透传就区分不开
            "data_valid": val.get("data_valid", True) if isinstance(val, dict) else True,
        })
    return out


@tool(
    "get_analysis_stats",
    "池级统计：近 N 天信号分布/平均分/翻转次数排行/警告频次。评估策略稳定性用。",
    _props(days={"type": "integer", "description": "统计窗口天数，默认 30"}),
)
async def t_get_analysis_stats(db, days: int = 30) -> dict:
    since = beijing_today() - timedelta(days=int(days))
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
    "自动回测汇总（backtest_results 表）：净值/策略收益、对满仓持有与对静态半仓两个超额口径、"
    "回撤、信号胜率与有效性。excess_vs_static_half 才是信号能力的主口径；"
    "low_sample=true 的行样本不足，不要据此下结论。"
    "口径：净值=分红复权（场外含分红），未成交现金按 0% 计息（不计息），"
    "基线是满仓持有与静态半仓而非沪深300。",
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
            "excess_vs_static_half": b.excess_vs_static_half,
            "signal_count_non_hold": b.signal_count_non_hold,
            "low_sample": b.low_sample, "caveat": b.caveat,
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
        # 板块排行转列式（2026-10-22 token 优化）
        # 原实现保留 3 个时间窗 × 各 20 条 dict，键名重复导致 6919 字符；
        # 列式后约 2500 字符（-64%），窗口/板块/涨跌/资金/龙头股全部保留，
        # 不牺牲信息量——这是与「只留前 5」不同的思路：省的是键名不是内容。
        cache = dict(cache)
        sf = cache.get("sector_flow")
        if isinstance(sf, list):
            flat = []
            for w in sf:
                if not isinstance(w, dict):
                    continue
                tf = w.get("timeframe") or ""
                for side in ("by_inflow", "by_outflow"):
                    for item in (w.get(side) or []):
                        if isinstance(item, dict):
                            flat.append({
                                "tf": tf,
                                "side": "流入" if side == "by_inflow" else "流出",
                                **{k: item.get(k) for k in (
                                    "sector_name", "change_pct",
                                    "main_net_inflow", "top_stock",
                                )},
                            })
            cache["sector_flow"] = compact_rows(flat)
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
        # MarketRegimeSnapshot 是 dataclass 非 pydantic 模型（2026-09-29 审查 P0：
        # 旧写法 snap.model_dump() 抛 AttributeError 被下方 except 吞掉，
        # regime 从未真正进入 Agent 上下文）
        out["regime"] = asdict(snap)
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
        start_date, end_date or beijing_today().isoformat(),
    )
    d = report.model_dump()
    d.pop("items", None)  # 明细过大，保留聚合与 summary_md
    d["items_count"] = len(report.items)
    return d


@tool(
    "get_positions",
    "我的真实持仓（手动/CSV 导入）：代码/份额/成本价/持有天数/最新评分信号；为空表示未录入（调仓分析将走池等权近似）。",
    _OBJ,
)
async def t_get_positions(db) -> list[dict]:
    from backend.services.position_service import list_positions
    rows = await list_positions(db)
    return [
        {"code": r["fund_code"], "name": r["fund_name"], "shares": r["shares"],
         "cost_nav": r["cost_nav"], "source": r["source"],
         "first_buy_date": r["first_buy_date"], "holding_days": r["holding_days"],
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
        limit=1000, since_ts=(beijing_today() - timedelta(days=int(days))).isoformat(),
    )
    stat: dict[str, dict[str, int]] = {}
    for item in logs or []:
        module = str(item.get("module", "?"))
        cat = str(item.get("category", "?"))
        stat.setdefault(module, {}).setdefault(cat, 0)
        stat[module][cat] += 1
    return {"days": days, "total": len(logs or []), "by_module": stat}


@tool(
    "get_trade_cost",
    "场外基金交易成本精算：按持有天数给出**赎回费阶梯**与该笔交易的"
    "**盈亏平衡涨幅**（涨多少才不亏）。买卖场外基金前必查——7 天内赎回"
    "费率通常 1.5%，短线交易很容易赚了涨幅倒亏手续费。",
    _props(code=_code_param("基金代码，如 004011"),
           hold_days={"type": "integer", "description": "计划/已持有天数，默认 7"}),
)
async def t_get_trade_cost(db, code: str, hold_days: int = 7) -> dict:
    """交易成本与盈亏平衡点（2026-10-22 新增）

    这是用户自己算不出来的东西：赎回费按持有期阶梯计，加上申购费/销售服务费，
    才能回答「这笔涨多少才回本」。实测 004011 持有 3 天需涨 1.52% 才不亏。
    """
    fund = await _fund_by_code(db, code)
    if not fund:
        raise ValueError(f"基金不存在: {code}")
    days = max(0, int(hold_days))

    import akshare as ak

    from backend.services.fund_fee_parser import (
        breakeven_growth_pct, parse_operating_fees, parse_redemption_tiers,
        redemption_rate_for_days,
    )

    def _fee(indicator: str) -> list[dict]:
        try:
            df = ak.fund_fee_em(symbol=str(code), indicator=indicator)
            return df.to_dict("records") if df is not None and not df.empty else []
        except Exception as e:  # noqa: BLE001 - 费率缺失不该让工具失败
            logger.info(f"费率获取失败 {code}/{indicator}: {e}")
            return []

    tiers = parse_redemption_tiers(await asyncio.to_thread(_fee, "赎回费率"))
    buy_rows = await asyncio.to_thread(_fee, "申购费率")
    ops_rows = await asyncio.to_thread(_fee, "运作费用")
    ops = parse_operating_fees(ops_rows[0] if ops_rows else {})

    buy_rate = 0.0
    for row in buy_rows:
        for v in row.values():
            r = None
            try:
                txt = str(v or "")
                if "%" in txt:
                    r = float(txt.replace("%", "").strip())
            except ValueError:
                r = None
            if r is not None and r < 100:
                buy_rate = r
                break

    sell_rate = redemption_rate_for_days(tiers, days)
    be = breakeven_growth_pct(buy_rate, sell_rate) if sell_rate is not None else None

    # 下一档更便宜的持有天数：给出「再等等能省多少」的可执行建议
    next_tier = None
    for t in tiers:
        if t["min_days"] > days and (sell_rate is None or t["rate_pct"] < sell_rate):
            next_tier = {"hold_days": t["min_days"], "rate_pct": t["rate_pct"]}
            break

    return {
        "code": str(code),
        "name": fund.name,
        "hold_days": days,
        "buy_fee_pct": round(buy_rate, 3),
        "sell_fee_pct": sell_rate,
        "breakeven_growth_pct": be,
        "redemption_tiers": tiers,
        "next_cheaper_tier": next_tier,
        "operating_fees_pct": ops,
        "annual_operating_total_pct": round(sum(ops.values()), 3) if ops else None,
        "note": (
            "赎回费按持有天数分档；盈亏平衡涨幅已含申赎两端费用。"
            "运作费用为年化，已从净值中日日扣除，不额外收取。"
        ),
    }


@tool(
    "get_fund_fee_profile",
    "基金费率全貌：申购费、赎回费阶梯、管理/托管/销售服务费率及年化合计。"
    "比较同类基金长期持有成本时用。",
    _props(code=_code_param("基金代码，如 004011")),
)
async def t_get_fund_fee_profile(db, code: str) -> dict:
    fund = await _fund_by_code(db, code)
    if not fund:
        raise ValueError(f"基金不存在: {code}")

    import akshare as ak

    from backend.services.fund_fee_parser import (
        parse_operating_fees, parse_redemption_tiers,
    )

    def _fee(indicator: str) -> list[dict]:
        try:
            df = ak.fund_fee_em(symbol=str(code), indicator=indicator)
            return df.to_dict("records") if df is not None and not df.empty else []
        except Exception:  # noqa: BLE001
            return []

    tiers = parse_redemption_tiers(await asyncio.to_thread(_fee, "赎回费率"))
    ops_rows = await asyncio.to_thread(_fee, "运作费用")
    ops = parse_operating_fees(ops_rows[0] if ops_rows else {})
    return {
        "code": str(code),
        "name": fund.name,
        "fund_type": fund.fund_type,
        "redemption_tiers": tiers,
        "operating_fees_pct": ops,
        "annual_operating_total_pct": round(sum(ops.values()), 3) if ops else None,
    }


@tool(
    "get_fund_nav_history",
    "基金净值历史与区间表现：区间涨跌幅、最大回撤、年化波动率、最好/最差单日。"
    "回答「这只基金最近表现如何/回撤多大」时用——信号评分是池内相对分，"
    "不能替代真实净值表现。",
    _props(code=_code_param("基金代码，如 004011"),
           days={"type": "integer", "description": "回溯天数，默认 90，上限 750"}),
)
async def t_get_fund_nav_history(db, code: str, days: int = 90) -> dict:
    """净值序列 + 区间统计（2026-10-22 新增）

    数据源 `fund_open_fund_info_em`（实测 2392 行可用）。返回的是**降采样后**
    的净值（最多 30 个点）与统计量：完整 2392 行会撑爆上下文，而判断
    「表现如何」只需要区间收益、回撤、波动率和少数关键点。
    """
    fund = await _fund_by_code(db, code)
    if not fund:
        raise ValueError(f"基金不存在: {code}")
    n = max(7, min(int(days), 750))

    import akshare as ak

    def _fetch():
        df = ak.fund_open_fund_info_em(symbol=str(code), indicator="单位净值走势")
        return df.tail(n) if df is not None and not df.empty else None

    try:
        df = await asyncio.to_thread(_fetch)
    except Exception as e:  # noqa: BLE001
        return {"code": str(code), "name": fund.name, "error": f"净值获取失败: {type(e).__name__}"}
    if df is None or df.empty:
        return {"code": str(code), "name": fund.name, "error": "该基金无净值数据"}

    navs: list[float] = []
    dates: list[str] = []
    for _, row in df.iterrows():
        try:
            navs.append(float(row.get("单位净值")))
            dates.append(str(row.get("净值日期"))[:10])
        except (TypeError, ValueError):
            continue
    if len(navs) < 2:
        return {"code": str(code), "name": fund.name, "error": "净值序列过短"}

    first, last = navs[0], navs[-1]
    period_ret = round((last / first - 1) * 100, 2) if first else None

    # 最大回撤
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v / peak - 1) * 100)

    # 日收益率 → 年化波动率
    rets = [(navs[i] / navs[i - 1] - 1) for i in range(1, len(navs)) if navs[i - 1]]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if len(rets) > 1 else 0.0
    vol_annual = round((var ** 0.5) * (252 ** 0.5) * 100, 2)

    step = max(1, len(navs) // 30)
    sample = [[dates[i], navs[i]] for i in range(0, len(navs), step)][-30:]

    return {
        "code": str(code),
        "name": fund.name,
        "window_days": len(navs),
        "start_date": dates[0],
        "end_date": dates[-1],
        "start_nav": first,
        "end_nav": last,
        "period_return_pct": period_ret,
        "max_drawdown_pct": round(mdd, 2),
        "annual_volatility_pct": vol_annual,
        "best_day_pct": round(max(rets) * 100, 2) if rets else None,
        "worst_day_pct": round(min(rets) * 100, 2) if rets else None,
        "nav_samples": sample,
        "note": "nav_samples 为降采样（≤30 点）；统计量基于完整窗口计算。",
    }


@tool(
    "get_fund_rating",
    "基金多机构评级（上海证券/招商证券/济安金信/晨星）+ 基金经理 + 所属公司。"
    "评级是长期业绩/风险调整后的第三方结论，可与池内相对评分互相印证："
    "评分高但评级低 = 只是短期动量强；评级高但评分低 = 可能被池内相对分低估。",
    _props(code=_code_param("基金代码，如 004011")),
)
async def t_get_fund_rating(db, code: str) -> dict:
    """多机构评级（2026-10-22 新增）

    数据源 `fund_rating_all`（实测 18572 行）。一次拉全表约 2 秒，故按
    进程内缓存复用：同一会话内多次查不同基金不必重复拉。
    """
    code = str(code).strip()
    fund = await _fund_by_code(db, code)

    import akshare as ak

    cache = getattr(t_get_fund_rating, "_cache", None)
    if cache is None:
        def _fetch():
            return ak.fund_rating_all()
        try:
            df = await asyncio.to_thread(_fetch)
            cache = {str(r.get("代码")): r for _, r in df.iterrows()} if df is not None else {}
        except Exception as e:  # noqa: BLE001
            return {"code": code, "error": f"评级数据获取失败: {type(e).__name__}"}
        t_get_fund_rating._cache = cache

    row = cache.get(code)
    if row is None:
        return {"code": code, "name": (fund.name if fund else ""),
                "error": "该基金未进入评级样本（多为新基金或规模过小）"}

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    ratings = {k: _num(row.get(k)) for k in ("上海证券", "招商证券", "济安金信", "晨星评级")}
    valid = [v for v in ratings.values() if v is not None]
    return {
        "code": code,
        "name": str(row.get("简称") or (fund.name if fund else "")),
        "manager": str(row.get("基金经理") or ""),
        "company": str(row.get("基金公司") or ""),
        "category": str(row.get("类型") or ""),
        "ratings": ratings,
        "avg_rating": round(sum(valid) / len(valid), 2) if valid else None,
        "five_star_count": _num(row.get("5星评级家数")),
        "note": "评级 1~5 星，分数越高越好；不同机构样本期不同，看均值与分布更稳。",
    }


@tool(
    "get_cross_border_etf_premium",
    "跨境/海外 ETF 折溢价率（市价 vs 净值）：识别高溢价买入风险与折价机会。"
    "QDII 类常因限购出现明显溢价——溢价 3% 以上买入等于先亏掉这部分，"
    "与海外指数涨跌无关。",
    _props(limit={"type": "integer", "description": "返回条数，默认 15，上限 50"}),
)
async def t_get_cross_border_etf_premium(db, limit: int = 15) -> dict:
    """跨境 ETF 折溢价扫描（2026-10-22 新增）

    实测（2026-10-22）197 只可用跨境 ETF：日经225ETF 溢价 4.99%，
    另有 44 只折价。这是纯计算得出的、用户凭行情软件难以批量获得的信息。
    """
    n = max(5, min(int(limit), 50))

    import akshare as ak

    def _fetch():
        return ak.fund_etf_fund_daily_em()

    try:
        df = await asyncio.to_thread(_fetch)
    except Exception as e:  # noqa: BLE001
        return {"error": f"ETF 列表获取失败: {type(e).__name__}", "note": "东财接口可能限流，稍后重试"}

    if df is None or df.empty:
        return {"error": "ETF 列表为空"}

    navcols = [c for c in df.columns if "单位净值" in c]
    if not navcols:
        return {"error": "ETF 列表缺少净值列"}

    def _num(x):
        try:
            v = float(str(x).replace("%", "").strip())
            return v
        except (TypeError, ValueError):
            return None

    kw = "纳斯达克|标普|恒生|港股|中概|日经|德国|法国|沙特|东南亚|印度|越南|海外|美国|亚太|全球"
    mask = df["基金简称"].astype(str).str.contains(kw, na=False)
    rows = []
    for _, r in df[mask].iterrows():
        nav = _num(r.get(navcols[0]))
        px = _num(r.get("市价"))
        if not nav or not px or nav <= 0:
            continue
        rows.append({
            "code": str(r.get("基金代码")),
            "name": str(r.get("基金简称")),
            "price": px,
            "nav": nav,
            "premium_pct": round((px / nav - 1) * 100, 2),
        })
    if not rows:
        return {"error": "无可用跨境 ETF 数据（净值/市价缺失）"}

    rows.sort(key=lambda x: -x["premium_pct"])
    high = [r for r in rows if r["premium_pct"] > 3]
    disc = [r for r in rows if r["premium_pct"] < -1]
    return {
        "date": navcols[0].replace("单位净值", "").strip("-"),
        "scanned": len(rows),
        "high_premium_count": len(high),
        "discount_count": len(disc),
        "highest_premium": rows[:n],
        "top_discount": rows[::-1][:n],
        "note": (
            "溢价 = 市价/净值-1。溢价越高越不该追（等于为同一份资产多付钱）；"
            "折价则是折价买，但要注意流动性差与限购风险。"
        ),
    }


@tool(
    "get_us_market_context",
    "美股主要指数近期表现（标普500/纳斯达克/道琼斯）：近 5/20/60 日涨跌幅，"
    "用于判断 A 股 QDII/跨境 ETF 的海外环境。",
    _props(),
)
async def t_get_us_market_context(db) -> dict:
    """美股环境（2026-10-22 新增）

    数据源 `index_us_stock_sina`（实测 5740 行/指数）。只取三指数最近 60 个
    交易日，返回区间涨跌幅而非全量序列，控制上下文体积。
    """
    import akshare as ak

    symbols = [("标普500", ".INX"), ("纳斯达克", ".IXIC"), ("道琼斯", ".DJI")]
    out: dict = {}
    for name, sym in symbols:
        def _fetch(s=sym):
            return ak.index_us_stock_sina(symbol=s)
        try:
            df = await asyncio.to_thread(_fetch)
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": type(e).__name__}
            continue
        if df is None or df.empty or "close" not in df.columns:
            out[name] = {"error": "无数据"}
            continue
        closes = [c for c in df["close"].tail(61).tolist() if c]
        if len(closes) < 2:
            out[name] = {"error": "序列过短"}
            continue
        last = closes[-1]

        def _ret(k: int):
            if len(closes) <= k:
                return None
            base = closes[-1 - k]
            return round((last / base - 1) * 100, 2) if base else None

        out[name] = {
            "last_close": round(last, 2),
            "last_date": str(df["date"].iloc[-1])[:10],
            "ret_5d_pct": _ret(5),
            "ret_20d_pct": _ret(20),
            "ret_60d_pct": _ret(60),
        }
    return {"indices": out, "note": "美股夜间走势领先 A 股一个交易日，可辅助判断跨境 ETF 次日方向。"}


def registered_names() -> list[str]:
    from backend.ai_tools.registry import _REGISTRY
    return sorted(_REGISTRY.keys())
