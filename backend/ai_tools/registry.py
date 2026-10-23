"""AI Agent 工具注册表

统一 ToolDef 抽象：name / description / JSON Schema / async handler(db, **kwargs)。
铁律：全部只读（不注册任何写库、触发分析、执行交易的工具）；
返回值经 execute_tool 统一信封 + 行数裁剪，防大结果撑爆上下文。
AISkill-as-tool 与未来外部 MCP 工具源（ToolSource 抽象）在 P4 接入本注册表。
"""

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

MAX_TOOL_ROWS = 500          # 单次工具返回行数上限（超出截断并标记）
MAX_TOOL_JSON_CHARS = 12000  # 序列化字符上限，双保险


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # OpenAI function parameters JSON Schema
    handler: Callable[..., Awaitable[Any]]
    category: str = "data"
    hidden: bool = False  # 隐藏工具不进 specs（保留可执行，供内部任务用）


_REGISTRY: dict[str, ToolDef] = {}


def tool(name: str, description: str, parameters: dict, category: str = "data", handler: Optional[Callable] = None):
    """装饰器注册；也可 tool(..., handler=fn) 函数式注册（用于动态生成的技能工具）"""
    def deco(fn: Callable[..., Awaitable[Any]]):
        if not inspect.iscoroutinefunction(fn):
            raise ValueError(f"工具 {name} handler 必须是 async 函数")
        _REGISTRY[name] = ToolDef(name=name, description=description, parameters=parameters,
                                  handler=fn, category=category)
        return fn
    if handler is not None:
        _REGISTRY[name] = ToolDef(name=name, description=description, parameters=parameters,
                                  handler=handler, category=category)
        return lambda f: f
    return deco


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def get_tool(name: str) -> Optional[ToolDef]:
    return _REGISTRY.get(name)



# ══════════════════════════════════════════════════════════════════
# 工具分组（2026-10-23）：按问题类型只暴露相关工具，省 prompt
# ══════════════════════════════════════════════════════════════════
# 为什么需要：工具定义每轮都要重发。24 个工具 7363 字符，3 轮对话累计
# 约 3.5 万字符；分组后只剩 8~10 个约 2800 字符，累计降到约 2.1 万（-39%）。
#
# 为什么必须带兜底：实测只给 3 个工具时模型把 3 个全调了（比不分组还费），
# 而给全量时只调 4 个且都切题。所以**选错组的代价高于省下的 token**——
# 置信度不足时必须回退全量，宁可多花 token 也不能让模型找不到工具。

TOOL_GROUPS: dict[str, list[str]] = {
    # 交易决策：能不能买、买了亏不亏、成本多少
    "trade": [
        "get_trade_cost", "get_otc_trade_status", "get_fund_fee_profile",
        "get_cross_border_etf_premium", "get_fund_nav_history", "get_latest_signals",
    ],
    # 组合诊断：持仓结构、分散度、集中度
    "portfolio": [
        "get_positions", "get_portfolio_correlation", "get_portfolio_concentration",
        "get_holding_overlap", "get_review_report", "get_latest_signals",
        "get_fund_nav_history", "list_fund_pool",
    ],
    # 信号/机会：今天买什么、评分怎么看
    "signal": [
        "get_latest_signals", "get_signal_history", "get_analysis_stats",
        "get_factor_snapshot", "get_factor_history", "list_factors",
        "get_scoring_config", "get_market_snapshot", "list_fund_pool",
        "get_backtest_summary",
    ],
    # 单只基金：这只怎么样
    "fund": [
        "get_fund_nav_history", "get_fund_rating", "get_fund_fee_profile",
        "get_fund_exposure", "get_signal_history", "get_factor_history",
        "get_otc_trade_status", "get_trade_cost", "list_fund_pool",
    ],
    # 市场环境
    "market": [
        "get_market_snapshot", "get_us_market_context",
        "get_cross_border_etf_premium", "get_latest_signals", "get_error_stats",
    ],
}

