"""基金资产类别判定 — A 股口径因子/基准/费率的唯一真相（第五轮审查 2026-10-17）

三件事共用这一份口径，否则"什么算 A 股股权类"会在质量过滤、命中率、赎回费里各写一套：

1. **市场三因子是否适用**（`a_share_market_proxy`）：沪深300 估值分位、全市场涨跌家数、
   A 股资金面，对纳指 QDII / 纯债 / 黄金基金不是"中性参考"而是**无关噪声**。
2. **沪深300 作基准是否可比**（`hs300_comparable`）：Q7 的超额命中与 Q11 的复盘超额，
   对海外基金量的是"美股跑赢 A 股"，对债基量的是"牛市里卖出必命中"。
3. **赎回费阶梯选哪一档**（`fee_profile`）：场内 ETF 根本没有赎回费（只有佣金），
   债券与 C 类份额满 30 天普遍免赎回费，套默认 `[[7,1.5],[365,0.5],[∞,0.25]]`
   会把可执行的卖出建议误降级成"观望"。

判据只用库里已有的名称/标签/官方类型文本，**零上游请求**。

口径纪律：
- 识别不出来一律回落 `ASSET_A_SHARE_EQUITY`（= 改造前的行为）。宁可保留旧行为，
  也不要因为文本没写"海外"就把一只基金的因子关掉 —— 那会让信号无声漂移。
- 商品/货币的判断**排在债券之前**：`指数型-其他` 的黄金ETF 与"商品型-非QDII"
  都不该被当成 A 股股权类，而 `债券型-混合二级` 必须走债基阶梯。
"""

from __future__ import annotations

import re
from typing import Optional

# ── 资产类别 ────────────────────────────────────────────────────────────
ASSET_A_SHARE_EQUITY = "a_share_equity"   # A 股股权类（股票/偏股混合/股票指数）
ASSET_OVERSEAS_EQUITY = "overseas_equity"  # 海外股权类（QDII/互认/港股通主题）
ASSET_BOND = "bond"
ASSET_MONEY_MARKET = "money_market"
ASSET_COMMODITY = "commodity"             # 黄金/原油/商品期货指数

# 沪深300 可当基准的类别：只有 A 股股权类。其余一律走绝对涨跌口径。
_HS300_COMPARABLE = {ASSET_A_SHARE_EQUITY}

_OVERSEAS_PATTERNS = (
    "QDII", "海外", "境外", "国际", "全球", "跨国",
    "纳斯达克", "纳指", "标普", "标准普尔", "美国", "中概", "港股", "恒生",
    "德国", "法国", "日本", "英国", "越南", "印度", "沙特", "东南亚", "大中华",
    "H股", "H30", "SP500", "NASDAQ",
)
_COMMODITY_PATTERNS = (
    "黄金", "上海金", "原油", "石油", "天然气", "商品", "有色", "豆粕", "贵金属",
    "GOLD", "OIL",
)
_BOND_PATTERNS = ("债券型", "纯债", "债基", "定开债")

# 96 开头 = 港澳互认基金（非公募代码段），披露与标的都在 A 股之外
_MUTUAL_RECOGNITION_PREFIX = "96"

# 份额类别后缀：…C / …（C）/ …C类
_SHARE_CLASS_RE = re.compile(r"[（(]?([ABCDEYH]{1,2})[)）]?[类份]?$")


def classify_asset(name: Optional[str],
                   fund_type_official: Optional[str],
                   tags: Optional[str] = None,
                   code: Optional[str] = None) -> str:
    """判资产类别（文本口径，识别不出来按 A 股股权类处理）

    `tags` 只作**商品/货币**的辅助判据，不参与海外判定：标签是"持仓暴露"，
    A 股主动基金写"含港股"很常见（实测池内 6 只 混合型-偏股 因此被误判成
    海外基金，占走 QDII 档费率并退出 A 股因子），而官方类型 `混合型-偏股`
    才是资产类别的真相。
    """
    blob = " ".join(x or "" for x in (name, fund_type_official)).upper()
    tag_blob = " ".join(x or "" for x in (name, fund_type_official, tags)).upper()
    code_str = str(code or "")

    if "货币" in tag_blob or "MMF" in tag_blob:
        return ASSET_MONEY_MARKET
    if any(p.upper() in blob for p in _COMMODITY_PATTERNS):
        return ASSET_COMMODITY
    if code_str.startswith(_MUTUAL_RECOGNITION_PREFIX):
        return ASSET_OVERSEAS_EQUITY
    if any(p.upper() in blob for p in _OVERSEAS_PATTERNS):
        return ASSET_OVERSEAS_EQUITY
    if any(p in blob for p in _BOND_PATTERNS) or (fund_type_official or "").startswith("债券"):
        return ASSET_BOND
    return ASSET_A_SHARE_EQUITY


