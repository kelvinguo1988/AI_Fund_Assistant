"""双层标签服务 — 主标签（官方定位）+ 副标签（当前持仓暴露）

背景（2026-08-30）：原实现抓天天基金"相关主题基金"区（relatedThemeFund）当
标签用，实测严重失真——该区语义是"当前重仓股所属概念板块"，导致固收+ 基金
（004011 中债 85%+股票 15%，Top5 持仓全 <1%）被标 CPO/光模块，互认基金无档案。
现拆为两层：

主标签（稳定定位，永不漂移）:
  F10 基本概况页（fundf10.eastmoney.com/jbgk_{code}.html）官方类型 + 业绩基准
  → 基准关键词解析定位（固收+/红利/主题指数/宽基…）→ 名称关键词补充。
  互认基金（968 开头等）F10 无档案 → 名称解析 + "互认基金"标记兜底。

副标签（动态暴露，随季报变动）:
  库内 fund_holdings 最新季度持仓 → 内置产业链关键词映射 → 赛道聚合计数与占比。
"""

import logging
import re
from collections import defaultdict
from typing import Optional

import requests

from backend.data_sources.base import guess_fund_type

logger = logging.getLogger(__name__)

F10_URL = "https://fundf10.eastmoney.com/jbgk_{code}.html"

# ── 主标签解析规则 ────────────────────────────────────────────────────

# 业绩基准关键词 → 定位标签（按优先级，先命中先得）
_BENCH_RULES: list[tuple[str, str]] = [
    (r"高股息|红利|股息率", "红利/高股息"),
    (r"中证智能制造|智能制造", "高端制造"),
    (r"数字经济|人工智能|科创", "数字经济/科技"),
    (r"半导体|芯片", "半导体"),
    (r"新能源|光伏|碳中和|电池", "新能源"),
    (r"消费", "消费"),
    (r"医疗|医药|健康", "医药"),
    (r"军工|国防", "军工"),
    (r"纳斯达克|标普|美国", "美股QDII"),
    (r"恒生|港股|沪港深", "含港股"),
    (r"黄金|商品", "商品"),
    (r"(?:中证|上证|国证)?A?500|沪深300|中证800|中证1000|创业板指|科创50|综合指数", "宽基指数"),
    (r"中债|存款利率|定期存款", "固收打底"),
]

# 基金类型直接映射的定位词
_TYPE_TAGS = {
    "货币型": ["货币"],
    "债券型": ["债券"],
    "混合型-偏债": ["混合", "偏债"],
    "混合型-灵活": ["混合", "灵活配置"],
    "混合型-偏股": ["混合", "偏股"],
    "股票型": ["股票"],
    "指数型-股票": ["指数"],
    "指数型-债券": ["指数", "债券"],
    "QDII": ["QDII"],
    "FOF": ["FOF"],
    "REITs": ["REITs"],
}

# 名称关键词 → 主题标签（F10 数据缺失时的兜底，如互认基金）
_NAME_RULES: list[tuple[str, str]] = [
    (r"稳健|回报|稳益|固收|安心|增利", "偏债稳健"),
    (r"红利|股息|分红", "红利/高股息"),
    (r"数字经济|算力|人工智能|AI", "数字经济/科技"),
    (r"半导体|芯片|存储|集成电路", "半导体"),
    (r"消费电子", "消费电子"),
    (r"新材料|固态电池|锂|光伏|新能源", "新材料/新能源"),
    (r"制造升级|先进制造|智能制造|高端装备", "高端制造"),
    (r"人形机器人|机器人", "机器人"),
    (r"医药|医疗|健康|生物", "医药"),
    (r"军工|国防", "军工"),
    (r"消费|食品|饮料|白酒", "消费"),
    (r"科技|信息|创新", "科技"),
    (r"光模块|通信|5G", "通信/光模块"),
    (r"价值|红利|优享", "价值"),
    (r"指数增强|增强", "指数增强"),
    (r"沪深300|中证500|中证1000|中证A500|宽基|创业板|科创板|中证全指", "宽基指数"),
    (r"纳指|标普|美元|中概|恒生|港股|全球|海外|亚洲", "QDII跨境"),
]