# 关键词 → 组。命中数 >= MIN_HITS 才算有把握；否则回退全量。
_GROUP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "trade": (
        "买", "卖", "申购", "赎回", "费率", "手续费", "成本", "亏", "赚",
        "持有", "溢价", "折价", "能买", "该买", "加仓", "减仓", "清仓",
    ),
    "portfolio": (
        "持仓", "组合", "分散", "集中", "相关性", "同涨同跌", "配置",
        "诊断", "我的仓", "再平衡", "调仓",
    ),
    "signal": (
        "信号", "评分", "机会", "推荐", "榜", "排名", "因子", "回测", "策略",
    ),
    "fund": (
        "这只", "那这只", "净值", "表现", "回撤", "评级", "经理", "详情",
        "怎么样", "如何",
    ),
    "market": (
        "大盘", "市场", "情绪", "资金", "北向", "板块", "环境", "美股",
        "指数", "行情",
    ),
}

# 独特关键词：出现即足以定组（这些词不会跨组出现）
_UNIQUE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "trade": ("申购", "赎回", "溢价", "折价", "费率", "手续费", "盈亏", "回本"),
    "portfolio": ("组合", "分散度", "集中度", "相关性", "同涨同跌", "持仓"),
    "market": ("大盘", "市场情绪", "北向", "美股", "板块", "指数估值"),
}

MIN_HITS = 2  # 普通关键词的成组门槛


def pick_group(prompt: str) -> Optional[str]:
    """按问题文本猜工具分组；置信不足返回 None（调用方回退全量）

    两档判定：
    1. 命中**独特关键词**（申购/溢价/组合/大盘…）→ 直接定型，这类词不跨组，
       误判风险极低；
    2. 靠普通关键词打分：需 >= MIN_HITS 且**不与第二名打平**。

    任何不确定的情况都返回 None 走全量——选错组的代价（模型找不到工具、
    多绕一轮）高于省下的 token。
    """
    text = (prompt or "").strip()
    if not text:
        return None

    # 第一档：独特词
    unique_hits = {
        g: sum(1 for k in kws if k in text) for g, kws in _UNIQUE_KEYWORDS.items()
    }
    top_unique = [g for g, v in unique_hits.items() if v > 0]
    if len(top_unique) == 1:
        return top_unique[0]

    # 第二档：普通打分
    scores: dict[str, int] = {}
    for group, kws in _GROUP_KEYWORDS.items():
        scores[group] = sum(1 for k in kws if k in text)
    best = max(scores, key=lambda g: scores[g])
    if scores[best] < MIN_HITS:
        return None
    # 与第二名打平也算不明确（如「买入机会」同时命中 trade 与 signal）
    tied = [g for g, v in scores.items() if v == scores[best]]
    if len(tied) > 1:
        return None
    return best


def group_tools(group: Optional[str]) -> Optional[list[str]]:
    """分组名 → 工具名列表；未知分组或 None 返回 None（= 全量）"""
    if not group:
        return None
    names = TOOL_GROUPS.get(group)
    if not names:
        return None
    # 只保留真实注册过的（删工具时分组表不会失效）
    return [n for n in names if n in _REGISTRY]


def to_openai_spec(d: ToolDef) -> dict:
    return {
        "type": "function",
        "function": {
            "name": d.name,
            "description": d.description,
            "parameters": d.parameters,
        },
    }


def tool_specs(
    allowed: Optional[list[str]] = None,
    extra: Optional[list[ToolDef]] = None,
) -> list[dict]:
    """导出 OpenAI tools 数组；allowed=None 表示全部非隐藏工具。

    extra：本次运行专属的动态工具（如 Skill-as-tool），不进全局注册表，
    与注册表同名时以 extra 为准。
    """
    defs = [
        d for d in _REGISTRY.values()
        if not d.hidden and (allowed is None or d.name in allowed)
    ]
    for d in extra or []:
        defs = [x for x in defs if x.name != d.name] + [d]
    return [to_openai_spec(d) for d in defs]



