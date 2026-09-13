"""概念板块映射服务 — 渐进式 THS 概念成分获取（模块 b 根治方案）

设计（2026-08-31，用户确认"可慢慢获取"）:
- 映射表 concept_board_map（concept × stock 唯一），暴露计算优先查表
- 起步数据: backend/seed_data/concept_ths.json（WorkBuddy 已抓 375 板块
  第一页核心成分，零网络导入）
- 渐进任务 fetch_batch: 每轮只抓"最久未更新"的 N 个板块（默认 20，
  防封禁），每板块间 sleep 2~5s；手动触发，进度可查
- 抓取方法与 WorkBuddy 验证一致: q.10jqka.com.cn/gn/detail/code/{code}
  解析 m-table 第一页成分（每板块核心股 ~10 只）
"""

import json
import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.concept_board_map import ConceptBoardMap

logger = logging.getLogger(__name__)

SEED_FILE = Path(__file__).resolve().parent.parent / "seed_data" / "concept_ths.json"
FETCH_BATCH_SIZE = 20
FETCH_SLEEP_RANGE = (2.0, 5.0)
THS_DETAIL_URL = "http://q.10jqka.com.cn/gn/detail/code/{code}/"

_TOTAL_CONCEPTS: Optional[int] = None  # 概念总数（首次列表抓取后记忆）


async def import_seed(db: AsyncSession, payload: Optional[dict] = None) -> dict:
    """导入概念成分（起步数据）

    Args:
        payload: {"concepts": {概念名: [股票代码...]}}；None 读 seed 文件
    """
    if payload is None:
        if not SEED_FILE.exists():
            return {"imported": 0, "error": "seed 文件不存在"}
        payload = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    concepts = payload.get("concepts", {})
    count = 0
    now = datetime.now()
    for cname, stocks in concepts.items():
        if not isinstance(stocks, (list, set)):
            continue
        for sc in stocks:
            sc = str(sc)
            if not re.match(r"^\d{6}$", sc):
                continue
            exists = (await db.execute(
                select(ConceptBoardMap.id).where(
                    ConceptBoardMap.concept == cname,
                    ConceptBoardMap.stock_code == sc,
                )
            )).first()
            if exists:
                continue
            db.add(ConceptBoardMap(concept=cname, stock_code=sc, updated_at=now))
            count += 1
    await db.commit()
    logger.info(f"概念映射导入完成: +{count} 行")
    return {"imported": count}


async def progress(db: AsyncSession) -> dict:
    """映射进度: 已映射概念数 / 概念总数 / 已映射股票数 / 最近更新"""
    mapped_concepts = (await db.execute(
        select(func.count(func.distinct(ConceptBoardMap.concept)))
    )).scalar() or 0
    total_stocks = (await db.execute(
        select(func.count(func.distinct(ConceptBoardMap.stock_code)))
    )).scalar() or 0
    latest = (await db.execute(
        select(func.max(ConceptBoardMap.updated_at))
    )).scalar()
    total = _TOTAL_CONCEPTS or mapped_concepts  # 未抓列表前用已映射数占位
    return {
        "concepts_mapped": mapped_concepts,
        "concepts_total": total,
        "stocks_mapped": total_stocks,
        "latest_update": str(latest) if latest else None,
    }


async def fetch_batch(db: AsyncSession, batch_size: int = FETCH_BATCH_SIZE) -> dict:
    """渐进抓取一轮：最久未更新的 N 个板块（含未抓取的），防封 sleep

    Returns:
        {fetched, stocks, failed, remaining_hint}
    """
    import asyncio
    global _TOTAL_CONCEPTS

    # 概念列表（THS；失败用库内已有概念名续抓）
    def _list_concepts():
        import akshare as ak
        df = ak.stock_board_concept_name_ths()
        return list(zip(df["名称"] if "名称" in df.columns else df["name"],
                        df["code"] if "code" in df.columns else df["code"]))

    try:
        from backend.data_sources.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter()
        concepts = await adapter._call(_list_concepts, _max_attempts=2)
        if concepts:
            _TOTAL_CONCEPTS = len(concepts)
    except Exception as e:
        logger.warning(f"THS 概念列表获取失败: {e}")
        concepts = None

    # 选批：库内已有概念按 updated_at 升序（最旧优先）；无库内数据时从概念列表取前 N
    existing = (await db.execute(
        select(ConceptBoardMap.concept, func.max(ConceptBoardMap.updated_at))
        .group_by(ConceptBoardMap.concept)
    )).all()
    existing_map = {name: ts for name, ts in existing}

    if concepts:
        all_names = [n for n, _ in concepts]
        code_by_name = {n: c for n, c in concepts}
    else:
        all_names = list(existing_map.keys())
        code_by_name = {}

    batch = sorted(all_names, key=lambda n: existing_map.get(n, datetime.min))[:batch_size]
    if not batch:
        return {"fetched": 0, "stocks": 0, "failed": 0, "remaining_hint": "无概念数据"}

    def _fetch_one(name: str, code: str) -> tuple[str, set[str]]:
        url = THS_DETAIL_URL.format(code=code)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return name, set()
        stocks = set(re.findall(r'>(\d{6})<', resp.text))
        return name, stocks

    fetched = stocks_n = failed = 0
    for i, name in enumerate(batch):
        code = code_by_name.get(name, "")
        if not code:
            continue
        try:
            name_, stocks = await asyncio.to_thread(_fetch_one, name, code)
        except Exception as e:
            logger.warning(f"概念成分抓取失败 {name}: {e}")
            failed += 1
        else:
            now = datetime.now()
            # 覆盖该概念旧行
            await db.execute(
                delete(ConceptBoardMap).where(ConceptBoardMap.concept == name)
            )
            for sc in stocks:
                db.add(ConceptBoardMap(concept=name, stock_code=sc, updated_at=now))
            await db.commit()
            fetched += 1
            stocks_n += len(stocks)
            logger.info(f"概念成分抓取 [{i + 1}/{len(batch)}] {name}: {len(stocks)} 只")
        if i < len(batch) - 1:
            await asyncio.sleep(random.uniform(*FETCH_SLEEP_RANGE))

    return {
        "fetched": fetched,
        "stocks": stocks_n,
        "failed": failed,
        "remaining_hint": f"本轮抓取 {len(batch)} 个板块",
    }


async def get_stock_concepts(
    db: AsyncSession, stock_codes: list[str]
) -> dict[str, list[str]]:
    """查持仓股票的已映射概念 → {stock_code: [概念名]}"""
    if not stock_codes:
        return {}
    rows = (await db.execute(
        select(ConceptBoardMap.stock_code, ConceptBoardMap.concept)
        .where(ConceptBoardMap.stock_code.in_(stock_codes))
    )).all()
    out: dict[str, list[str]] = {}
    for sc, cname in rows:
        out.setdefault(sc, []).append(cname)
    return out
