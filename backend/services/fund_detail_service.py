"""基金详情服务 — 阶段涨幅数据抓取

从基金详情页数据文件 pingzhongdata/{code}.js 中提取时段收益率。
"""

import asyncio
import logging
import re
from typing import Optional

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

# 并发控制：最多同时 5 个请求
_SEMAPHORE = asyncio.Semaphore(5)


def _fetch_single(code: str) -> dict[str, Optional[str]]:
    """同步抓取单只基金的阶段涨幅（由 to_thread 调用）"""
    result: dict[str, Optional[str]] = {k: None for k in _RETURN_PATTERNS}
    url = _PINGZHONG_URL.format(code=code)
    try:
        resp = requests.get(url, headers={"Referer": "http://fund.eastmoney.com"}, timeout=30)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            logger.debug("获取 %s 数据失败, status=%d", code, resp.status_code)
            return result

        for key, pattern in _RETURN_PATTERNS.items():
            m = pattern.search(resp.text)
            if m and m.group(1):
                result[key] = m.group(1)
        return result
    except Exception as e:
        logger.debug("获取基金 %s 阶段涨幅异常: %s", code, e)
        return result


async def fetch_period_returns(codes: list[str]) -> dict[str, dict[str, Optional[str]]]:
    """并发获取多只基金的阶段涨幅数据

    Args:
        codes: 基金代码列表

    Returns:
        {"基金代码": {"return_1m": "21.23", "return_3m": "43.99", ...}}
    """
    if not codes:
        return {}

    async def fetch_one(code: str) -> tuple[str, dict[str, Optional[str]]]:
        async with _SEMAPHORE:
            result = await asyncio.to_thread(_fetch_single, code)
            return code, result

    tasks = [fetch_one(code) for code in codes]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output: dict[str, dict[str, Optional[str]]] = {}
    for r in results:
        if isinstance(r, tuple):
            code, data = r
            output[code] = data
        elif isinstance(r, Exception):
            logger.warning("并发抓取基金数据异常: %s", r)
    return output
