from __future__ import annotations
"""分析结果查询 + 手动触发路由"""

import json
import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.schemas.common import ApiResponse
from backend.schemas.analysis import (
    FactorScore, AnalysisResultOut,
    AnalysisExportPayload, AnalysisImportResult,
    ReviewReport,
    CompareReport,
)
from backend.schemas.market import MarketSummaryOut, SignalSummary, MarketCapitalFlow, SectorFlowRanking, HSGTFlow, MarketAdvDecline, MarketTurnover, MarketRegimeOut
from backend.utils.timezone import beijing_today

CACHE_KEY_MARKET = "market_summary"

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 导出/导入历史报告 ──────────────────────────────────────────────


@router.get("/export")
async def export_analysis(db: AsyncSession = Depends(get_db)):
    """导出全部分析结果为 JSON 文件"""
    from backend.services.analysis_service import AnalysisService
    svc = AnalysisService(db)
    payload = await svc.export_analysis()
    # 注意：Pydantic v2 的 model_dump_json() 不接受 ensure_ascii 参数，
    # 该参数属于 json.dumps()。改用 model_dump() + json.dumps 保留中文可读。
    data = json.dumps(payload.model_dump(), ensure_ascii=False, indent=2)
    return Response(
        content=data,
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="analysis_export.json"',
        },
    )


@router.post("/import", response_model=ApiResponse[AnalysisImportResult])
async def import_analysis(
    payload: AnalysisExportPayload,
    overwrite: bool = Query(False, description="已存在的记录是否覆盖"),
    db: AsyncSession = Depends(get_db),
):
    """导入分析结果备份"""
    from backend.services.analysis_service import AnalysisService
    svc = AnalysisService(db)
    result = await svc.import_analysis(payload, overwrite=overwrite)
    return ApiResponse(data=result)


def _parse_warnings_json(raw) -> list | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, list) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _result_to_out(r: AnalysisResult, fund: Fund | None = None) -> AnalysisResultOut:
    """ORM → Schema 转换"""
    factor_scores: list[FactorScore] = []
    try:
        raw = json.loads(r.factor_scores) if isinstance(r.factor_scores, str) else r.factor_scores
        if isinstance(raw, dict):
            for code, val in raw.items():
                if isinstance(val, dict):
                    factor_scores.append(FactorScore(
                        factor_code=code,
                        factor_name=val.get("name", code),
                        raw_value=val.get("raw_value", 0),
                        score=val.get("score", 0),
                        direction=val.get("direction", "positive"),
                        data_valid=bool(val.get("data_valid", True)),
                    ))
                else:
                    factor_scores.append(FactorScore(
                        factor_code=code,
                        factor_name=code,
                        raw_value=0,
                        score=float(val),
                        direction="positive",
                    ))
    except (json.JSONDecodeError, TypeError):
        pass

    return AnalysisResultOut(
        id=r.id,
        fund_id=r.fund_id,
        fund_code=fund.code if fund else "",
        fund_name=fund.name if fund else "",
        analysis_date=r.analysis_date,
        weighted_score=r.weighted_score,
        signal_direction=r.signal_direction,
        signal_strength=r.signal_strength or "",
        operation_advice=r.operation_advice or "",
        equity_ratio=getattr(r, "equity_ratio", 0.5),
        factor_scores=factor_scores,
        created_at=r.created_at,
        original_score=getattr(r, "original_score", None),
        dynamic_buy_threshold=getattr(r, "dynamic_buy_threshold", None),
        dynamic_sell_threshold=getattr(r, "dynamic_sell_threshold", None),
        quality_warnings=_parse_warnings_json(getattr(r, "quality_warnings", None)),
        nav_as_of_date=getattr(r, "nav_as_of_date", None),
        pool_size=getattr(r, "pool_size", None),
    )


async def _batch_load_funds(db: AsyncSession, results: list[AnalysisResult]) -> dict[int, Fund]:
    """批量加载基金，避免 N+1 查询。

    一次 SELECT ... WHERE id IN (...) 取回所有需要的 Fund，
    返回 {fund_id: Fund} 映射，供 _result_to_out 使用。
    """
    fund_ids = {r.fund_id for r in results}
    if not fund_ids:
        return {}
    fund_result = await db.execute(select(Fund).where(Fund.id.in_(fund_ids)))
    return {f.id: f for f in fund_result.scalars().all()}


