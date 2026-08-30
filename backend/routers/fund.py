"""基金 CRUD 路由"""

import asyncio
import json
import logging
import random
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response

logger = logging.getLogger(__name__)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.common import ApiResponse
from backend.schemas.fund import (
    FundCreate, FundUpdate, FundOut, FundPeriodReturn,
    FundHoldingOut, FundManagerOut, FundChangeSummary,
    FundDetailResponse, FundDetailStatus,
)
from backend.services.fund_cache_service import (
    get_cached_period_returns,
    get_last_refreshed_time,
    update_period_returns_cache,
    get_cached_json,
    CACHE_KEY_EXTENDED_DETAIL,
)
from backend.services.fund_holding_service import get_latest_holdings, refresh_holdings
from backend.services.fund_manager_service import get_current_managers, refresh_managers
from backend.services.fund_change_detector import get_fund_changes
from backend.models.fund import Fund
from backend.services.fund_service import FundService, classify_and_sort_funds, enrich_fund_themes
from backend.services.fund_refresh_task import get_refresh_state, run_refresh_all_details

router = APIRouter()

# 后台任务强引用（防止 asyncio.create_task 结果被 GC 回收中途取消）
_refresh_task: Optional[asyncio.Task] = None


@router.get("/export")
async def export_funds(db: AsyncSession = Depends(get_db)):
    """导出基金池为 JSON 文件"""
    svc = FundService(db)
    funds = await svc.list_funds()
    items = [FundOut.model_validate(f).model_dump(mode="json") for f in funds]
    payload = {
        "version": "1.0",
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }
    return Response(
        content=json.dumps(payload, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="funds_export.json"',
        },
    )