def hs300_comparable(asset_class: str) -> bool:
    """沪深300（价格指数 + 股息常数）能否当该基金的比较基准"""
    return asset_class in _HS300_COMPARABLE


# 基准口径的机器键（落进 advice_log.benchmark_kind，也写进复盘报表）
BENCHMARK_HS300_EXCESS = "hs300_excess"   # 与沪深300 比同区间超额
BENCHMARK_ABS = "abs"                     # 只看绝对涨跌（无可比基准时的回落口径）


def benchmark_kind(asset_class: str) -> str:
    """该资产类别应使用的命中/超额基准口径

    空串 = 调用方没判过（历史行、外部调用）→ 按改造前的 excess 口径处理，
    这样 `benchmark_kind IS NULL` 的旧样本统计语义不变。
    """
    if asset_class == "":
        return BENCHMARK_HS300_EXCESS
    return BENCHMARK_HS300_EXCESS if hs300_comparable(asset_class) else BENCHMARK_ABS


def a_share_market_proxy(asset_class: str) -> bool:
    """A 股大盘估值分位 / 涨跌家数 / 资金面三因子是否适用

    空串 = 调用方没判过（历史数据、外部调用）→ 按适用处理，保持改造前行为。
    """
    return asset_class in ("", ASSET_A_SHARE_EQUITY)


def share_class(name: Optional[str]) -> str:
    """份额类别后缀（A/C/…）；识别不出来返回空串"""
    m = _SHARE_CLASS_RE.search((name or "").strip())
    return m.group(1) if m else ""


# ── 赎回费阶梯档位 ──────────────────────────────────────────────────────
FEE_PROFILE_EXCHANGE_TRADED = "exchange_traded"
FEE_PROFILE_MONEY_MARKET = "money_market"
FEE_PROFILE_BOND = "bond"
FEE_PROFILE_BOND_C = "bond_c"
FEE_PROFILE_EQUITY_C = "equity_c"
FEE_PROFILE_QDII = "qdii"
FEE_PROFILE_EQUITY = "equity"        # 默认档 = 历史沿用阶梯

_FEE_ASSET_PROFILES = {
    ASSET_MONEY_MARKET: FEE_PROFILE_MONEY_MARKET,
    ASSET_BOND: FEE_PROFILE_BOND,
    ASSET_COMMODITY: FEE_PROFILE_EQUITY,     # 商品 ETF 走场内档；场外按默认保守
    ASSET_OVERSEAS_EQUITY: FEE_PROFILE_QDII,
    ASSET_A_SHARE_EQUITY: FEE_PROFILE_EQUITY,
}


def fee_profile(name: Optional[str],
                fund_type_official: Optional[str],
                tags: Optional[str] = None,
                code: Optional[str] = None,
                fund_type: Optional[str] = None) -> str:
    """选赎回费阶梯档位（场内优先：卖出只有佣金，没有赎回费）"""
    if (fund_type or "").strip().lower() == "etf":
        return FEE_PROFILE_EXCHANGE_TRADED
    asset = classify_asset(name, fund_type_official, tags, code)
    if asset == ASSET_BOND:
        return FEE_PROFILE_BOND_C if share_class(name) == "C" else FEE_PROFILE_BOND
    if asset == ASSET_A_SHARE_EQUITY and share_class(name) == "C":
        return FEE_PROFILE_EQUITY_C
    return _FEE_ASSET_PROFILES.get(asset, FEE_PROFILE_EQUITY)
