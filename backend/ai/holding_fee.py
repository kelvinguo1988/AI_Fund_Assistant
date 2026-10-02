"""持有期与赎回费阶梯（Q10-C，2026-10-02 第二批 2B）— 纯函数，零上游请求

调仓工单原来只量"该不该卖"，不量"卖了要付多少"：持有 3 天的持仓被建议卖出时，
1.5% 的短期赎回费会直接吃掉一段预期改善，而工单上一个字都看不见。本模块把
持有期与阶梯费率算成可展示的字段，供 `ai/rebalance.py` 在卖出/换仓项上标注，
并在费率进入惩罚档时把建议降级为观望。

口径说明（都写死在这里，避免各处各说一套）：
- **持有期按自然日**：基金合同里的"持续持有期少于 7 日收取 1.5% 赎回费"量的就是
  自然日，不是交易日。
- **未填首次买入日 = 未知**，一律返回 None 并跳过费用约束。当成 0 天会把池子里
  所有建议都拦掉（Q10 裁定明确禁止该默认）。
- 阶梯 `[[7,1.5],[365,0.5],[null,0.25]]` 读自 `QUALITY_CONFIG["redemption_fee_ladder"]`
  （可经 `quality_filter_config` 覆盖），任何脏值回落默认阶梯。

详见 docs/QUANT_DECISIONS_2026-10.md §5.1（裁定 B+A+C，C 分两步）。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

# [持有自然日上界(不含), 赎回费率%]，最后一条 None = 不限持有期的基础费率
DEFAULT_FEE_LADDER: list = [[7, 1.5], [365, 0.5], [None, 0.25]]

FEE_PCT_MAX = 100.0


def parse_fee_ladder(raw) -> list[tuple[Optional[int], float]]:
    """阶梯归一化 → `[(上界或 None, 费率%)]`，按上界升序、None 收尾

    接受 `[[7,1.5],[365,0.5],[null,0.25]]` 这种 JSON 形状。逐行校验，脏行丢弃；
    全部无效（配置写错/类型不对）时回落 DEFAULT_FEE_LADDER —— 费用约束可以
    关掉，但不能因为配置脏就把费率算成 0。
    """
    ladder: list[tuple[Optional[int], float]] = []
    if isinstance(raw, (list, tuple)):
        for row in raw:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                continue
            bound, pct = row
            days: Optional[int]
            if bound is None:
                days = None
            else:
                try:
                    days = int(bound)
                except (TypeError, ValueError):
                    continue
                if days <= 0:
                    continue
            try:
                fee = float(pct)
            except (TypeError, ValueError):
                continue
            if not 0.0 <= fee <= FEE_PCT_MAX:
                continue
            ladder.append((days, fee))
    if not ladder:
        logger.warning(f"赎回费阶梯配置无效（{raw!r}），回落默认阶梯")
        return [(b, p) for b, p in DEFAULT_FEE_LADDER]
    ladder.sort(key=lambda x: (x[0] is None, x[0] if x[0] is not None else 0))
    return ladder


def fee_pct_for_holding(holding_days: Optional[int],
                        ladder: list[tuple[Optional[int], float]]) -> Optional[float]:
    """持有 N 个自然日适用的赎回费率%；持有期未知返回 None（不做费用约束）"""
    if holding_days is None:
        return None
    for bound, pct in ladder:
        if bound is None or holding_days < bound:
            return pct
    return ladder[-1][1] if ladder else None


def days_to_next_fee_tier(holding_days: Optional[int],
                          ladder: list[tuple[Optional[int], float]]) -> int:
    """再持有几个自然日能落到下一档（已在最低档 → 0），供观望项给确认天数"""
    if holding_days is None:
        return 0
    for bound, _pct in ladder:
        if bound is not None and holding_days < bound:
            return max(1, bound - holding_days)
    return 0


def ladder_text(ladder: list[tuple[Optional[int], float]]) -> str:
    """人类可读阶梯："持有<7天 1.5% / <365天 0.5% / ≥365天 0.25%" """
    parts: list[str] = []
    prev_bound: Optional[int] = None
    for bound, pct in ladder:
        if bound is None:
            head = f"≥{prev_bound}天" if prev_bound is not None else "其余"
        else:
            head = f"<{bound}天"
        parts.append(f"{head} {pct:g}%")
        prev_bound = bound
    return " / ".join(parts)


def holding_days_between(first_buy_date: Optional[date], as_of: date) -> Optional[int]:
    """首次买入日 → 工单日的自然日数；未填首买日返回 None（不默认 0 天）"""
    if first_buy_date is None:
        return None
    return max(0, (as_of - first_buy_date).days)


def parse_date_flex(raw) -> Optional[date]:
    """宽松解析用户/CSV 里的日期：2026-09-01 / 2026/9/1 / 20260901 / 2026年9月1日

    解析不出来就返回 None（= 未填，持有期未知），不做"猜"——把 09-01 当成某年
    或把脏字符串当成 0 天，都会让赎回费约束凭空生效或凭空失效。
    """
    s = str(raw or "").strip()
    if not s or s in ("-", "--", "None", "null", "nan"):
        return None
    for sep in ("/", "年", "月"):
        s = s.replace(sep, "-")
    s = s.replace("日", "").split(" ")[0].split("T")[0]
    parts = s.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        try:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            return None
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:]))
        except ValueError:
            return None
    return None
