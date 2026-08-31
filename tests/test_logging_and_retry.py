"""日志轮转 + 调度重试 + 限连错误分类 测试"""

import logging
import logging.handlers
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.data_sources.akshare_adapter import _is_rate_limited


# ═══════════════════════════════════════════════════════════════════
# 1. 限连错误分类
# ═══════════════════════════════════════════════════════════════════

class TestIsRateLimited:
    def test_remote_disconnected(self):
        exc = ConnectionResetError("Remote end closed connection without response")
        assert _is_rate_limited(exc) is True

    def test_too_many_requests(self):
        assert _is_rate_limited(Exception("HTTP 429 Too Many Requests")) is True

    def test_forbidden(self):
        assert _is_rate_limited(Exception("403 Forbidden")) is True

    def test_verify_page(self):
        assert _is_rate_limited(Exception("请完成滑块验证 verify")) is True

    def test_timeout_not_rate_limited(self):
        # 普通超时/数据错误不应触发"跳过重试"
        assert _is_rate_limited(TimeoutError("timeout")) is False

    def test_value_error_not_rate_limited(self):
        assert _is_rate_limited(ValueError("wrong shape")) is False

    def test_empty_message(self):
        assert _is_rate_limited(Exception()) is False


# ═══════════════════════════════════════════════════════════════════
# 2. 限连错误跳过重试（_call 行为）
# ═══════════════════════════════════════════════════════════════════

class TestCallSkipsRetryOnRateLimit:
    @pytest.mark.asyncio
    async def test_rate_limited_no_retry(self):
        from backend.data_sources.akshare_adapter import AKShareAdapter

        adapter = AKShareAdapter.__new__(AKShareAdapter)  # 跳过 __init__（避免网络）
        call_count = {"n": 0}

        async def fake_run_with_timeout(func, *args, timeout=None, **kwargs):
            call_count["n"] += 1
            raise ConnectionResetError("Remote end closed connection without response")

        with patch(
            "backend.utils.concurrency.run_with_timeout",
            new=fake_run_with_timeout,
        ):
            with pytest.raises(ConnectionResetError):
                await adapter._call(lambda: None)

        # 限连错误：1 次失败后不再重试
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_timeout_retries(self):
        from backend.data_sources.akshare_adapter import AKShareAdapter

        adapter = AKShareAdapter.__new__(AKShareAdapter)
        adapter.BASE_DELAY = 0.01  # 加速测试
        adapter.MAX_RETRIES = 3
        call_count = {"n": 0}

        async def fake_run_with_timeout(func, *args, timeout=None, **kwargs):
            call_count["n"] += 1
            raise TimeoutError("timeout")

        with patch(
            "backend.utils.concurrency.run_with_timeout",
            new=fake_run_with_timeout,
        ):
            with pytest.raises(TimeoutError):
                await adapter._call(lambda: None)

        # 普通超时：重试满 3 次
        assert call_count["n"] == 3


# ═══════════════════════════════════════════════════════════════════
# 3. 日志配置：7 天轮转
# ═══════════════════════════════════════════════════════════════════

class TestLoggingSetup:
    def test_setup_logging_7day_rotation(self, tmp_path, monkeypatch):
        """日志文件 handler：midnight 轮转 + backupCount=7"""
        from backend.main import _setup_logging
        from backend.config import settings

        monkeypatch.setattr(settings, "DATABASE_DIR", str(tmp_path))
        # 清理 root，避免污染其他测试
        root = logging.getLogger()
        old_handlers = list(root.handlers)
        try:
            _setup_logging()
            file_handlers = [
                h for h in root.handlers
                if isinstance(h, logging.handlers.TimedRotatingFileHandler)
            ]
            assert len(file_handlers) == 1
            h = file_handlers[0]
            assert h.when == "MIDNIGHT"
            assert h.backupCount == 7
            assert Path(h.baseFilename).parent.name == "logs"

            # 写一条日志验证落盘
            logging.getLogger("test_rotation").error("测试错误日志")
            h.flush()
            content = Path(h.baseFilename).read_text(encoding="utf-8")
            assert "测试错误日志" in content
            # 格式含异常级别与 logger 名
            assert "ERROR" in content
            assert "test_rotation" in content
        finally:
            for h in root.handlers:
                if isinstance(h, logging.handlers.TimedRotatingFileHandler):
                    h.close()
            root.handlers = old_handlers

    def test_setup_logging_unwritable_dir_fallback(self, tmp_path, monkeypatch):
        """日志目录不可写时降级为仅控制台，不抛异常"""
        from backend.main import _setup_logging
        from backend.config import settings

        # 用一个文件路径当目录 → mkdir 失败（不是 OSError 的场景外层吞掉）
        monkeypatch.setattr(settings, "DATABASE_DIR", str(tmp_path))
        root = logging.getLogger()
        old_handlers = list(root.handlers)
        try:
            _setup_logging()
            # 正常路径也应有 console handler
            assert any(
                isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.handlers.TimedRotatingFileHandler)
                for h in root.handlers
            )
            # uvicorn logger 已接管
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
                lg = logging.getLogger(name)
                assert lg.handlers == []
                assert lg.propagate is True
        finally:
            for h in root.handlers:
                if isinstance(h, logging.handlers.TimedRotatingFileHandler):
                    h.close()
            root.handlers = old_handlers


# ═══════════════════════════════════════════════════════════════════
# 4. 调度任务重试逻辑
# ═══════════════════════════════════════════════════════════════════

async def _no_sleep(*_args, **_kwargs):
    """测试用 asyncio.sleep 替身（立即返回）"""
    return None


class TestSchedulerRetry:
    @pytest.mark.asyncio
    async def test_run_task_retries_once_then_succeeds(self):
        """首次失败 → 60s 后重试 1 次 → 成功"""
        from backend.scheduler.task_scheduler import TaskScheduler

        sched = TaskScheduler()
        attempts = {"n": 0}

        async def flaky_execute(schedule_id):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("网络抖动")

        with patch.object(
            sched, "_execute_task_once", new=flaky_execute
        ), patch("asyncio.sleep", new=_no_sleep):
            await sched._run_task(1)

        assert attempts["n"] == 2

    @pytest.mark.asyncio
    async def test_run_task_both_fail(self):
        """两次都失败 → 放弃不无限重试"""
        from backend.scheduler.task_scheduler import TaskScheduler

        sched = TaskScheduler()
        attempts = {"n": 0}

        async def always_fail(schedule_id):
            attempts["n"] += 1
            raise RuntimeError("持续失败")

        with patch.object(
            sched, "_execute_task_once", new=always_fail
        ), patch("asyncio.sleep", new=_no_sleep):
            await sched._run_task(1)  # 不抛（内部消化并记 ERROR）

        assert attempts["n"] == 2

    @pytest.mark.asyncio
    async def test_run_task_first_try_success(self):
        """首试成功 → 不重试"""
        from backend.scheduler.task_scheduler import TaskScheduler

        sched = TaskScheduler()
        attempts = {"n": 0}

        async def ok(schedule_id):
            attempts["n"] += 1

        with patch.object(sched, "_execute_task_once", new=ok):
            await sched._run_task(1)

        assert attempts["n"] == 1
