"""乐咕取数的 CSRF 复用与限流加固（2026-11-26）

## 线上故障
温度计 `data: null`，错误日志 `KeyError: 'data'` 与
`AttributeError: 'NoneType' object has no attribute 'attrs'`。

## 根因（容器内实测）
akshare 的 `stock_index_pe_lg` / `stock_index_pb_lg` **各自**内部调用
`get_cookie_csrf()`，即每个接口都去抓一次乐咕首页拿 CSRF：
- 取一次温度计 = 2 次首页请求；页面+定时任务并发时瞬间 4~6 次
- 撞上限流后首页返回 429/504 → `soup.find` 得 None → `.attrs` 崩（AttributeError）
- 或 API 返回无 data 的错误体（KeyError 'data'）

实测数据：
- 连续调用失败率 50%，并发 4 路必现
- 但首页间隔 8 秒即可连续成功 → **限流是秒级窗口，不是封禁**
- 原始 API 本身一直正常（PE 39739 字节 / PB 18496 字节，data 都在）

## 修复
1. 自己抓一次 CSRF，PE 与 PB **共用**（首页请求 2 → 1）
2. CSRF 进程内缓存 10 分钟（正常路径下首页请求 → 0）
3. 不调 akshare 的 `get_cookie_csrf`——它崩溃时抛的 AttributeError
   对上层毫无信息量；自己解析能给出「429 / 未返回 _csrf」这类可辨识错误
4. API 级节流 1.2s + 429 退避重试（乐咕对 API 也限流，实测连打 3 次即 429）
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.services.market_temperature_service as m  # noqa: E402


def _src() -> str:
    return Path(m.__file__).read_text(encoding="utf-8")


class TestCsrfCache:
    def test_cache_ttl_defined(self):
        assert m._CSRF_TTL >= 60, "缓存太短会退化成每次都打首页"
        assert m._CSRF_TTL <= 3600, "缓存太长会因 cookie 过期而失败"

    def test_cache_structure(self):
        assert "ts" in m._CSRF_CACHE and "payload" in m._CSRF_CACHE

    def test_cached_payload_returned_without_request(self, monkeypatch):
        """命中缓存时不应发起任何网络请求"""
        sentinel = {"cookies": {"x": 1}, "headers": {"y": 2}}
        monkeypatch.setitem(m._CSRF_CACHE, "payload", sentinel)
        monkeypatch.setitem(m._CSRF_CACHE, "ts", __import__("time").time())

        # 任何 requests 调用都视为失败
        import requests
        def _boom(*a, **k):
            raise AssertionError("命中缓存时不应发请求")
        monkeypatch.setattr(requests.Session, "get", _boom)

        assert m._legulegu_csrf() is sentinel


class TestNoAkshareCsrf:
    def test_does_not_use_akshare_get_cookie_csrf(self):
        """必须自己抓：akshare 的实现在首页限流时抛 AttributeError，无信息量"""
        src = _src()
        assert "from akshare.stock_feature.stock_a_indicator import get_cookie_csrf" not in src

    def test_parses_csrf_from_meta(self):
        """自己解析 _csrf meta 标签"""
        src = _src()
        assert "_csrf" in src
        assert "X-CSRF-Token" in src

    def test_429_is_identified(self):
        """429 要能识别成限流（而不是笼统的网络错误）"""
        src = _src()
        assert "429" in src
        assert "限流" in src


class TestApiThrottle:
    def test_min_interval_defined(self):
        assert 0 < m._API_MIN_INTERVAL <= 3, "太短无效，太长拖慢正常路径"

    def test_throttle_applied_before_request(self):
        src = _src()
        assert "_API_MIN_INTERVAL" in src
        assert "_API_LAST_CALL" in src

    def test_429_retry_in_api_get(self):
        src = _src()
        # _get 内部要有 429 分支与退避
        assert "Too Many Requests" in src


class TestSharedFetch:
    def test_pe_pb_share_one_csrf(self):
        """PE 与 PB 必须共用一次 CSRF（首页请求 2 → 1）"""
        src = _src()
        fn = src.split("def _fetch_legulegu_pair")[1].split("\ndef ")[0]
        assert fn.count("_legulegu_csrf()") == 1, "只应取一次 CSRF"

    def test_pair_function_used_by_service(self):
        """服务必须走新的 pair 取数，而不是 akshare 的两次独立调用"""
        src = _src()
        assert "_fetch_legulegu_pair(TEMPERATURE_INDEX)" in src
        # 不应再有直接调用 akshare 两个接口
        assert "ak.stock_index_pe_lg(" not in src
        assert "ak.stock_index_pb_lg(" not in src

    def test_symbol_map_covers_index(self):
        """中证800 的 indexCode 必须正确（000906.SH）"""
        src = _src()
        assert '"中证800": "000906.SH"' in src
