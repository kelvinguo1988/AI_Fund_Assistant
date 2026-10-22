"""场外基金费率解析（2026-10-22）

用途：AI 工作台的 `get_trade_cost` / `get_fund_fee_profile` 需要把东财
自然语言费率表转成可计算的结构。

数据源实测（2026-10-22，akshare `fund_fee_em`）:
  indicator="赎回费率" → 3 行，形如：
      适用期限              赎回费率
      小于7天               1.50%
      大于等于7天，小于30天   0.50%
      大于等于30天           0.00%
  indicator="运作费用" → 键值对形式的字符串表：
      管理费率=0.60%（每年） 托管费率=0.15%（每年） 销售服务费率=0.25%（每年）
  indicator="申购费率" → 该基金在 akshare 侧返回 0 行（C 类通常免申购费），
      调用方要能容忍空表。

注意：解析失败一律返回空结构，不抛异常——费率是辅助信息，不能因为它
让整个工具调用失败。
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# 「小于7天」「大于等于7天，小于30天」「大于等于30天」→ (下限, 上限)
_NUM = r"([0-9]+(?:\.[0-9]+)?)"
_PAT_LT = re.compile(rf"小于\s*{_NUM}\s*天")
_PAT_GE_LT = re.compile(rf"大于等于\s*{_NUM}\s*天[，,]\s*小于\s*{_NUM}\s*天")
_PAT_GE = re.compile(rf"大于等于\s*{_NUM}\s*天")
_PAT_PCT = re.compile(rf"{_NUM}\s*%")


def _pct(text: str) -> Optional[float]:
    m = _PAT_PCT.search(str(text or ""))
    return float(m.group(1)) if m else None


def parse_redemption_tiers(rows: list[dict]) -> list[dict]:
    """赎回费阶梯 → [{min_days, max_days, rate_pct}]（max_days=None 表示无上限）

    按持有天数升序，便于调用方线性查找。
    """
    out: list[dict] = []
    for row in rows or []:
        period = str(row.get("适用期限") or "")
        rate = _pct(row.get("赎回费率"))
        if rate is None:
            continue
        m = _PAT_GE_LT.search(period)
        if m:
            out.append({"min_days": int(float(m.group(1))),
                        "max_days": int(float(m.group(2))), "rate_pct": rate})
            continue
        m = _PAT_LT.search(period)
        if m:
            out.append({"min_days": 0, "max_days": int(float(m.group(1))), "rate_pct": rate})
            continue
        m = _PAT_GE.search(period)
        if m:
            out.append({"min_days": int(float(m.group(1))),
                        "max_days": None, "rate_pct": rate})
    out.sort(key=lambda x: x["min_days"])
    return out


def redemption_rate_for_days(tiers: list[dict], days: int) -> Optional[float]:
    """按持有天数查赎回费率（%）；区间为 [min_days, max_days)"""
    for t in tiers:
        lo, hi = t["min_days"], t["max_days"]
        if days >= lo and (hi is None or days < hi):
            return t["rate_pct"]
    return None


def parse_operating_fees(pairs: dict) -> dict:
    """运作费用 → {管理费率: 0.6, 托管费率: 0.15, 销售服务费率: 0.25}（年化 %）

    akshare 返回的是**扁平位置键**（实测 2026-10-22）：
        {0: '管理费率', 1: '0.60%（每年）', 2: '托管费率', 3: '0.15%（每年）', ...}
    不是 {"管理费率": "0.60%"}。这里按「键名, 值, 键名, 值…」成对读取；
    若调用方给的是普通字典（中文键），也照样支持。
    """
    out: dict = {}
    if not pairs:
        return out
    if any(isinstance(k, str) and "费率" in k for k in pairs):
        for k, v in pairs.items():
            if isinstance(k, str) and "费率" in k:
                rate = _pct(v)
                if rate is not None:
                    out[k] = rate
        return out
    items = [pairs[k] for k in sorted(k for k in pairs if isinstance(k, int))]
    for i in range(0, len(items) - 1, 2):
        name, val = items[i], items[i + 1]
        if isinstance(name, str) and "费率" in name:
            rate = _pct(val)
            if rate is not None:
                out[name] = rate
    return out


def breakeven_growth_pct(
    buy_fee_pct: float, sell_fee_pct: float
) -> Optional[float]:
    """盈亏平衡所需涨幅（%）

    买入实际到手 = 1 - buy/100；卖出到手 = (1+g) * (1 - sell/100)。
    令二者相等解 g：
        (1+g)(1 - s) = 1 - b
        g = (1-b)/(1-s) - 1
    返回百分比；费率异常（≥100%）返回 None。
    """
    b = max(0.0, float(buy_fee_pct or 0)) / 100.0
    s = max(0.0, float(sell_fee_pct or 0)) / 100.0
    if s >= 1.0 or b >= 1.0:
        return None
    return round(((1 - b) / (1 - s) - 1) * 100, 3)