@router.get("", response_model=ApiResponse[list[AnalysisResultOut]])
async def query_analysis(
    date_param: Optional[str] = Query(None, alias="date"),
    fund_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """查询分析结果"""
    stmt = select(AnalysisResult).order_by(AnalysisResult.analysis_date.desc())
    if date_param:
        stmt = stmt.where(AnalysisResult.analysis_date == date_param)
    if fund_id:
        stmt = stmt.where(AnalysisResult.fund_id == fund_id)

    result = await db.execute(stmt)
    results = result.scalars().all()

    fund_map = await _batch_load_funds(db, results)
    out_list = [_result_to_out(r, fund_map.get(r.fund_id)) for r in results]

    return ApiResponse(data=out_list)


@router.get("/latest", response_model=ApiResponse[list[AnalysisResultOut]])
async def get_latest_analysis(db: AsyncSession = Depends(get_db)):
    """获取最新分析结果"""
    # 获取最新日期
    from sqlalchemy import func
    result = await db.execute(select(func.max(AnalysisResult.analysis_date)))
    latest_date = result.scalar()

    if not latest_date:
        return ApiResponse(data=[])

    stmt = select(AnalysisResult).where(AnalysisResult.analysis_date == latest_date)
    result = await db.execute(stmt)
    results = result.scalars().all()

    fund_map = await _batch_load_funds(db, results)
    out_list = [_result_to_out(r, fund_map.get(r.fund_id)) for r in results]

    return ApiResponse(data=out_list)


@router.get("/summary", response_model=ApiResponse[MarketSummaryOut])
async def get_market_summary(db: AsyncSession = Depends(get_db)):
    """获取市场概况汇总 — 先展示缓存，后台刷新"""
    from sqlalchemy import func

    # 1. 获取最新分析日期
    result = await db.execute(select(func.max(AnalysisResult.analysis_date)))
    latest_date = result.scalar()

    today_str = beijing_today().isoformat()
    summary_date = latest_date.isoformat() if latest_date else today_str

    # 2. 获取当日分析结果（始终实时查询，DB 查询很快）
    stmt = select(AnalysisResult).where(AnalysisResult.analysis_date == latest_date)
    result = await db.execute(stmt)
    analysis_list = result.scalars().all()

    signal_summary = SignalSummary(total=len(analysis_list))
    out_list: list[AnalysisResultOut] = []

    fund_map = await _batch_load_funds(db, list(analysis_list))
    for r in analysis_list:
        fund = fund_map.get(r.fund_id)
        out = _result_to_out(r, fund)
        out_list.append(out)
        if out.signal_direction == "buy":
            signal_summary.buy_count += 1
        elif out.signal_direction == "sell":
            signal_summary.sell_count += 1
        else:
            signal_summary.hold_count += 1

    out_list.sort(key=lambda x: x.weighted_score, reverse=True)
    signal_summary.top_buy = [o for o in out_list if o.signal_direction == "buy"][:10]
    signal_summary.top_sell = [o for o in out_list if o.signal_direction == "sell"][-10:]

    # 3. 尝试返回缓存的行情数据
    from backend.services.fund_cache_service import (
        build_market_summary_payload, get_cached_json, market_cache_has_data,
        store_market_summary,
    )
    market_cache, updated_at = await get_cached_json(db, CACHE_KEY_MARKET)

    if market_cache_has_data(market_cache) and updated_at:
        summary = MarketSummaryOut(
            date=summary_date,
            signals=signal_summary,
            market_flow=MarketCapitalFlow(**market_cache["market_flow"]) if market_cache.get("market_flow") else None,
            sector_flow=[SectorFlowRanking(**s) for s in market_cache.get("sector_flow", [])],
            hsgt_flow=HSGTFlow(**market_cache["hsgt_flow"]) if market_cache.get("hsgt_flow") else None,
            adv_decline=MarketAdvDecline(**market_cache["adv_decline"]) if market_cache.get("adv_decline") else None,
            turnover=MarketTurnover(**market_cache["turnover"]) if market_cache.get("turnover") else None,
            updated_at=updated_at,
        )
        return ApiResponse(data=summary)

    # 4. 无缓存 — 全量拉取行情数据
    from backend.services.market_service import MarketService
    import asyncio as _asyncio
    svc = MarketService()
    # 2026-08-29 修复：冷缓存时 5 个源串行最坏 ~4 分钟（超出前端 120s 超时）
    market_flow, sector_flow_raw, hsgt_flow, adv_decline, turnover = await _asyncio.gather(
        svc.get_market_capital_flow(),
        svc.get_sector_flow_rankings(),
        svc.get_hsgt_flow(),
        svc.get_market_adv_decline(),
        svc.get_market_turnover(),
    )

    sector_flow_list = list(sector_flow_raw.values())

    # 5. 写入缓存
    cache_data = build_market_summary_payload(
        market_flow, sector_flow_list, hsgt_flow, adv_decline, turnover
    )
    updated_at = await store_market_summary(db, cache_data)

    summary = MarketSummaryOut(
        date=summary_date,
        signals=signal_summary,
        market_flow=market_flow,
        sector_flow=sector_flow_list,
        hsgt_flow=hsgt_flow,
        adv_decline=adv_decline,
        turnover=turnover,
        updated_at=updated_at,
    )
    return ApiResponse(data=summary)


@router.get("/market-regime", response_model=ApiResponse[MarketRegimeOut])
async def get_market_regime():
    """获取市场环境快照（大盘估值分位/市场情绪/资金面）

    数据源 AKShare，服务层缓存 1 小时；单项失败对应字段为 null。
    """
    try:
        from backend.services.market_regime_service import MarketRegimeService
        snap = await MarketRegimeService().get_snapshot()
        return ApiResponse(data=MarketRegimeOut(
            fetched_at=snap.fetched_at,
            valuation_percentile=snap.valuation_percentile,
            valuation_date=snap.valuation_date,
            valuation_current_pe=snap.valuation_current_pe,
            valuation_sample_points=snap.valuation_sample_points,
            adv_decline_ratio=snap.adv_decline_ratio,
            up_count=snap.up_count,
            down_count=snap.down_count,
            margin_balance=snap.margin_balance,
            margin_change_pct_7d=snap.margin_change_pct_7d,
            margin_date=snap.margin_date,
        ))
    except Exception as e:
        logger.error(f"市场环境快照获取失败: {e}")
        raise HTTPException(status_code=500, detail=f"市场环境数据获取失败: {str(e)}")


@router.post("/refresh-summary", response_model=ApiResponse[dict])
async def refresh_market_summary(db: AsyncSession = Depends(get_db)):
    """后台刷新行情数据并更新缓存"""
    from backend.services.market_service import MarketService
    from backend.services.fund_cache_service import (
        build_market_summary_payload, store_market_summary,
    )

    # 手动刷新：连带解除失败冷却（用户就是要立刻重试）；定时推送路径不清，
    # 避免每天两轮推送各自把已耗尽的降级链再撞一遍
    MarketService.clear_cache(include_failures=True)
    import asyncio as _asyncio
    svc = MarketService()
    market_flow, sector_flow_raw, hsgt_flow, adv_decline, turnover = await _asyncio.gather(
        svc.get_market_capital_flow(),
        svc.get_sector_flow_rankings(),
        svc.get_hsgt_flow(),
        svc.get_market_adv_decline(),
        svc.get_market_turnover(),
    )

    sector_flow_list = list(sector_flow_raw.values())

    cache_data = build_market_summary_payload(
        market_flow, sector_flow_list, hsgt_flow, adv_decline, turnover
    )
    # updated_at 为 None = 五路全空且本地也没有旧缓存
    updated_at = await store_market_summary(db, cache_data)

    return ApiResponse(data={"updated_at": updated_at})


@router.post("/trigger", response_model=ApiResponse[list[AnalysisResultOut]])
async def trigger_analysis(
    body: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
):
    """手动触发分析

    body: {"fund_ids": [1, 2, 3]} 或空对象表示全部
    """
    try:
        from backend.config import settings
        from backend.services.analysis_service import AnalysisService
        svc = AnalysisService(
            db,
            joinquant_user=settings.JOINQUANT_USER,
            joinquant_password=settings.JOINQUANT_PASSWORD,
        )
        fund_ids = body.get("fund_ids") if body else None
        results = await svc.run_analysis(fund_ids=fund_ids)
        return ApiResponse(data=results)
    except Exception as e:
        logger.error(f"触发分析失败: {e}")
        raise HTTPException(status_code=500, detail=f"分析执行失败: {str(e)}")


@router.post("/trigger-stream")
async def trigger_analysis_stream(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """流式触发分析 — 通过 SSE 逐批推送分析结果

    Body: {"fund_ids": [1, 2, 3]} 或空对象表示全部
    Returns: text/event-stream
    """
    from backend.config import settings
    from backend.services.analysis_service import AnalysisService

    svc = AnalysisService(
        db,
        joinquant_user=settings.JOINQUANT_USER,
        joinquant_password=settings.JOINQUANT_PASSWORD,
    )

    body = await request.json() if request.headers.get("content-type") else None
    fund_ids = body.get("fund_ids") if isinstance(body, dict) else None

    async def _event_stream():
        try:
            async for event in svc.run_analysis_streaming(fund_ids=fund_ids):
                yield event
                if await request.is_disconnected():
                    break
        finally:
            # 客户端断开时确保 DB session 归还连接池
            await db.close()

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/review", response_model=ApiResponse[ReviewReport])
async def review_portfolio(
    start_date: str = Query(..., description="起始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
    fund_ids: Optional[str] = Query(None, description="逗号分隔基金 ID，空=全部活跃基金"),
    db: AsyncSession = Depends(get_db),
):
    """投资复盘 — 组合区间收益 vs 基准（沪深300+股息）+ 信号同向率（等权买入持有口径）

    净值走分红复权、基准含股息折算，口径头随 `report.caliber` 返回（Q11）。
    """
    from backend.services.review_service import ReviewService

    ids = (
        [int(x) for x in fund_ids.split(",") if x.strip().isdigit()]
        if fund_ids else None
    )
    svc = ReviewService(db)
    try:
        report = await svc.review(start_date, end_date, fund_ids=ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(data=report)


@router.get("/compare", response_model=ApiResponse[CompareReport])
async def compare_funds(
    fund_ids: str = Query(..., description="逗号分隔基金 ID（2~10 只）"),
    years: int = Query(2, ge=1, le=5, description="近期窗口年数（另一窗口为成立以来）"),
    db: AsyncSession = Depends(get_db),
):
    """基金 PK — 多基金业绩/风险/基准归因（Beta/Alpha/IR）+ 规模/机构对比"""
    from backend.services.fund_compare_service import FundCompareService

    ids = [int(x) for x in fund_ids.split(",") if x.strip().isdigit()]
    if not (2 <= len(ids) <= 10):
        raise HTTPException(status_code=400, detail="请选择 2~10 只基金")
    svc = FundCompareService(db)
    try:
        report = await svc.compare(ids, years=years)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(data=report)


# ── 收益口径（Q11：净值复权 / 基准股息，两个键即两条回滚路径）───────────

def _caliber_state(policy: dict) -> dict:
    from backend.services import caliber_service as cs

    return {
        **policy,
        "keys": {
            "nav_adjusted": cs.NAV_ADJUSTED_CONFIG_KEY,
            "bench_div_yield_pct": cs.BENCH_DIV_YIELD_CONFIG_KEY,
        },
        "defaults": {
            "nav_adjusted": cs.DEFAULT_NAV_ADJUSTED,
            "bench_div_yield_pct": cs.DEFAULT_BENCH_DIV_YIELD_PCT,
        },
        "bench_div_yield_max": cs.BENCH_DIV_YIELD_MAX,
    }


@router.get("/caliber")
async def get_return_caliber(db: AsyncSession = Depends(get_db)):
    """复盘/PK/建议回填当前生效的收益口径

    `review_nav_adjusted=0` 回到裸单位净值；`benchmark_dividend_yield_pct=0`
    回到纯价格指数。股息率是常数、复权只在已取回的序列上做变换 —— 零上游请求。
    """
    from backend.services import caliber_service as cs

    return ApiResponse(data=_caliber_state(await cs.load_caliber(db)))


@router.put("/caliber")
async def update_return_caliber(
    body: dict, db: AsyncSession = Depends(get_db)
):
    """修改生效收益口径（下一轮复盘/PK/回填即生效，历史报告不可比）"""
    from backend.services import caliber_service as cs

    raw_div = body.get("benchmark_dividend_yield_pct")
    raw_nav = body.get("review_nav_adjusted")
    try:
        div = float(raw_div) if raw_div is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="benchmark_dividend_yield_pct 需为数字")
    nav = None
    if raw_nav is not None:
        nav = not str(raw_nav).strip().lower() in ("0", "false", "off", "no", "")
    saved = await cs.save_caliber(db, nav_adjusted=nav, bench_div_yield_pct=div)
    return ApiResponse(data=_caliber_state(saved))


# ── 调仓建议自进化（2026-09-24）──────────────────────────────────────

@router.get("/advice-stats")
async def advice_stats():
    """调仓建议命中率统计与阈值校准状态（by_mode 里 abs/excess 两个口径并存）"""
    from backend.services.advice_learning_service import AdviceLearningStore
    store = AdviceLearningStore()
    return ApiResponse(data=store.stats())


@router.get("/advice-hit-mode")
async def get_advice_hit_mode():
    """调仓建议命中口径（excess=相对沪深300超额 / abs=绝对涨跌旧口径，回滚用）"""
    from backend.services.advice_learning_service import (
        AdviceLearningStore, DEFAULT_HIT_MODE, HIT_MODES)
    store = AdviceLearningStore()
    return ApiResponse(data={
        "hit_mode": store.get_hit_mode(),
        "options": list(HIT_MODES),
        "default": DEFAULT_HIT_MODE,
    })


@router.put("/advice-hit-mode")
async def update_advice_hit_mode(body: dict):
    """切换命中口径（只影响此后回填的样本与校准，历史双口径两列都已在库）"""
    from backend.services.advice_learning_service import AdviceLearningStore
    mode = str(body.get("hit_mode") or "")
    try:
        saved = AdviceLearningStore().set_hit_mode(mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(data={"hit_mode": saved})


@router.post("/advice-eval")
async def advice_evaluate():
    """回填到期建议的实际表现并尝试校准（调度/手动触发）

    口径与上游预算都在 `run_advice_backfill` 里（Q7，2026-10-02）：
    窗口固定为「建议日 → 其后第 30 个净值日」，未走完的样本跳过留到下一轮；
    基准走 `adapter.get_benchmark_series()` 的类级 1h 缓存，净值按基金去重后
    每只取一次 —— 不新增上游请求。
    """
    from backend.services.advice_learning_service import run_advice_backfill
    return ApiResponse(data=await run_advice_backfill())


# ── 影子评分（§3：新口径先只写 shadow_* 列，分歧达标再切生产）────────

@router.get("/shadow-config")
async def get_shadow_config(db: AsyncSession = Depends(get_db)):
    """影子开关/变体与判据常量（判据写死在代码里，不做成可配置项以免事后凑数）

    附带内置变体的口径说明与 2C 的生效参数：读分歧数字的人需要知道"这一列是按
    哪套口径、哪些参数算出来的"，参数写错越界时 `out_of_range` 会直接标出来。
    """
    from backend.engines.shadow_scoring import (
        DIVERGENCE_THRESHOLD_PCT, STABLE_DAYS_REQUIRED, load_shadow_config,
        variant_descriptions)
    # 只为了触发内置变体注册：本端点可能是进程里第一个碰到影子层的地方，
    # 不导入就会把"已实现的口径"报成"注册表为空"
    from backend.engines import shadow_variants  # noqa: F401
    from backend.engines.quality_filter import merge_quality_config

    cfg = await load_shadow_config(db)
    cfg["divergence_threshold_pct"] = DIVERGENCE_THRESHOLD_PCT
    cfg["stable_days_required"] = STABLE_DAYS_REQUIRED
    cfg["variant_descriptions"] = variant_descriptions()
    cfg["caliber_params"] = shadow_variants.effective_params(await merge_quality_config(db))
    return ApiResponse(data=cfg)


@router.put("/shadow-config")
async def update_shadow_config(body: dict, db: AsyncSession = Depends(get_db)):
    """开关影子评分 / 指定变体；未注册的变体名 400（写错就静默停摆最坏）"""
    from backend.engines.shadow_scoring import load_shadow_config, save_shadow_config

    enabled = None
    if "enabled" in body:
        raw = body.get("enabled")
        enabled = not str(raw).strip().lower() in ("0", "false", "off", "no", "")
    variant = None
    if "variant" in body:
        variant = str(body.get("variant") or "").strip()
    try:
        saved = await save_shadow_config(db, enabled=enabled, variant=variant)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(data=saved)


@router.get("/shadow-divergence")
async def get_shadow_divergence(
    days: int = Query(
        10, ge=1, le=60,
        description="回看最近 N 个有影子数据的日期（休市轮次仍展示，但不计入判据一的连续交易日）",
    ),
    db: AsyncSession = Depends(get_db),
):
    """新旧口径每日分歧比例 + 方向迁移矩阵 + 切换判据进度（纯本地 3 条 SQL，零上游请求）"""
    from backend.engines import shadow_variants  # noqa: F401
    # ↑ 只为触发内置变体注册：直接打开分歧卡（没先读 /shadow-config）时，
    #   不导入会把"已实现的 2C 口径"报成"注册表为空"，读数字的人以为口径没上线
    from backend.services.shadow_report_service import get_divergence_report
    return ApiResponse(data=await get_divergence_report(db, days=days))