# ── 副标签：持仓赛道关键词映射（股票名称 → 产业链）──────────────────
_EXPOSURE_RULES: list[tuple[str, str]] = [
    (r"旭创|新易盛|源杰|天孚|光迅|仕佳|德科立|太辰光|剑桥", "光模块/CPO"),
    (r"寒武纪|中芯|华虹|海光|澜起|兆易|韦尔|卓胜微|圣邦", "半导体/算力芯片"),
    (r"工业富联|浪潮|紫光|中科曙光|拓维", "AI服务器"),
    (r"宁德|亿纬|国轩|欣旺达|阳光电源|隆基|通威|晶澳", "新能源"),
    (r"贵州茅台|五粮液|泸州老窖|山西汾酒|洋河", "白酒"),
    (r"保险|人寿|平安|太保|新华", "保险"),
    (r"银行|工商|建设|招商银行|兴业|宁波银行", "银行"),
    (r"铜|铝|神火|紫金|江铜|云铝|洛阳钼业", "有色"),
    (r"迈瑞|恒瑞|药明|爱尔|片仔癀", "医药"),
    (r"中航|沈飞|航发|西飞|洪都", "军工"),
    (r"腾讯|阿里|美团|快手|网易|百度", "互联网"),
    (r"美的|格力|海尔|海信", "家电"),
    (r"长江电力|华能|国电|三峡", "电力"),
    (r"宁德|比亚迪|长城汽车|赛力斯|长安汽车", "汽车"),
]


def parse_primary_tags(
    official_type: Optional[str],
    benchmark: Optional[str],
    fund_name: str,
    fund_type: Optional[str] = None,
) -> tuple[list[str], Optional[str]]:
    """解析主标签

    Returns:
        (tags, position_tag) — tags 为分类词列表；position_tag 为从基准
        识别出的主题/风格定位（独立返回供前端对比展示）
    """
    tags: list[str] = []
    position_tag: Optional[str] = None

    # 1. 官方类型 → 基础定位
    if official_type:
        tags.append(official_type)
        for key, words in _TYPE_TAGS.items():
            if official_type.startswith(key):
                tags.extend(w for w in words if w not in tags)
                break

    # 2a. 固收+/偏债 特判（优先于主题规则）：
    #     中债占比 ≥50%，或基准无任何 ≥50% 的指数占比（纯存款/纯债类，如 001194
    #     "1年期定存利率+3%"）。023299（指数×95%+活期×5%）不能因含"存款利率"误判。
    if benchmark:
        m_bond = re.search(r"中债[^*×]*指数收益率[×*](\d+(?:\.\d+)?)%", benchmark)
        index_ratios = [int(float(x)) for x in re.findall(r"指数收益率[×*](\d+(?:\.\d+)?)%", benchmark)]
        has_bond_dominant = bool(m_bond and float(m_bond.group(1) or 0) >= 50)
        no_dominant_index = (not index_ratios) or max(index_ratios) < 50
        if has_bond_dominant or no_dominant_index:
            position_tag = "固收+/偏债"
            if position_tag not in tags:
                tags.append(position_tag)

    # 2b. 业绩基准 → 主题/风格定位（position_tag 未定时才判）
    if benchmark and position_tag is None:
        for pat, label in _BENCH_RULES:
            if re.search(pat, benchmark):
                position_tag = label
                if label not in tags:
                    tags.append(label)
                break


    # 4. 名称关键词补充（主题词在名称中时最可靠）
    name = fund_name or ""
    for pat, label in _NAME_RULES:
        if re.search(pat, name) and label not in tags:
            tags.append(label)

    # 5. 场内 ETF 简化
    if fund_type == "etf" and "ETF" in name and "宽基指数" not in tags and position_tag is None:
        tags.append("场内ETF")

    return tags, position_tag


