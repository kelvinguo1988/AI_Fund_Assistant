"""错误日志模块回归测试 — 节流去重/分类/查询/导出/容量裁剪"""

import sys, os
import asyncio
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services import error_log_service as mod
from backend.services.error_log_service import (
    ErrorLogStore, classify_source_error,
)


@pytest.fixture
def store(monkeypatch, tmp_path):
    """独立临时库的 ErrorLogStore（重置单例与节流表）"""
    dbfile = tmp_path / "err_test.db"
    monkeypatch.setattr(mod, "DB_PATH", dbfile)
    monkeypatch.setattr(mod, "MAX_ROWS", 50)
    monkeypatch.setattr(mod, "THROTTLE_WINDOW", 60.0)
    # 重置单例（指向新文件）
    with mod.ErrorLogStore._lock:
        mod.ErrorLogStore._instance = None
    store = mod.ErrorLogStore()
    yield store
    with mod.ErrorLogStore._lock:
        mod.ErrorLogStore._instance = None


class TestErrorLogStore:
    def test_write_and_query(self, store):
        store.log("akshare.get_fund_data", "请求失败", category="rate_limit", detail="RemoteDisconnected")
        store.log("tags.F10", "F10 超时", category="timeout", severity="warning")
        rows = store.query()
        assert len(rows) == 2
        assert rows[0]["category"] == "timeout"  # 倒序
        rl = store.query(category="rate_limit")
        assert len(rl) == 1 and rl[0]["module"] == "akshare.get_fund_data"

    def test_throttle_same_key(self, store):
        """同 (module,category,message) 60s 内只记 1 条"""
        assert store.log("m", "same message", category="rate_limit") is True
        assert store.log("m", "same message", category="rate_limit") is False
        assert store.query(limit=10) .__len__() == 1
        # 不同消息不受节流影响
        assert store.log("m", "different message", category="rate_limit") is True
        assert len(store.query()) == 2

    def test_count_since(self, store):
        import time
        store.log("m1", "old")
        # 等 1 秒翻转（ts 为秒级精度，同秒内 since 比较会歧义）
        start_sec = time.strftime("%H:%M:%S")
        while time.strftime("%H:%M:%S") == start_sec:
            time.sleep(0.05)
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        cutoff_sec = time.strftime("%H:%M:%S")
        while time.strftime("%H:%M:%S") == cutoff_sec:
            time.sleep(0.05)
        store.log("m2", "new")
        assert store.count() == 2
        assert store.count(since_ts=cutoff) == 1

    def test_capacity_trim(self, store):
        for i in range(60):
            store.log("m", f"msg-{i}", category="other")  # 消息唯一绕过节流
        assert store.count() == 50  # MAX_ROWS=50

    def test_download_text(self, store):
        store.log("akshare.x", "被限流", category="rate_limit", detail="RemoteDisconnected")
        text = store.download_text()
        assert "错误日志导出" in text
        assert "被限流" in text
        filtered = store.download_text(category="timeout")
        assert "被限流" not in filtered

    def test_retention_30_days(self, store, monkeypatch):
        """时间保留期：30 天前的记录在下次写入时清理（用户确认的覆盖需求）"""
        import time
        # 直接造一条 40 天前的旧记录
        old_ts = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 40 * 86400)
        )
        store._conn.execute(
            "INSERT INTO error_logs (ts, module, category, severity, message) "
            "VALUES (?, 'legacy', 'other', 'error', '四十天前的旧日志')",
            (old_ts,),
        )
        store._conn.commit()
        assert store.count() == 1
        # 新写入触发清理
        store.log("m", "新日志")
        assert store.count() == 1
        assert store.query()[0]["message"] == "新日志"

    def test_retention_keeps_recent(self, store):
        """30 天内的记录不受影响"""
        import time
        recent_ts = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 5 * 86400)
        )
        store._conn.execute(
            "INSERT INTO error_logs (ts, module, category, severity, message) "
            "VALUES (?, 'recent', 'rate_limit', 'error', '五天前的限流日志')",
            (recent_ts,),
        )
        store._conn.commit()
        store.log("m", "新日志")
        rows = store.query()
        assert len(rows) == 2
        assert any("五天前的限流日志" == r["message"] for r in rows)

    def test_clear(self, store):
        store.log("m", "x")
        n = store.clear()
        assert n == 1 and store.count() == 0


class TestClassify:
    def test_rate_limit_markers(self):
        assert classify_source_error("Connection aborted., RemoteDisconnected") == "rate_limit"
        assert classify_source_error("ProxyError('Cannot connect to proxy')") == "rate_limit"
        assert classify_source_error("HTTP 429 Too Many Requests") == "rate_limit"

    def test_timeout_and_network(self):
        assert classify_source_error("request timeout after 25s") == "timeout"
        assert classify_source_error("SSL certificate error") == "network"

    def test_other(self):
        assert classify_source_error("KeyError: 'data'") == "other"


@pytest.mark.asyncio
async def test_error_log_api_functions(tmp_path, monkeypatch):
    """路由函数端到端：上报→计数→列表→下载→清空（绕过 HTTP 层，直测逻辑）"""
    dbfile = tmp_path / "api_err.db"
    monkeypatch.setattr(mod, "DB_PATH", dbfile)
    with mod.ErrorLogStore._lock:
        mod.ErrorLogStore._instance = None
    try:
        from backend.routers.system_config import (
            list_error_logs, count_error_logs, clear_error_logs, report_error,
        )

        await report_error({
            "module": "frontend.test", "message": "页面请求失败",
            "category": "network", "severity": "error",
        })
        cnt = (await count_error_logs(since=None)).data["count"]
        assert cnt >= 1
        rows = (await list_error_logs(limit=10, category=None, since=None)).data
        assert any(x["module"] == "frontend.test" for x in rows)
        # 下载文本可生成
        text = await asyncio.to_thread(mod.ErrorLogStore().download_text, None)
        assert "页面请求失败" in text
        await clear_error_logs()
        assert (await count_error_logs(since=None)).data["count"] == 0
    finally:
        with mod.ErrorLogStore._lock:
            mod.ErrorLogStore._instance = None
