"""基金业务逻辑 — CRUD + 状态管理"""

import asyncio
import json
import logging
import re
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data_sources.base import guess_fund_type as _guess_fund_type
from backend.models.fund import Fund
from backend.schemas.fund import FundCreate, FundUpdate
from backend.utils.concurrency import run_with_timeout

logger = logging.getLogger(__name__)


def _merge_tags(existing_tags: Optional[str], new_themes: list[str]) -> Optional[str]:
    """将自动抓取的主题合并到已有标签中，去重"""
    if not new_themes:
        return existing_tags
    existing = set()
    if existing_tags:
        existing = {t.strip() for t in existing_tags.split(",") if t.strip()}
    for theme in new_themes:
        existing.add(theme.strip())
    return ",".join(sorted(existing)) if existing else None


# guess_fund_type 从 backend.data_sources.base 导入，与数据源层共享同一份路由规则


class FundService:
    """基金池服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_funds(self, status: Optional[str] = None) -> list[Fund]:
        """获取基金列表

        Args:
            status: 筛选状态，None 表示全部

        Returns:
            基金列表
        """
        stmt = select(Fund).order_by(Fund.id)
        if status:
            stmt = stmt.where(Fund.status == status)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_fund(self, fund_id: int) -> Optional[Fund]:
        """获取单个基金

        Args:
            fund_id: 基金 ID

        Returns:
            Fund 对象或 None
        """
        result = await self.db.execute(select(Fund).where(Fund.id == fund_id))
        return result.scalars().first()

    async def get_fund_by_code(self, code: str) -> Optional[Fund]:
        """根据代码获取基金

        Args:
            code: 基金代码

        Returns:
            Fund 对象或 None
        """
        result = await self.db.execute(select(Fund).where(Fund.code == code))
        return result.scalars().first()

    async def create_fund(self, data: FundCreate) -> Fund:
        """新增基金

        创建后自动从天天基金抓取相关主题并合并到标签中。

        Args:
            data: 基金创建数据

        Returns:
            创建的 Fund 对象

        Raises:
            ValueError: 基金代码已存在
        """
        existing = await self.get_fund_by_code(data.code)
        if existing:
            raise ValueError(f"基金代码 {data.code} 已存在")

        fund = Fund(
            code=data.code,
            name=data.name,
            fund_type=data.fund_type,
            tags=data.tags,
            status="active",
        )
        self.db.add(fund)
        await self.db.commit()
        await self.db.refresh(fund)

        # 仅当未手动填写标签时，自动生成双层标签（官方类型+基准定位 / 持仓暴露）
        if not fund.tags:
            try:
                from backend.services.fund_tag_service import build_double_tags
                tags_result = await run_with_timeout(
                    build_double_tags,
                    fund.code, fund.name or "", fund.fund_type, None,
                    timeout=25.0,
                )
                fund.tags = tags_result["tags"]
                fund.fund_type_official = tags_result["fund_type_official"]
                fund.benchmark_text = tags_result["benchmark_text"]
                fund.exposure_tags = tags_result["exposure_tags"]
                await self.db.commit()
                await self.db.refresh(fund)
            except Exception as e:
                logger.warning("新建基金自动标签失败 %s: %s", fund.code, e)

        return fund

    async def update_fund(self, fund_id: int, data: FundUpdate) -> Optional[Fund]:
        """更新基金信息

        Args:
            fund_id: 基金 ID
            data: 更新数据

        Returns:
            更新后的 Fund 对象或 None
        """
        fund = await self.get_fund(fund_id)
        if fund is None:
            return None

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(fund, key, value)

        await self.db.commit()
        await self.db.refresh(fund)
        return fund

    async def delete_fund(self, fund_id: int) -> bool:
        """删除基金

        Args:
            fund_id: 基金 ID

        Returns:
            是否删除成功
        """
        fund = await self.get_fund(fund_id)
        if fund is None:
            return False

        await self.db.delete(fund)
        await self.db.commit()
        return True

    async def batch_import(self, items: list[dict]) -> dict:
        """批量导入基金

        已有代码跳过不重复创建，其余自动识别类型并创建。
        导入仅做数据库写入（毫秒级返回）；相关主题标签改由后台任务
        `enrich_fund_themes` 异步补全，避免东财网络慢/反爬导致导入请求
        卡死或超时（曾表现为前端"服务器内部错误"）。

        Args:
            items: [{"code": "000001", "name": "示例基金", "tags": "宽基"}]

        Returns:
            {"total": 3, "created": 2, "skipped": ["000001"], "errors": [], "created_codes": [...]}
        """
        created = 0
        skipped: list[str] = []
        errors: list[str] = []
        created_codes: list[str] = []

        logger.info("批量导入 %d 个基金", len(items))
        try:
            for item in items:
                code = str(item.get("code", "")).strip()
                name = str(item.get("name", "")).strip()
                tags = str(item.get("tags", "")).strip() or None

                if not code or not name:
                    errors.append(f"代码或名称为空: {item}")
                    continue
                if not re.match(r"^\d{6}$", code):
                    errors.append(f"代码格式无效: {code}")
                    continue

                try:
                    existing = await self.get_fund_by_code(code)
                    if existing:
                        skipped.append(code)
                        continue

                    fund = Fund(
                        code=code,
                        name=name,
                        fund_type=_guess_fund_type(code),
                        tags=tags,
                        status="active",
                    )
                    self.db.add(fund)
                    await self.db.flush()
                    created_codes.append(code)
                    created += 1
                except Exception as e:
                    errors.append(f"{code}: {e}")
                    continue

            # 单处提交；失败整体回滚，绝不抛 500 给前端
            await self.db.commit()
        except Exception as e:
            logger.exception("批量导入提交失败，已回滚: %s", e)
            try:
                await self.db.rollback()
            except Exception:
                pass
            errors.append(f"数据库提交失败: {e}")
            created = 0
            created_codes = []

        logger.info("批量导入完成: total=%d created=%d skipped=%d errors=%d",
                     len(items), created, len(skipped), len(errors))
        if errors:
            logger.warning("导入失败项: %s", errors)
        return {
            "total": len(items),
            "created": created,
            "skipped": skipped,
            "errors": errors,
            "created_codes": created_codes,
        }

    async def refresh_themes(self, fund_id: int) -> Optional[Fund]:
        """刷新指定基金的双层标签（手动刷新入口，逻辑见 recompute_double_tags）

        手动触发的单只重刷允许双失败兜底写名称解析标签（用户明确要求修）。
        """
        return await recompute_double_tags(self.db, fund_id, allow_fallback_on_fail=True)

    async def batch_update_status(self, ids: list[int], action: str) -> None:
        """批量更新基金状态

        Args:
            ids: 基金 ID 列表
            action: 操作类型 "active" / "disabled"
        """
        if action not in ("active", "disabled"):
            raise ValueError(f"无效的操作类型: {action}")

        stmt = update(Fund).where(Fund.id.in_(ids)).values(status=action)
        await self.db.execute(stmt)
        await self.db.commit()


async def recompute_double_tags(
    db: AsyncSession, fund_id: int, allow_fallback_on_fail: bool = False
) -> Optional[Fund]:
    """重算并持久化单只基金的双层标签（手动刷新/持仓新季度联动/启动自愈共用）

    保护语义（2026-08-30 复查修复）：F10 瞬时失败保留旧 official/benchmark；
    F10+雪球双失败（total）时旧 tags 整体保留，不写名称兜底标签。
    allow_fallback_on_fail（自愈/手动刷新用）：双失败时，对「tags 或基准缺失」
    的脏基金仍写名称解析兜底（坏标签不修等于没修）；字段齐全的基金照旧保留。
    """
    fund = (await db.execute(select(Fund).where(Fund.id == fund_id))).scalars().first()
    if fund is None:
        return None

    from backend.services.fund_holding_service import get_latest_holdings
    from backend.services.fund_tag_service import build_double_tags

    holdings = await get_latest_holdings(db, fund.id, limit=50)
    holds_payload = [
        {"stock_name": h.stock_name, "stock_code": h.stock_code, "ratio": h.ratio}
        for h in holdings
    ]
    # 真概念映射（THS 表，渐进获取；未覆盖股票由关键词并集兜底）
    from backend.services.concept_map_service import get_stock_concepts
    concept_map = await get_stock_concepts(
        db, [h.stock_code for h in holdings]
    )
    result = await run_with_timeout(
        build_double_tags,
        fund.code, fund.name or "", fund.fund_type, holds_payload,
        timeout=25.0, concept_map=concept_map,
    )
    if result.get("_fetch_failed") == "total":
        dirty = fund.tags is None or fund.benchmark_text is None
        if not (allow_fallback_on_fail and dirty):
            await db.refresh(fund)
            return fund
    fund.tags = result["tags"]
    if not result.get("_fetch_failed") or fund.fund_type_official is None:
        fund.fund_type_official = result["fund_type_official"]
    if not result.get("_fetch_failed") or fund.benchmark_text is None:
        fund.benchmark_text = result["benchmark_text"]
    fund.exposure_tags = result["exposure_tags"]
    await db.commit()
    await db.refresh(fund)
    return fund


async def find_dirty_tag_fund_ids(db: AsyncSession) -> list[int]:
    """存量脏标签基金扫描（启动自愈用）：基准缺失 / 暴露从未生成 / 无标签"""
    from sqlalchemy import or_

    rows = (await db.execute(
        select(Fund.id).where(
            Fund.status == "active",
            or_(
                Fund.benchmark_text.is_(None),
                Fund.exposure_tags.is_(None),
                Fund.tags.is_(None),
            ),
        ).order_by(Fund.id)
    )).scalars().all()
    return list(rows)


HEAL_FAIL_KEY = "tag_heal_fail_counts"
HEAL_MAX_ATTEMPTS = 3  # 单只基金自愈失败上限：连续 3 轮无进展即跳过，防每启动全量打网络


def _tag_fingerprint(fund: Fund) -> tuple:
    return (fund.tags, fund.fund_type_official, fund.benchmark_text, fund.exposure_tags)


async def _load_heal_counts(db: AsyncSession) -> dict[str, int]:
    from backend.models.system_config import SystemConfig

    row = (await db.execute(
        select(SystemConfig).where(SystemConfig.config_key == HEAL_FAIL_KEY)
    )).scalars().first()
    if not row:
        return {}
    try:
        data = json.loads(row.config_value or "{}")
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


async def _save_heal_counts(db: AsyncSession, counts: dict[str, int]) -> None:
    from backend.models.system_config import SystemConfig

    value = json.dumps(counts, ensure_ascii=False)
    row = (await db.execute(
        select(SystemConfig).where(SystemConfig.config_key == HEAL_FAIL_KEY)
    )).scalars().first()
    if row:
        row.config_value = value
    else:
        db.add(SystemConfig(config_key=HEAL_FAIL_KEY, config_value=value))
    await db.commit()


async def self_heal_dirty_tags() -> dict:
    """启动自愈（设计③）：逐只限频重刷存量脏标签基金

    F10/概念表抓取均走网络，2s 间隔串行防封禁；单只失败仅告警。
    双源失败时对脏字段写名称兜底（allow_fallback_on_fail）；连续
    HEAL_MAX_ATTEMPTS 轮无进展的基金跳过，不再每启动重复抓取（防封禁）。
    """
    from backend.database import async_session_factory

    stats = {"total": 0, "done": 0, "failed": 0, "skipped": 0}
    async with async_session_factory() as db:
        ids = await find_dirty_tag_fund_ids(db)
        counts = await _load_heal_counts(db)
    stats["total"] = len(ids)
    if not ids:
        return stats
    logger.info("存量标签自愈开始：%d 只基金待重刷", len(ids))
    for fid in ids:
        key = str(fid)
        if counts.get(key, 0) >= HEAL_MAX_ATTEMPTS:
            stats["skipped"] += 1
            continue
        try:
            async with async_session_factory() as db:
                row = (await db.execute(
                    select(Fund).where(Fund.id == fid)
                )).scalars().first()
                before = _tag_fingerprint(row) if row else None
                fund = await recompute_double_tags(db, fid, allow_fallback_on_fail=True)
                after = _tag_fingerprint(fund) if fund is not None else None
                if after is not None and after == before:
                    # 抓取失败且未触发兜底 → 无进展，计数防每启动重复打网络
                    counts[key] = counts.get(key, 0) + 1
                else:
                    counts.pop(key, None)
                    stats["done"] += 1
        except Exception as e:
            stats["failed"] += 1
            counts[key] = counts.get(key, 0) + 1
            logger.warning("存量标签自愈失败 fund_id=%s: %s", fid, e)
        await asyncio.sleep(2.0)
    try:
        async with async_session_factory() as db:
            await _save_heal_counts(db, counts)
    except Exception as e:
        logger.warning("存量标签自愈计数保存失败: %s", e)
    logger.info("存量标签自愈完成: %s", stats)
    return stats


async def enrich_fund_themes(codes: list[str]) -> None:
    """后台任务：为新导入基金异步补全相关主题标签

    原实现在导入请求内同步抓取东财主题，因东财反爬/网络慢，单只即耗时数秒，
    整池导入（百只级）动辄数分钟，前端 120s 超时或网关 504 → 误报"服务器内部错误"。
    现改为请求返回后异步执行：不阻塞导入；任一基金失败仅告警，不影响其余。
    """
    if not codes:
        return
    from backend.database import async_session_factory

    async with async_session_factory() as session:
        for code in codes:
            try:
                result = await session.execute(select(Fund).where(Fund.code == code))
                fund = result.scalars().first()
                # 仅当确实无标签时补全，用户手动填写的不覆盖
                if fund is None or fund.tags:
                    continue
                await recompute_double_tags(session, fund.id)
                logger.info("基金 %s 标签补全完成", code)
            except Exception as e:
                logger.warning("后台标签补全失败 %s: %s", code, e)


def _primary_tag(tags: Optional[str]) -> Optional[str]:
    """取基金的主标签（逗号分隔标签中的第一个，作为分类依据）"""
    if not tags:
        return None
    parts = [t.strip() for t in tags.split(",") if t.strip()]
    return parts[0] if parts else None


def classify_and_sort_funds(funds: list, pin_starred: bool = False) -> list:
    """按标签分类排序（基金池与基金详情共享同一套顺序规则）

    规则：
    1. 主标签 = 逗号分隔标签中的第一个；无标签归为「未分类」。
    2. 分类分组顺序 = 主标签出现频率降序（同类基金多的分类排前面），
       「未分类」永远排在最后；频率相同则按标签名升序。
    3. 同一分类内按基金名称升序（名称相同再按代码升序）。
    4. pin_starred=True 时，星标基金整体置顶，作为独立的「已星标」分组
       （基金池使用）；基金详情传 False，仅按分类排序、不置顶星标。

    Args:
        funds: Fund ORM 对象列表（需含 tags / starred 属性）
        pin_starred: 是否将星标基金置顶

    Returns:
        排序后的 Fund 列表
    """
    from collections import Counter

    freq = Counter(_primary_tag(f.tags) for f in funds if _primary_tag(f.tags))

    def sort_key(f):
        star = 0 if (pin_starred and bool(getattr(f, "starred", False))) else 1
        tag = _primary_tag(f.tags)
        # 分类排序键：(0, -频率, 标签名) 表示有标签；无标签用 (1, 0, "") 永远最后
        cat = (1, 0, "") if tag is None else (0, -freq[tag], tag)
        return (star, cat, (f.name or "", f.code or ""))

    return sorted(funds, key=sort_key)