def parse_exposure_tags(holdings: list[dict]) -> Optional[str]:
    """从库内最新持仓反推赛道暴露

    Args:
        holdings: [{stock_name, ratio}] 最新季度持仓（含占比%）

    Returns:
        "光模块/CPO×3 27.6%, 半导体/算力芯片×2 13.8%, 其他 45.1%" 或 None
    """
    bucket: dict[str, float] = defaultdict(float)
    count: dict[str, int] = defaultdict(int)
    total = 0.0
    matched = 0.0
    for h in holdings:
        name = str(h.get("stock_name") or "")
        ratio = h.get("ratio")
        if ratio is None:
            continue
        total += float(ratio)
        hit = None
        for pat, label in _EXPOSURE_RULES:
            if re.search(pat, name):
                hit = label
                break
        if hit:
            bucket[hit] += float(ratio)
            count[hit] += 1
            matched += float(ratio)

    if not total:
        return None
    parts = [
        f"{label}×{count[label]} {bucket[label]:.1f}%"
        for label in sorted(bucket, key=bucket.get, reverse=True)
    ]
    other = total - matched
    if other > 0.5:
        parts.append(f"其他 {other:.1f}%")
    return ", ".join(parts)


class F10FetchError(RuntimeError):
    """F10 网络抓取失败（区别于档案缺失）——调用方应保留旧标签而非降级"""


def fetch_f10_profile(code: str) -> Optional[dict]:
    """抓取 F10 基本概况 → {official_type, benchmark}

    页面缺失（互认基金等非内地公募）返回 None；
    网络异常抛 F10FetchError（调用方保留既有标签，避免瞬时失败
    被持久化为'互认基金'错误分类）。
    """
    url = F10_URL.format(code=code)
    try:
        resp = requests.get(
            url, headers={"Referer": "https://fundf10.eastmoney.com/"}, timeout=15
        )
        resp.encoding = "utf-8"
    except Exception as e:
        raise F10FetchError(f"F10 网络抓取失败 code={code}: {e}") from e

    if resp.status_code != 200 or len(resp.text) < 5000:
        logger.info("F10 档案缺失 code=%s（可能为互认/非内地公募）", code)
        return None

    def field(label: str) -> str:
        m = re.search(rf"<th>{label}</th>\s*<td[^>]*>(.*?)</td>", resp.text, re.S)
        if not m:
            return ""
        return re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip()

    official_type = field("基金类型")
    benchmark = field("业绩比较基准")
    if not official_type and not benchmark:
        return None
    return {"official_type": official_type, "benchmark": benchmark}


def build_double_tags(
    code: str, name: str, fund_type: str,
    holdings: Optional[list[dict]] = None,
    f10: Optional[dict] = None,
) -> dict:
    """一站式生成双层标签

    Returns:
        {tags, fund_type_official, benchmark_text, exposure_tags, is_mutual_fund}
    """
    fetch_failed = False
    if f10 is None:
        try:
            f10 = fetch_f10_profile(code)
        except F10FetchError as e:
            logger.warning(f"{e}；保留既有标签，仅用名称解析")
            fetch_failed = True
    is_mutual = False
    official_type = benchmark = None

    if f10:
        official_type = f10.get("official_type") or None
        # 互认基金档案页存在但值为 "---"，视为无档案
        if official_type in ("", "---"):
            official_type = None
        benchmark = f10.get("benchmark") or None
    elif not fetch_failed and official_type is None and code.startswith("968"):
        # 档案页存在但类型为空（如 968 开头香港互认基金，值为 ---）
        is_mutual = True
        official_type = "互认基金"
    else:
        # F10 无档案（互认基金/非内地公募）→ 名称解析兜底
        is_mutual = not code.startswith(("0", "1", "5")) or code.startswith("968")
        official_type = "互认基金" if is_mutual else None

    tags, position_tag = parse_primary_tags(
        official_type, benchmark, name, fund_type=fund_type
    )
    if is_mutual and "互认基金" not in tags:
        tags.insert(0, "互认基金")

    exposure = parse_exposure_tags(holdings) if holdings else None
    return {
        "tags": ",".join(tags) if tags else None,
        "fund_type_official": official_type,
        "benchmark_text": benchmark,
        "exposure_tags": exposure,
        "position_tag": position_tag,
        "is_mutual_fund": is_mutual,
        "_fetch_failed": fetch_failed,  # 调用方据此保留旧 official/benchmark
    }
