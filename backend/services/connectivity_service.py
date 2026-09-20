"""数据源连通性测试服务"""

import time
import ipaddress
import logging
from typing import Optional
from urllib.parse import urlparse

import httpx

from backend.schemas.system_config import ConnectivityItem, ConnectivityResult

logger = logging.getLogger(__name__)

# 必要数据源域名列表
TARGETS: list[dict[str, str]] = [
    {"name": "fund.eastmoney.com", "url": "https://fund.eastmoney.com"},
    {"name": "push2.eastmoney.com", "url": "https://push2.eastmoney.com"},
    {"name": "anonflow2.eastmoney.com", "url": "https://anonflow2.eastmoney.com"},
    {"name": "push2his.eastmoney.com", "url": "https://push2his.eastmoney.com"},
    {"name": "datacenter-web.eastmoney.com", "url": "https://datacenter-web.eastmoney.com"},
]

_PRIVATE_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

# 常被用作 SSRF 探测目标：未指定地址 / 链路本地 / 保留段（含 169.254.169.254 元数据）
_blocked_addr_types = ("is_unspecified", "is_link_local", "is_reserved", "is_multicast", "is_loopback")


def _is_blocked_address(addr: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    if any(addr in net for net in _PRIVATE_NETS):
        return True
    return any(getattr(addr, t, False) for t in _blocked_addr_types)


def _validate_public_url(url: str) -> None:
    """校验 URL 为公网 HTTPS 地址，防止 SSRF

    除字面 IP 外还解析域名：否则 attacker.com → 127.0.0.1 / 169.254.169.254
    这类 DNS 指向内网的域名可绕过纯字符串检查。
    """
    import socket

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"仅支持 HTTPS 地址，收到: {parsed.scheme}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"无法解析主机名: {url}")
    try:
        candidates = [hostname]
        try:
            addr = ipaddress.ip_address(hostname)
        except ValueError:
            # 域名 → 解析全部 A/AAAA 记录逐一校验
            infos = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
            candidates = sorted({i[4][0] for i in infos})
        for cand in candidates:
            try:
                ip = ipaddress.ip_address(cand)
            except ValueError:
                continue
            if _is_blocked_address(ip):
                raise ValueError(f"不允许访问内网/保留地址: {cand} (host={hostname})")
    except socket.gaierror:
        raise ValueError(f"域名无法解析: {hostname}")


CONNECT_TIMEOUT = 5.0
MIN_TEST_INTERVAL = 30.0  # 最短测试间隔(秒)，防止频繁调用

_last_test_at: float = 0.0


async def _test_single(client: httpx.AsyncClient, name: str, url: str) -> ConnectivityItem:
    """测试单个目标的连通性"""
    start = time.monotonic()
    try:
        resp = await client.head(url, follow_redirects=True, timeout=CONNECT_TIMEOUT)
        latency = (time.monotonic() - start) * 1000
        if 200 <= resp.status_code < 400:
            return ConnectivityItem(name=name, reachable=True, latency_ms=round(latency, 1))
        else:
            return ConnectivityItem(
                name=name, reachable=True, latency_ms=round(latency, 1),
                error=f"HTTP {resp.status_code}"
            )
    except httpx.TimeoutException:
        latency = (time.monotonic() - start) * 1000
        return ConnectivityItem(name=name, reachable=False, latency_ms=round(latency, 1), error="timeout")
    except httpx.ConnectError:
        latency = (time.monotonic() - start) * 1000
        return ConnectivityItem(name=name, reachable=False, latency_ms=round(latency, 1), error="network unreachable")
    except Exception:
        logger.exception("Unexpected error testing %s", name)
        latency = (time.monotonic() - start) * 1000
        return ConnectivityItem(name=name, reachable=False, latency_ms=round(latency, 1), error="unexpected error")


async def test_all_connectivity(
    ai_base_url: Optional[str] = None,
    ai_enabled: bool = False,
) -> ConnectivityResult:
    """测试所有必要数据源的连通性"""
    global _last_test_at
    now = time.monotonic()
    if now - _last_test_at < MIN_TEST_INTERVAL:
        raise ValueError(
            f"测试间隔不能小于 {MIN_TEST_INTERVAL:.0f} 秒，请 {MIN_TEST_INTERVAL - (now - _last_test_at):.0f} 秒后再试"
        )
    _last_test_at = now

    targets = list(TARGETS)

    if ai_enabled and ai_base_url:
        _validate_public_url(ai_base_url)
        targets.append({"name": "AI API", "url": ai_base_url.rstrip("/")})

    results: list[ConnectivityItem] = []
    async with httpx.AsyncClient(timeout=CONNECT_TIMEOUT) as client:
        for t in targets:
            item = await _test_single(client, t["name"], t["url"])
            results.append(item)

    reachable = sum(1 for r in results if r.reachable)
    total = len(results)
    unreachable = total - reachable

    if unreachable == 0:
        status = "ok"
    elif reachable == 0:
        status = "fail"
    else:
        status = "partial"

    return ConnectivityResult(
        status=status,
        results=results,
        summary={"total": total, "reachable": reachable, "unreachable": unreachable},
    )
