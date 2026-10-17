"""持有期与赎回费阶梯（Q10-C + 第五轮审查 #1）— 纯函数，零上游请求

调仓工单原来只量"该不该卖"，不量"卖了要付多少"：持有 3 天的持仓被建议卖出时，
1.5% 的短期赎回费会直接吃掉一段预期改善，而工单上一个字都看不见。本模块把
持有期与阶梯费率算成可展示的字段，供 `ai/rebalance.py` 在卖出/换仓项上标注，
并在费率进入惩罚档时把建议降级为观望。

**阶梯按基金档位分列**（2026-10-17 审查 #1）：原先一条全局阶梯套全池，
场内 ETF（无赎回费，只有佣金）、债券与 C 类份额（满 30 天普遍免赎回费）都被
按权益 A 类的 `0.5% / 0.25%` 计费 —— 后果不是"标错一个数字"，而是
`penalize_pct` 闸门把这些本可直接执行的卖出**误降级成观望**。档位判定走
`utils/fund_asset_class.fee_profile`，与因子/基准口径共用同一份真相。

口径说明（都写死在这里，避免各处各说一套）：
- **持有期按自然日**：基金合同里的"持续持有期少于 7 日收取 1.5% 赎回费"量的就是
  自然日，不是交易日。
- **未填首次买入日 = 未知**，一律返回 None 并跳过费用约束。当成 0 天会把池子里
  所有建议都拦掉（Q10 裁定明确禁止该默认）。
- 配置 `redemption_fee_ladders` 是 `{档位: [[天数,费率%]…]}`；写成旧的**单条
  列表**（`redemption_fee_ladder`）时全档共用那一条，保持改造前语义。
- 档位阶梯是**同类基金的常见区间**，不是本合同实测费率：识别不出来回落权益 A 类
  （最保守的一档），宁可多算费用也不要把"免赎回费"当成假象。

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

# 按档位的默认阶梯（键与 utils.fund_asset_class.FEE_PROFILE_* 一一对应，由测试锁定）
DEFAULT_FEE_LADDERS: dict[str, list] = {
    "equity":         [[7, 1.5], [365, 0.5], [None, 0.25]],
    "equity_c":       [[7, 1.5], [30, 0.5], [None, 0.0]],
    "bond":           [[7, 1.5], [30, 0.5], [None, 0.1]],
    "bond_c":         [[7, 1.5], [30, 0.5], [None, 0.0]],
    "qdii":           [[7, 1.5], [365, 0.5], [None, 0.25]],
    # 场内卖出只有券商佣金，没有基金赎回费；货币基金赎回 0 费
    "exchange_traded": [[None, 0.0]],
    "money_market":   [[None, 0.0]],
}

# 无赎回费档位的展示文案（否则工单只显示"赎回费 0%"，用户不知道是免费还是没数据）
ZERO_FEE_NOTES: dict[str, str] = {
    "exchange_traded": "场内份额：卖出不产生基金赎回费（只有券商佣金，未计入）",
    "money_market": "货币基金：赎回免费率",
}


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


def parse_fee_ladders(raw_ladders, legacy_ladder) -> dict[str, list]:
    """按档位解析阶梯 → `{profile: [(上界或 None, 费率%)]}`

    接受两种配置形状：
    - `redemption_fee_ladders = {"equity": [[7,1.5],…], "bond": […]}` —— 逐档覆盖，
      没写的档位用 `DEFAULT_FEE_LADDERS`；
    - 旧的 `redemption_fee_ladder = [[7,1.5],[365,0.5],[null,0.25]]`（或新键写成列表）
      —— **全档共用同一条**，等价于改造前的单一阶梯语义，便于灰度与回滚。

    档位名拼错的行直接丢弃（回落该档默认），但整份配置全脏时回落 `DEFAULT_FEE_LADDERS`
    并 warning —— 与旧实现同口径：费用约束可以关，不能因配置脏就表现为免赎回费。
    """
    source = raw_ladders if raw_ladders is not None else legacy_ladder
    if isinstance(source, (list, tuple)):
        shared = parse_fee_ladder(source)
        return {k: list(shared) for k in DEFAULT_FEE_LADDERS}  # 同一条阶梯全档共用

    out: dict[str, list] = {}
    if isinstance(source, dict):
        for profile, ladder in source.items():
            key = str(profile or "").strip().lower()
            if key not in DEFAULT_FEE_LADDERS:
                logger.warning(f"未知的赎回费档位 {profile!r}，该配置行忽略")
                continue
            parsed = parse_fee_ladder(ladder)
            out[key] = parsed
    if not out:
        return {k: parse_fee_ladder(v) for k, v in DEFAULT_FEE_LADDERS.items()}
    # 只覆盖部分档位时，其余档位不能跟着"全档共用"跑，各自回到自己的默认
    for k, v in DEFAULT_FEE_LADDERS.items():
        out.setdefault(k, parse_fee_ladder(v))
    return out


def ladder_for_profile(ladders: dict[str, list], profile: str) -> list:
    """取某档位的阶梯；未知档位回落权益档（最保守）"""
    return ladders.get(profile) or ladders.get("equity") or [
        (b, p) for b, p in DEFAULT_FEE_LADDER]


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
    """人类可读阶梯："持有<7天 1.5% / <365天 0.5% / ≥365天 0.25%"

    只有一档且无上界（场内/货币的 `[(None, 0.0)]`）时写"全持有期 0%" ——
    按 `≥天` 模板生成的"≥ None 天"会把档位口径写成乱码。
    """
    parts: list[str] = []
    prev_bound: Optional[int] = None
    for bound, pct in ladder:
        if bound is None:
            head = f"≥{prev_bound}天" if prev_bound is not None else "全持有期"
        else:
            head = f"<{bound}天"
        parts.append(f"{head} {pct:g}%")
        prev_bound = bound
    return " / ".join(parts)


# 档位中文名（工单口径行与配置文档共用，避免"exchange_traded"这种键名直接漏到用户面前）
PROFILE_LABELS: dict[str, str] = {
    "exchange_traded": "场内份额",
    "money_market": "货币基金",
    "bond": "债券型",
    "bond_c": "债券C类",
    "qdii": "QDII/海外",
    "equity": "权益A类",
    "equity_c": "权益C类",
}


def ladders_text(ladders: dict[str, list]) -> str:
    """逐档位口径行：只列**互不相同**的阶梯，同值档位合并点名"""
    grouped: dict[str, list[str]] = {}
    for profile in PROFILE_LABELS:
        key = ladder_text(ladders.get(profile, []))
        if not key:
            continue
        grouped.setdefault(key, []).append(PROFILE_LABELS[profile])
    return "；".join(f"{'、'.join(names)}：{text}" for text, names in grouped.items())


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
