"""东方财富反爬虫补丁 — NID 授权 + User-Agent 轮换

通过拦截 requests.Session.request，对东方财富域名的请求注入：
1. 随机 User-Agent（从预置池选取）
2. NID 授权令牌（从 anonflow2 接口获取，20s 缓存）
3. 随机休眠（1~4s）降低请求频率
"""

import hashlib
import json
import logging
import random
import secrets
import threading
import time
import uuid
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 预置 User-Agent 池（无需 fake_useragent 依赖）
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
]

_TARGET_DOMAINS = [
    "fund.eastmoney.com",
    "push2.eastmoney.com",
    "push2his.eastmoney.com",
    "datacenter-web.eastmoney.com",
    "datacenter.eastmoney.com",
]

original_request = requests.Session.request


class AuthCache:
    def __init__(self):
        self.data: Optional[str] = None
        self.expire_at: float = 0
        self.lock = threading.Lock()
        self.ttl = 20


_cache = AuthCache()
_patched = False


def _generate_uuid_md5() -> str:
    return hashlib.md5(str(uuid.uuid4()).encode("utf-8")).hexdigest()


def _generate_st_nvi() -> str:
    charset = "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict"
    random_str = "".join(secrets.choice(charset) for _ in range(21))
    hash_prefix = hashlib.sha256(random_str.encode("utf-8")).hexdigest()[:4]
    return random_str + hash_prefix


def _get_nid(user_agent: str) -> Optional[str]:
    now = time.time()
    if _cache.data and now < _cache.expire_at:
        return _cache.data

    with _cache.lock:
        try:
            url = "https://anonflow2.eastmoney.com/backend/api/webreport"
            screen = random.choice(["1920X1080", "2560X1440", "3840X2160"])
            payload = json.dumps({
                "osPlatform": "Windows",
                "sourceType": "WEB",
                "osversion": "Windows 10.0",
                "language": "zh-CN",
                "timezone": "Asia/Shanghai",
                "webDeviceInfo": {
                    "screenResolution": screen,
                    "userAgent": user_agent,
                    "canvasKey": _generate_uuid_md5(),
                    "webglKey": _generate_uuid_md5(),
                    "fontKey": _generate_uuid_md5(),
                    "audioKey": _generate_uuid_md5(),
                },
            })
            headers = {
                "Cookie": f"st_nvi={_generate_st_nvi()}",
                "Content-Type": "application/json",
            }
            resp = requests.post(url, headers=headers, data=payload, timeout=30)
            resp.raise_for_status()
            nid = resp.json()["data"]["nid"]
            _cache.data = nid
            _cache.expire_at = now + _cache.ttl
            return nid
        except Exception as e:
            logger.warning(f"东方财富 NID 授权失败: {e}")
            _cache.data = None
            _cache.expire_at = now + 300  # 5 分钟冷却
            return None


def apply_patch():
    """全局应用 EastMoney 反爬虫补丁"""
    global _patched
    if _patched:
        return

    def patched_request(self, method, url, **kwargs):
        is_target = any(d in (url or "") for d in _TARGET_DOMAINS)
        if not is_target:
            return original_request(self, method, url, **kwargs)

        user_agent = random.choice(_USER_AGENTS)
        headers = kwargs.get("headers", {})
        headers["User-Agent"] = user_agent
        nid = _get_nid(user_agent)
        if nid:
            headers["Cookie"] = f"nid18={nid}"
        kwargs["headers"] = headers
        time.sleep(random.uniform(1, 4))
        return original_request(self, method, url, **kwargs)

    requests.Session.request = patched_request
    _patched = True
    logger.info("EastMoney 反爬虫补丁已应用")
