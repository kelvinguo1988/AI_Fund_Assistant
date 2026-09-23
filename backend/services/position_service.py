"""我的持仓服务：CRUD 辅助 + CSV 导入解析

兼容支付宝/天天基金等常见导出格式：按表头别名探测列，不依赖列顺序。
仅解析文本，不碰网络；基金代码必须已在基金池中，否则该行进 errors 跳过。
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.models.user_position import UserPosition
from backend.utils.timezone import now_beijing

logger = logging.getLogger(__name__)

# 表头别名 → 字段（全部小写、去空格后匹配）
CODE_ALIASES = {"基金代码", "证券代码", "代码", "产品代码", "场内基金代码", "code"}
SHARES_ALIASES = {"持有份额", "基金份额", "份额", "保有份额", "持仓份额", "数量", "shares"}
COST_ALIASES = {"持仓成本价", "成本价", "单位成本", "估算成本价", "持仓单位成本", "成本净值", "cost"}
WEIGHT_ALIASES = {"持仓占比", "占组合比例", "占比", "权重"}  # 识别但忽略：权重由份额×成本推算


def _norm_header(h: str) -> str:
    return (h or "").strip().strip("\ufeff").strip()


def _parse_number(raw: str) -> Optional[float]:
    s = (raw or "").strip().replace(",", "").replace("，", "")
    s = s.rstrip("%").replace("元", "")
    if not s or s in ("-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_positions_csv(text: str) -> tuple[list[dict], list[str]]:
    """解析 CSV 文本 → (rows, errors)。

    rows: [{code, shares, cost_nav}]，errors 为逐行跳过原因。
    无表头自动探测：首行若无任何别名列，则视为「代码,份额[,成本]」裸数据。
    """
    errors: list[str] = []
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\ufeff")
    if not text.strip():
        return [], ["CSV 内容为空"]

    reader = csv.reader(io.StringIO(text))
    rows_raw = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows_raw:
        return [], ["CSV 内容为空"]

    header = [_norm_header(c) for c in rows_raw[0]]
    idx: dict[str, int] = {}
    for i, h in enumerate(header):
        key = h.lower()
        if key in {a.lower() for a in CODE_ALIASES}:
            idx.setdefault("code", i)
        elif key in {a.lower() for a in SHARES_ALIASES}:
            idx.setdefault("shares", i)
        elif key in {a.lower() for a in COST_ALIASES}:
            idx.setdefault("cost", i)
        elif key in {a.lower() for a in WEIGHT_ALIASES}:
            pass
    if "code" in idx:
        data_rows = rows_raw[1:]
        has_header = True
    else:
        # 无表头：按 代码,份额[,成本] 顺序
        data_rows = rows_raw
        idx = {"code": 0, "shares": 1, "cost": 2}
        has_header = False
        errors.append("未识别到表头，按「基金代码,持有份额[,成本价]」列序解析")

    out: list[dict] = []
    for n, r in enumerate(data_rows, start=(2 if has_header else 1)):
        code = (r[idx["code"]] if idx["code"] < len(r) else "").strip()
        # 兼容 "004011-" / 引号残留 与数字型代码 110011.0
        code = code.strip('"').split("-")[0].split(".")[0]
        if not code.isdigit() or len(code) != 6:
            errors.append(f"第{n}行：基金代码「{code or '空'}」不是 6 位数字，已跳过")
            continue
        si = idx.get("shares", -1)
        shares = _parse_number(r[si]) if 0 <= si < len(r) else None
        if shares is None or shares <= 0:
            errors.append(f"第{n}行({code})：份额缺失或≤0，已跳过")
            continue
        cost = None
        if idx.get("cost", -1) >= 0 and idx["cost"] < len(r):
            cost = _parse_number(r[idx["cost"]])
            if cost is not None and cost <= 0:
                cost = None
        out.append({"code": code, "shares": shares, "cost_nav": cost})
    return out, errors


async def list_positions(db: AsyncSession) -> list[dict]:
    """持仓列表（附基金信息与最新一期评分/信号，便于前端权重展示）"""
    rows = (await db.execute(
        select(UserPosition, Fund)
        .join(Fund, UserPosition.fund_id == Fund.id)
        .order_by(UserPosition.updated_at.desc())
    )).all()
    if not rows:
        return []
    fund_ids = [p.fund_id for p, _ in rows]
    latest: dict[int, AnalysisResult] = {}
    ar = (await db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.fund_id.in_(fund_ids))
        .order_by(AnalysisResult.analysis_date.desc())
    )).scalars().all()
    for r in ar:
        latest.setdefault(r.fund_id, r)
    return [
        {
            "id": p.id, "fund_id": p.fund_id, "fund_code": f.code, "fund_name": f.name,
            "shares": p.shares, "cost_nav": p.cost_nav, "source": p.source,
            "fund_type": f.fund_type, "status": f.status,
            "latest_score": latest[p.fund_id].weighted_score if p.fund_id in latest else None,
            "latest_signal": latest[p.fund_id].signal_direction if p.fund_id in latest else None,
            "updated_at": p.updated_at.isoformat(timespec="seconds") if p.updated_at else None,
        }
        for p, f in rows
    ]


async def upsert_position(
    db: AsyncSession, fund_code: str, shares: float,
    cost_nav: Optional[float] = None, source: str = "manual",
) -> dict:
    """按基金代码建仓/覆盖更新。代码不在池中抛 ValueError。"""
    fund = (await db.execute(
        select(Fund).where(Fund.code == str(fund_code).strip())
    )).scalars().first()
    if not fund:
        raise ValueError(f"基金不在基金池: {fund_code}")
    if shares is None or float(shares) <= 0:
        raise ValueError("份额必须大于 0")
    p = (await db.execute(
        select(UserPosition).where(UserPosition.fund_id == fund.id)
    )).scalars().first()
    created = p is None
    if p is None:
        p = UserPosition(fund_id=fund.id, shares=float(shares), cost_nav=cost_nav, source=source)
        db.add(p)
    else:
        p.shares = float(shares)
        p.cost_nav = cost_nav
        p.source = source
        p.updated_at = now_beijing()
    await db.commit()
    return {"fund_id": fund.id, "fund_code": fund.code, "created": created}
