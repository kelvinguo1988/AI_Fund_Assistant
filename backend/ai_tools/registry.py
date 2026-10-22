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
