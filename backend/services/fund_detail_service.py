"""基金详情服务 — 阶段涨幅 + 扩展数据 + 数据批量获取

从基金详情页数据文件 pingzhongdata/{code}.js 中提取时段收益率、
累计收益走势、规模变动、持有人结构、资产配置等数据。
持仓和经理详情需通过 AKShare（fund_holding_service / fund_manager_service）获取。

并发控制：
- pingzhongdata JS 下载是轻量级 HTTP GET（几 KB 文件），使用专用 Semaphore(8)
- 与大重量 akshare 接口（fund_portfolio_hold_em 等）共享的全局 Semaphore(5) 分离
- 单只基金 JS 获取超时 30s，内部 requests 超时 25s（先于 asyncio 触发）
"""

import asyncio
import json
import logging
import random
import re
from typing import Any, Optional

import requests

from backend.utils.concurrency import run_with_timeout

logger = logging.getLogger(__name__)

# 天天基金详情数据 JS 文件 URL 模板
_PINGZHONG_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"

# 单只基金 JS 获取超时（秒）— asyncio.wait_for 层
_JS_TIMEOUT: float = 30.0

# requests 层超时 — 必须小于 _JS_TIMEOUT，确保超时时获得干净的 requests.Timeout
# 而非 asyncio.TimeoutError（asyncio 超时无法区分是网络慢还是线程池耗尽）
_JS_REQUESTS_TIMEOUT: float = 25.0

# pingzhongdata 专用并发信号量（8 并发）— JS 文件下载极轻量，不应与大重量
# akshare 接口共享全局 Semaphore(5)，否则 40-60 只基金 × 全局 5 并发 =
# 大量排队等 slot，导致 _fetch_js 超时堆积。
_PINGZHONG_SEM = asyncio.Semaphore(8)

# _fetch_js 失败重试配置
_MAX_RETRIES = 1  # 超时/网络错误重试 1 次
_RETRY_DELAY_BASE = 1.5  # 重试基础等待秒数（加 jitter 避免惊群）

# 正则提取 syl_ 变量值
_RETURN_PATTERNS: dict[str, re.Pattern] = {
    "return_1m": re.compile(r'var syl_1y="([^"]*)"'),
    "return_3m": re.compile(r'var syl_3y="([^"]*)"'),
    "return_6m": re.compile(r'var syl_6y="([^"]*)"'),
    "return_1y": re.compile(r'var syl_1n="([^"]*)"'),
}

# 基金名称
_FUND_NAME_PATTERN = re.compile(r'var fS_name\s*=\s*"([^"]*)"')

# 扩展数据正则模式
_PAT_GRAND_TOTAL = re.compile(r'var\s+Data_grandTotal\s*=\s*(\[)')
_PAT_FLUCTUATION_SCALE = re.compile(r'var\s+Data_fluctuationScale\s*=\s*(\{)')
_PAT_HOLDER_STRUCTURE = re.compile(r'var\s+Data_holderStructure\s*=\s*(\{)')
_PAT_ASSET_ALLOCATION = re.compile(r'var\s+Data_assetAllocation\s*=\s*(\{)')


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


def _extract_js_object(text: str, start_pattern: re.Pattern) -> Optional[dict]:
    """从 JS 文本中提取匹配第一个 '{...}' 的 JSON 对象（支持嵌套）"""
    m = start_pattern.search(text)
    if not m:
        return None
    start = m.start(1)
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _parse_grand_total(js_text: str) -> Optional[list]:
    """解析累计收益走势 Data_grandTotal"""
    return _extract_js_array(js_text, _PAT_GRAND_TOTAL)


def _parse_fluctuation_scale(js_text: str) -> Optional[dict]:
    """解析规模变动 Data_fluctuationScale"""
    return _extract_js_object(js_text, _PAT_FLUCTUATION_SCALE)


def _parse_holder_structure(js_text: str) -> Optional[dict]:
    """解析持有人结构 Data_holderStructure"""
    return _extract_js_object(js_text, _PAT_HOLDER_STRUCTURE)


def _parse_asset_allocation(js_text: str) -> Optional[dict]:
    """解析资产配置 Data_assetAllocation"""
    return _extract_js_object(js_text, _PAT_ASSET_ALLOCATION)


def _parse_extended_data(js_text: str) -> dict:
    """从 JS 文本中解析全部扩展数据（4类）"""
    return {
        "grand_total": _parse_grand_total(js_text),
        "fluctuation_scale": _parse_fluctuation_scale(js_text),
        "holder_structure": _parse_holder_structure(js_text),
        "asset_allocation": _parse_asset_allocation(js_text),
    }


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
    """同步获取单只基金的 pingzhongdata JS 文本（由 run_with_timeout 在线程池调用）

    关键设计：
    - requests 超时 25s < asyncio 超时 30s，确保网络层面先超时释放线程
    - 东财补丁会注入 NID + UA 轮换 + 1-4s jitter sleep
    - 超时 / 连接错误时自动重试 1 次（带 jitter 延迟避免惊群）
    """
    import time as _time

    url = _PINGZHONG_URL.format(code=code)
    last_error = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                headers={"Referer": "https://fund.eastmoney.com"},
                timeout=_JS_REQUESTS_TIMEOUT,
            )
            resp.encoding = "utf-8"
            if resp.status_code != 200:
                logger.debug("获取 %s 数据失败, status=%d", code, resp.status_code)
                return None
            return resp.text
        except requests.Timeout as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                jitter = _RETRY_DELAY_BASE + random.uniform(0, 2)
                logger.debug("获取 %s 超时(attempt %d/%d)，%0.1fs 后重试", code, attempt + 1, _MAX_RETRIES + 1, jitter)
                _time.sleep(jitter)
        except requests.ConnectionError as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                jitter = _RETRY_DELAY_BASE + random.uniform(0, 2)
                logger.debug("获取 %s 连接错误(attempt %d/%d)，%0.1fs 后重试", code, attempt + 1, _MAX_RETRIES + 1, jitter)
                _time.sleep(jitter)
        except Exception as e:
            logger.debug("获取基金 %s 数据异常: %s", code, e)
            return None

    logger.debug("获取 %s 重试 %d 次仍失败: %s", code, _MAX_RETRIES, last_error)
    return None


async def fetch_fund_detail(code: str) -> dict[str, Any]:
    """一站式获取单只基金基础数据"""
    js_text = await run_with_timeout(
        _fetch_js, code, timeout=_JS_TIMEOUT, semaphore=_PINGZHONG_SEM,
    )
    if not js_text:
        return {"period_returns": {}, "fund_name": "", "extended_data": {}}

    return {
        "period_returns": _parse_period_returns(js_text),
        "fund_name": _parse_fund_name(js_text),
        "extended_data": _parse_extended_data(js_text),
    }


async def fetch_all_js_texts(codes: list[str]) -> dict[str, str]:
    """并发批量获取多只基金的 pingzhongdata JS 文本

    使用专用信号量（8 并发）—— pingzhongdata 是轻量级 JS 文件下载，
    与大重量 akshare 接口分离，避免共享全局 Semaphore(5) 导致排队超时。
    """
    if not codes:
        return {}

    async def fetch_one(code: str) -> tuple[str, Optional[str]]:
        try:
            text = await run_with_timeout(
                _fetch_js, code, timeout=_JS_TIMEOUT, semaphore=_PINGZHONG_SEM,
            )
            return code, text
        except Exception as e:
            logger.debug("批量抓取基金 %s 数据异常: %s", code, e)
            return code, None

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