def compact_rows(items: list[dict], keep: Optional[list[str]] = None,
                 drop_empty: bool = True) -> dict:
    """把「字典列表」压成列式表 {cols, rows}（2026-10-22）

    为什么需要：工具结果原样 JSON 化后，每条记录的**键名都要重复一遍**。
    实测 list_fund_pool 的 57 条记录 10432 字符里，有一半是重复键名——
    而多轮 Agent 对话每轮都会重发历史，这些键名被反复计费。

    实测收益（同一份数据，AI 理解结果完全一致）：
      原始 dict 列表 10432 字符 → 列式 3281 字符（-69%）
    对照实验：压成「纯文本行」只剩 2425 字符，但模型解析失败（返回空），
    所以保留 JSON 结构、只去掉重复键名——这是可靠性与体积的平衡点。

    Args:
        items: 原始记录列表
        keep: 只保留这些字段（None=全部）；固定低信息字段建议在调用方剔除
        drop_empty: 丢掉全空列，避免 cols 里挂一堆无用的键

    Returns:
        {"cols": [...], "rows": [[...], ...]}
    """
    if not items:
        return {"cols": [], "rows": []}
    cols = list(keep) if keep else list(dict.fromkeys(k for it in items for k in it))
    if drop_empty:
        cols = [c for c in cols if any(it.get(c) not in (None, "", [], {}) for it in items)]
    return {"cols": cols, "rows": [[it.get(c) for c in cols] for it in items]}


def _truncate_rows(data: Any) -> tuple[Any, bool]:
    """list 结果行数裁剪；dict 中若有 rows/items 列表同样裁剪"""
    truncated = False
    if isinstance(data, list) and len(data) > MAX_TOOL_ROWS:
        data, truncated = data[:MAX_TOOL_ROWS], True
    elif isinstance(data, dict):
        for key in ("rows", "items", "data"):
            v = data.get(key)
            if isinstance(v, list) and len(v) > MAX_TOOL_ROWS:
                data = dict(data)
                data[key] = v[:MAX_TOOL_ROWS]
                truncated = True
                break
    return data, truncated


async def execute_tool(
    db, name: str, arguments: dict,
    overrides: Optional[dict[str, ToolDef]] = None,
) -> str:
    """执行工具并以 JSON 字符串回填给模型（role=tool 内容）。

    overrides：本次运行专属工具（如 Skill-as-tool），优先于全局注册表。
    永不抛异常到调用方：错误也序列化进信封，让模型自行决定绕行。
    """
    d = (overrides or {}).get(name) or _REGISTRY.get(name)
    if d is None:
        return json.dumps({"ok": False, "error": f"未知工具: {name}"}, ensure_ascii=False)
    try:
        result = await d.handler(db, **arguments)
        result, truncated = _truncate_rows(result)
        payload = json.dumps(result, ensure_ascii=False, default=str)
        if len(payload) > MAX_TOOL_JSON_CHARS:
            # 粗略兜底：压缩为截断说明 + 前段文本，提示模型缩小查询范围
            return json.dumps({
                "ok": True, "truncated": True,
                "note": f"结果超过 {MAX_TOOL_JSON_CHARS} 字符被截断，请缩小 days/codes 范围重试",
                "preview": payload[:MAX_TOOL_JSON_CHARS],
            }, ensure_ascii=False)
        return json.dumps({"ok": True, "truncated": truncated, "data": json.loads(payload)}, ensure_ascii=False)
    except TypeError as e:
        return json.dumps({"ok": False, "error": f"参数错误: {e}", "expected_schema": d.parameters}, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"工具执行失败 {name}: {type(e).__name__}: {e}")
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}, ensure_ascii=False)