@router.get("", response_model=ApiResponse[list[FundOut]])
async def list_funds(
    status: Optional[str] = None,
    order: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """获取基金列表（支持 ?status=active 筛选、?order=classification 按标签分类排序）"""
    svc = FundService(db)
    funds = await svc.list_funds(status=status)
    if order == "classification":
        funds = classify_and_sort_funds(funds, pin_starred=False)
    return ApiResponse(data=[FundOut.model_validate(f) for f in funds])


@router.get("/lookup-name", response_model=ApiResponse[dict])
async def lookup_fund_name(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """根据基金代码查询名称与类型

    优先使用内存/文件缓存的基金名称映射（fund_open_fund_rank_em），
    命中即返回；未命中（多为 ETF，场外排行表不含）再尝试 ETF 行情表兜底。
    网络仅首次触发，后续复用缓存。
    """
    from backend.data_sources.akshare_adapter import AKShareAdapter
    from backend.data_sources.base import guess_fund_type

    adapter = AKShareAdapter()
    name = await adapter._get_cached_fund_name(code)

    if not name:
        # 场外排行表不含 ETF，用 ETF 行情表兜底
        try:
            import akshare as ak
            from backend.utils.concurrency import run_with_timeout

            df = await run_with_timeout(adapter._call, ak.fund_etf_spot_em, timeout=25.0)
            if df is not None and not df.empty:
                match = df[df["代码"] == code]
                if not match.empty:
                    name = str(match.iloc[0]["名称"])
        except Exception as e:
            logger.debug("ETF 名称查询失败 code=%s: %s", code, e)

    fund_type = guess_fund_type(code) if name else None
    return ApiResponse(data={"code": code, "name": name, "fund_type": fund_type})


@router.post("", response_model=ApiResponse[FundOut])
async def create_fund(
    body: FundCreate,
    db: AsyncSession = Depends(get_db),
):
    """新增基金"""
    svc = FundService(db)
    try:
        fund = await svc.create_fund(body)
        return ApiResponse(data=FundOut.model_validate(fund))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{fund_id}", response_model=ApiResponse[FundOut])
async def update_fund(
    fund_id: int,
    body: FundUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新基金"""
    svc = FundService(db)
    fund = await svc.update_fund(fund_id, body)
    if fund is None:
        raise HTTPException(status_code=404, detail="基金不存在")
    return ApiResponse(data=FundOut.model_validate(fund))


@router.delete("/{fund_id}", response_model=ApiResponse[None])
async def delete_fund(
    fund_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除基金"""
    svc = FundService(db)
    ok = await svc.delete_fund(fund_id)
    if not ok:
        raise HTTPException(status_code=404, detail="基金不存在")
    return ApiResponse()


@router.post("/import", response_model=ApiResponse[dict])
async def import_funds(
    body: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """批量导入基金

    body: {
      "items": [
        {"code": "510300", "name": "沪深300ETF", "tags": "宽基,大盘"},
        {"code": "018495", "name": "融通产业趋势臻选股票C"}
      ]
    }
    已有代码自动跳过，返回导入摘要。
    """
    items = body.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="items 不能为空")
    svc = FundService(db)
    result = await svc.batch_import(items)
    # 主题标签补全移出请求主流程，后台异步执行，避免导入因东财网络卡死/超时
    created_codes = result.get("created_codes") or []
    if created_codes:
        background_tasks.add_task(enrich_fund_themes, created_codes)
    return ApiResponse(data=result)


@router.get("/{fund_id}/holdings", response_model=ApiResponse[list[FundHoldingOut]])
async def get_fund_holdings(
    fund_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取基金最新季度持仓"""
    holdings = await get_latest_holdings(db, fund_id)
    return ApiResponse(data=[FundHoldingOut.model_validate(h) for h in holdings])


@router.get("/{fund_id}/manager", response_model=ApiResponse[list[FundManagerOut]])
async def get_fund_manager(
    fund_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取基金当前经理信息"""
    managers = await get_current_managers(db, fund_id)
    return ApiResponse(data=[FundManagerOut.model_validate(m) for m in managers])


@router.post("/refresh-details", response_model=ApiResponse[dict])
async def refresh_all_details(
    db: AsyncSession = Depends(get_db),
):
    """触发后台刷新所有活跃基金的持仓+经理+阶段涨幅+扩展数据

    立即返回，重活在后台 asyncio 任务中执行（避免长耗时请求被前端/网关超时掐断）。
    前端轮询 GET /refresh-details/status 获取实时进度，完成后自动重载详情。

    数据来源：
    - 阶段涨幅+扩展数据: pingzhongdata/{code}.js（批量并发）
    - 股票持仓: AKShare fund_portfolio_hold_em（逐只，3-6s 反爬间隔）
    - 基金经理: AKShare fund_manager_em（全量缓存，逐只匹配）
    """
    state = get_refresh_state()
    if state.status == "running":
        # 已有刷新在跑，直接返回当前任务状态，不重复触发
        return ApiResponse(data={
            "accepted": True,
            "already_running": True,
            "status": state.status,
            "total": state.total,
            "done": state.done,
        })

    # 同步置 running 再启动任务：消除并发 POST 双触发竞态
    #（原先状态由任务协程启动后才重置，两个近似同时的请求都会观察到 idle）
    state.reset_running()

    # 启动后台任务（使用独立 DB 会话，不占用请求级会话）；
    # 持有强引用防止事件循环 GC 取消进行中的任务（CPython 已知陷阱）
    global _refresh_task
    _refresh_task = asyncio.create_task(run_refresh_all_details())
    return ApiResponse(data={
        "accepted": True,
        "already_running": False,
        "status": "running",
    })


@router.get("/refresh-details/status", response_model=ApiResponse[dict])
async def get_refresh_details_status(
    db: AsyncSession = Depends(get_db),
):
    """查询后台刷新任务的实时进度"""
    state = get_refresh_state()
    return ApiResponse(data=state.to_dict())


@router.get("/change-summary", response_model=ApiResponse[list[FundChangeSummary]])
async def get_funds_change_summary(
    db: AsyncSession = Depends(get_db),
):
    """获取所有活跃基金的变更摘要（持仓调仓+经理变更）"""
    svc = FundService(db)
    funds = await svc.list_funds(status="active")
    data: list[FundChangeSummary] = []
    for f in funds:
        try:
            changes = await get_fund_changes(db, f.id)
            data.append(FundChangeSummary(
                fund_id=f.id,
                fund_code=f.code,
                fund_name=f.name,
                **changes,
            ))
        except Exception as e:
            logger.warning("获取基金 %s 变更摘要异常: %s", f.code, e)
    return ApiResponse(data=data)


@router.get("/detail", response_model=ApiResponse[FundDetailResponse])
async def get_funds_detail(
    db: AsyncSession = Depends(get_db),
):
    """获取基金池内所有活跃基金的阶段涨幅（优先返回缓存数据）

    返回 cached=true 时表示是缓存数据，updated_at 为缓存时间。
    前端应显示缓存数据，再在后台调用 POST refresh-details 刷新。

    返回顺序：按标签分类排序（与基金池一致，但详情页不展示分类表头）。
    """
    svc = FundService(db)
    funds = await svc.list_funds(status="active")
    if not funds:
        return ApiResponse(data=FundDetailResponse())

    # 按标签分类排序（星标不置顶，仅分类顺序）
    ordered = classify_and_sort_funds(funds, pin_starred=False)
    ordered_codes = [f.code for f in ordered]
    ordered_set = set(ordered_codes)

    # 尝试从缓存读取
    cached_data, updated_at = await get_cached_period_returns(db)
    if cached_data:
        cached_by_code = {item.get("code"): item for item in cached_data}
        ordered_items = [cached_by_code[c] for c in ordered_codes if c in cached_by_code]
        # 兜底：缓存中存在但当前活跃列表缺失的项（如刚停用）追加在末尾
        extra = [item for item in cached_data if item.get("code") not in ordered_set]
        ordered_items.extend(extra)
        return ApiResponse(data=FundDetailResponse(
            funds=[FundPeriodReturn(**item) for item in ordered_items],
            updated_at=updated_at,
        ))

    # 无缓存时直接抓取（按分类顺序）
    codes = ordered_codes
    name_map = {f.code: f.name for f in ordered}

    # 2026-08-29 修复：原先 fetch_period_returns 与 update_period_returns_cache
    # 内部各抓一次全部 pingzhongdata JS——冷缓存时请求数翻倍（最易触发反爬的路径）
    # 改为复用缓存写入的返回值
    returns, _js_texts = await update_period_returns_cache(db, codes, name_map)

    data = [
        FundPeriodReturn(
            code=code,
            name=name_map.get(code, ""),
            **returns.get(code, {}),
        )
        for code in codes
    ]
    new_updated = await get_last_refreshed_time(db)

    return ApiResponse(data=FundDetailResponse(
        funds=data,
        updated_at=new_updated,
    ))


@router.get("/detail/status", response_model=ApiResponse[FundDetailStatus])
async def get_funds_detail_status(
    db: AsyncSession = Depends(get_db),
):
    """获取基金详情缓存状态 — 前端用于判断是否需要刷新"""
    cached_data, updated_at = await get_cached_period_returns(db)
    state = get_refresh_state()
    return ApiResponse(data=FundDetailStatus(
        has_cache=bool(cached_data),
        updated_at=updated_at,
        refreshing=(state.status == "running"),
    ))


@router.post("/{fund_id}/refresh-themes", response_model=ApiResponse[FundOut])
async def refresh_fund_themes(
    fund_id: int,
    db: AsyncSession = Depends(get_db),
):
    """重新抓取天天基金相关主题并更新标签"""
    svc = FundService(db)
    fund = await svc.refresh_themes(fund_id)
    if fund is None:
        raise HTTPException(status_code=404, detail="基金不存在")
    return ApiResponse(data=FundOut.model_validate(fund))


@router.get("/extended-detail", response_model=ApiResponse[dict])
async def get_funds_extended_detail(
    db: AsyncSession = Depends(get_db),
):
    """获取所有活跃基金的扩展详情（累计收益走势/规模变动/持有人结构/资产配置）

    优先返回缓存数据，无缓存时返回空。
    数据在 refresh-details 时同步更新。
    """
    data, updated_at = await get_cached_json(db, CACHE_KEY_EXTENDED_DETAIL)
    if data is None:
        return ApiResponse(data={})
    return ApiResponse(data={"funds": data, "updated_at": updated_at})


@router.patch("/batch", response_model=ApiResponse[None])
async def batch_update_funds(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """批量启用/停用基金

    body: {"ids": [1, 2, 3], "action": "active" / "disabled"}
    """
    ids = body.get("ids", [])
    action = body.get("action", "")
    if not ids or action not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="参数错误：ids 非空且 action 为 active/disabled")
    svc = FundService(db)
    await svc.batch_update_status(ids, action)
    return ApiResponse()


@router.get("/realtime", response_model=ApiResponse[dict])
async def get_funds_realtime(
    force: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """获取全部活跃基金的实时净值预估（涨跌百分比）

    数据源（以场外基金为主，三级降级）:
    - fundgz:      天天基金官方盘中估值（精度最高，部分网络被反爬时不可用）
    - holdings_est: 持仓×个股实时快照自算（主力兜底，coverage<0.5 时指数混合）
    - etf_spot:    场内 ETF 实时行情（真实价格）

    结果缓存 60s；force=true 跳过缓存。
    """
    result = await db.execute(select(Fund).where(Fund.status == "active"))
    funds = list(result.scalars().all())
    if not funds:
        return ApiResponse(data={})

    from backend.services.fund_realtime_service import FundRealtimeService
    svc = FundRealtimeService(db)
    data = await svc.get_realtime(funds, force=force)
    return ApiResponse(data=data)
