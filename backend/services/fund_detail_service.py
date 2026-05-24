"""基金详情服务 — 阶段涨幅 + 数据批量获取

从基金详情页数据文件 pingzhongdata/{code}.js 中提取时段收益率等数据。
持仓和经理详情需通过 AKShare（fund_holding_service / fund_manager_service）获取。
"""

import asyncio
import json
import logging
import re
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# 天天基金详情数据 JS 文件 URL 模板
_PINGZHONG_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"

# 正则提取 syl_ 变量值
_RETURN_PATTERNS: dict[str, re.Pattern] = {
    "return_1m": re.compile(r'var syl_1y="([^"]*)"'),
    "return_3m": re.compile(r'var syl_3y="([^"]*)"'),
    "return_6m": re.compile(r'var syl_6y="([^"]*)"'),
    "return_1y": re.compile(r'var syl_1n="([^"]*)"'),
}

# 基金名称
_FUND_NAME_PATTERN = re.compile(r'var fS_name\s*=\s*"([^"]*)"')

# 并发控制：最多同时 5 个请求
_SEMAPHORE = asyncio.Semaphore(5)


def _extract_js_array(text: str, start_pattern: re.Pattern) -> Optional[list]:
    """从 JS 文本中提取匹配第一个 '[...]' 的 JSON 数组（支持嵌套）"""
    m = start_pattern.search(text)
    if not m:
        return None
    start = m.start(1)
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _parse_period_returns(js_text: str) -> dict[str, Optional[str]]:
    """从 JS 文本中提取阶段涨幅"""
    result: dict[str, Optional[str]] = {}
    for key, pattern in _RETURN_PATTERNS.items():
        m = pattern.search(js_text)
        result[key] = m.group(1) if m and m.group(1) else None
    return result


def _parse_fund_name(js_text: str) -> str:
    """从 JS 文本中提取基金名称"""
    m = _FUND_NAME_PATTERN.search(js_text)
    return m.group(1) if m else ""


def _fetch_js(code: str) -> Optional[str]:
    """同步获取单只基金的 pingzhongdata JS 文本（由 to_thread 调用）"""
    url = _PINGZHONG_URL.format(code=code)
    try:
        resp = requests.get(url, headers={"Referer": "https://fund.eastmoney.com"}, timeout=30)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            logger.debug("获取 %s 数据失败, status=%d", code, resp.status_code)
            return None
        return resp.text
    except Exception as e:
        logger.debug("获取基金 %s 数据异常: %s", code, e)
        return None


async def fetch_fund_detail(code: str) -> dict[str, Any]:
    """一站式获取单只基金基础数据

    Returns:
        {
            "period_returns": {"return_1m": "...", ...},
            "fund_name": "基金名称",
        }
    """
    js_text = await asyncio.to_thread(_fetch_js, code)
    if not js_text:
        return {"period_returns": {}, "fund_name": ""}

    return {
        "period_returns": _parse_period_returns(js_text),
        "fund_name": _parse_fund_name(js_text),
    }


async def fetch_all_js_texts(codes: list[str]) -> dict[str, str]:
    """并发批量获取多只基金的 pingzhongdata JS 文本

    Args:
        codes: 基金代码列表

    Returns:
        {"基金代码": "js文本内容", ...}
    """
    if not codes:
        return {}

    async def fetch_one(code: str) -> tuple[str, Optional[str]]:
        async with _SEMAPHORE:
            text = await asyncio.to_thread(_fetch_js, code)
            return code, text

    tasks = [fetch_one(code) for code in codes]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output: dict[str, str] = {}
    for r in results:
        if isinstance(r, tuple):
            code, text = r
            if text:
                output[code] = text
        elif isinstance(r, Exception):
            logger.warning("批量抓取基金数据异常: %s", r)
    return output


# ─── 保持向后兼容 ────────────────────────────────────────────────────


def _fetch_single(code: str) -> dict[str, Optional[str]]:
    """兼容旧版：只提取阶段涨幅"""
    js_text = _fetch_js(code)
    if js_text:
        return _parse_period_returns(js_text)
    return {k: None for k in _RETURN_PATTERNS}


async def fetch_period_returns(codes: list[str]) -> dict[str, dict[str, Optional[str]]]:
    """兼容旧版：并发获取多只基金的阶段涨幅数据"""
    texts = await fetch_all_js_texts(codes)
    output: dict[str, dict[str, Optional[str]]] = {}
    for code, js_text in texts.items():
        output[code] = _parse_period_returns(js_text)
    return output
