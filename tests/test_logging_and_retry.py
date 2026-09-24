"""日志轮转 + 调度重试 + 限连错误分类 测试"""

import logging
import logging.handlers
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

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
# 2.5 超时空消息可读化 + 风控 JSParse 归类（2026-09-23 报错排查）
# ═══════════════════════════════════════════════════════════════════

class TestErrBriefAndClassify:
    def test_timeout_empty_msg_humanized(self):
        import asyncio
        from backend.data_sources.akshare_adapter import _err_brief
        reason, msg = _err_brief(asyncio.TimeoutError(), 60.0)
        assert reason == "TimeoutError"
        assert "60s" in msg

    def test_non_timeout_empty_msg_unchanged(self):
        from backend.data_sources.akshare_adapter import _err_brief
        _, msg = _err_brief(RuntimeError(), 25.0)
        assert msg == "(no error message)"

    def test_jsparse_is_rate_limited(self):
        # pingzhongdata 被风控返回非 JS → py_mini_racer JSParseException
        from backend.data_sources.akshare_adapter import _is_rate_limited
        assert _is_rate_limited(Exception("Unknown JavaScript error during parse")) is True

    def test_classify_with_reason_prefix(self):
        from backend.services.error_log_service import classify_source_error
        assert classify_source_error(
            "JSParseException: Unknown JavaScript error during parse"
        ) == "rate_limit"
        # 原缺陷：TimeoutError str 为空，只传 msg 落 other；带类名后命中 timeout
        assert classify_source_error("TimeoutError: 请求超过 60s 未完成") == "timeout"

    @pytest.mark.asyncio
    async def test_call_timeout_override_and_message(self):
        from backend.data_sources.akshare_adapter import AKShareAdapter

        adapter = AKShareAdapter.__new__(AKShareAdapter)
        adapter.BASE_DELAY = 0.01
        seen = {}

        async def fake_run_with_timeout(func, *args, timeout=None, **kwargs):
            seen["timeout"] = timeout
            raise TimeoutError()

        with patch(
            "backend.utils.concurrency.run_with_timeout",
            new=fake_run_with_timeout,
        ):
            with pytest.raises(TimeoutError):
                await adapter._call(lambda: None, _timeout=60.0, _max_attempts=1)

        assert seen["timeout"] == 60.0


# ═══════════════════════════════════════════════════════════════════
# 2.6 rank_em 全量名称拉取失败冷却（防重试风暴）
# ═══════════════════════════════════════════════════════════════════

class TestFundNameFetchCooldown:
    @staticmethod
    def _isolate(monkeypatch, tmp_path, fail_ts):
        from backend.data_sources.akshare_adapter import AKShareAdapter
        monkeypatch.setattr(AKShareAdapter, "_fund_name_map", None)
        monkeypatch.setattr(AKShareAdapter, "_fund_rank_df", None)
        monkeypatch.setattr(AKShareAdapter, "_cache_timestamp", 0.0)
        monkeypatch.setattr(AKShareAdapter, "_fund_name_fail_ts", fail_ts)
        # 锁按当前事件循环 lazy 重建，避免跨用例绑旧 loop
        monkeypatch.setattr(AKShareAdapter, "_fund_name_lock", None)
        monkeypatch.setattr(AKShareAdapter, "_CACHE_FILE", str(tmp_path / "names.json"))

    @pytest.mark.asyncio
    async def test_cooldown_skips_network(self, tmp_path, monkeypatch):
        import time as _t
        from backend.data_sources.akshare_adapter import AKShareAdapter

        self._isolate(monkeypatch, tmp_path, _t.time() - 10)
        adapter = AKShareAdapter.__new__(AKShareAdapter)
        adapter._call = AsyncMock()
        assert await adapter._get_cached_fund_name("004011") is None
        adapter._call.assert_not_called()

    @pytest.mark.asyncio
    async def test_after_cooldown_retries_and_empty_sets_fail_ts(self, tmp_path, monkeypatch):
        import pandas as pd
        import time as _t
        from backend.data_sources.akshare_adapter import AKShareAdapter

        self._isolate(monkeypatch, tmp_path, 0.0)
        adapter = AKShareAdapter.__new__(AKShareAdapter)
        adapter._call = AsyncMock(return_value=pd.DataFrame())
        assert await adapter._get_cached_fund_name("004011") is None
        # 空数据同样进入冷却，防止 miss 风暴反复打 rank 接口
        assert AKShareAdapter._fund_name_fail_ts > _t.time() - 5
        # 冷却窗口内后续查找不再发请求
        assert await adapter._get_cached_fund_name("004011") is None
        adapter._call.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_resets_fail_ts(self, tmp_path, monkeypatch):
        import pandas as pd
        from backend.data_sources.akshare_adapter import AKShareAdapter

        self._isolate(monkeypatch, tmp_path, 1.0)
        adapter = AKShareAdapter.__new__(AKShareAdapter)
        df = pd.DataFrame({"基金代码": ["004011"], "基金简称": ["华泰柏瑞易利灵活配置混合C"]})
        adapter._call = AsyncMock(return_value=df)
        assert await adapter._get_cached_fund_name("004011") == "华泰柏瑞易利灵活配置混合C"
        assert AKShareAdapter._fund_name_fail_ts == 0.0


# ═══════════════════════════════════════════════════════════════════
# 2.7 f10/lsjz 备用净值链路翻页（服务端每页固定 20 行）
# ═══════════════════════════════════════════════════════════════════

class TestOtcNavRawPaging:
    @staticmethod
    def _make_adapter():
        from backend.data_sources.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter.__new__(AKShareAdapter)
        return adapter

    @staticmethod
    def _page_response(page_index, page_size=20, total_rows=100):
        """构造 f10/lsjz 风格响应：第 n 页返回 [(n-1)*20, n*20) 区间内的行（新→旧）"""
        from datetime import date, timedelta

        start = (page_index - 1) * page_size
        n = max(0, min(page_size, total_rows - start))
        rows = [
            {
                "FSRQ": (date(2026, 9, 23) - timedelta(days=start + i)).isoformat(),
                "DWJZ": f"{1.0 + (start + i) * 0.001:.4f}",
                "LJJZ": "1.5",
                "JZZZL": "0.1",
            }
            for i in range(n)
        ]
        resp = MagicMock()
        resp.json.return_value = {"Data": {"LSJZList": rows, "TotalCount": total_rows}, "ErrCode": 0}
        resp.raise_for_status.return_value = None
        return resp

    @pytest.mark.asyncio
    async def test_loops_pages_until_enough_rows(self, monkeypatch):
        import pandas as pd
        import requests

        calls = []

        def fake_get(url, headers=None, params=None, timeout=None):
            calls.append(params)
            return self._page_response(params["pageIndex"], total_rows=100)

        async def no_sleep(*_a, **_k):
            return None

        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr("asyncio.sleep", no_sleep)
        adapter = self._make_adapter()

        df = await adapter._get_otc_fund_nav_raw("004011", period=50)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 50
        # 50 条需翻 3 页（末页取回后 60≥50 提前停止）
        assert [c["pageIndex"] for c in calls] == [1, 2, 3]
        assert all(c["pageSize"] == 20 for c in calls)
        assert df["净值日期"].is_monotonic_increasing

    @pytest.mark.asyncio
    async def test_partial_pages_then_failure_keeps_rows(self, monkeypatch):
        """首页成功、次页异常 → 返回已取回的部分数据而非 None"""
        import requests

        def fake_get(url, headers=None, params=None, timeout=None):
            if params["pageIndex"] == 1:
                return self._page_response(1, total_rows=100)
            raise requests.exceptions.ConnectionError("closed by peer")

        async def no_sleep(*_a, **_k):
            return None

        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr("asyncio.sleep", no_sleep)
        adapter = self._make_adapter()

        df = await adapter._get_otc_fund_nav_raw("004011", period=60)
        assert df is not None
        assert len(df) == 20

    @pytest.mark.asyncio
    async def test_first_page_failure_returns_none(self, monkeypatch):
        import requests

        def fake_get(url, headers=None, params=None, timeout=None):
            raise requests.exceptions.ConnectionError("closed by peer")

        monkeypatch.setattr(requests, "get", fake_get)
        adapter = self._make_adapter()
        assert await adapter._get_otc_fund_nav_raw("004011", period=60) is None

    @pytest.mark.asyncio
    async def test_max_pages_cap(self, monkeypatch):
        """period 很大时翻页数封顶 _LSJZ_MAX_PAGES，不死循环"""
        import requests

        calls = []

        def fake_get(url, headers=None, params=None, timeout=None):
            calls.append(params["pageIndex"])
            return self._page_response(params["pageIndex"], total_rows=10_000)

        async def no_sleep(*_a, **_k):
            return None

        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr("asyncio.sleep", no_sleep)
        adapter = self._make_adapter()

        df = await adapter._get_otc_fund_nav_raw("004011", period=500)
        assert len(calls) == adapter._LSJZ_MAX_PAGES
        assert len(df) == adapter._LSJZ_MAX_PAGES * 20


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


# ═══════════════════════════════════════════════════════════════════
# 深交所 ETF 份额数据（规模稳定性因子数据源）
# ═══════════════════════════════════════════════════════════════════

class TestFillFundSizeSzse:
    """fund_scale_daily_szse 只接受 YYYYMMDD，且宽区间直接返回 0 行"""

    @staticmethod
    def _fake_df():
        import pandas as pd
        from datetime import date, timedelta
        d0 = date.today()
        rows = [
            {"日期": d0 - timedelta(days=i), "基金代码": "159915",
             "基金简称": "创业板ETF", "基金份额": 1_000_000.0 * (i + 1)}
            for i in range(6)
        ]
        rows.append({"日期": d0, "基金代码": "159916", "基金简称": "其他", "基金份额": 1.0})
        return pd.DataFrame(rows)

    @pytest.mark.asyncio
    async def test_date_format_is_yyyymmdd_and_window_short(self):
        from datetime import datetime

        from backend.data_sources.akshare_adapter import AKShareAdapter
        from backend.data_sources.base import FundData

        captured = {}

        async def fake_call(*args, **kwargs):
            captured.update(kwargs)
            return self._fake_df()

        fd = FundData(code="159915")
        with patch.object(AKShareAdapter, "_call", new=fake_call):
            await AKShareAdapter()._fill_fund_size("159915", fd)

        for key in ("start_date", "end_date"):
            assert len(captured[key]) == 8 and captured[key].isdigit(), f"{key}={captured[key]} 非 YYYYMMDD"
            datetime.strptime(captured[key], "%Y%m%d")  # 进一步验证可解析
        # 宽区间会返回空表，窗口必须收紧到可命中数据的短区间
        span = (datetime.strptime(captured["end_date"], "%Y%m%d")
                - datetime.strptime(captured["start_date"], "%Y%m%d")).days
        assert span <= 60

        # 取按日期升序的最后 4 期
        assert len(fd.fund_size_history) == 4
        assert fd.fund_size_history == sorted(fd.fund_size_history, reverse=True)

    @pytest.mark.asyncio
    async def test_non_szse_code_skips_network(self):
        from backend.data_sources.akshare_adapter import AKShareAdapter
        from backend.data_sources.base import FundData

        calls = {"n": 0}

        async def fake_call(*args, **kwargs):
            calls["n"] += 1
            return self._fake_df()

        fd = FundData(code="510300")
        with patch.object(AKShareAdapter, "_call", new=fake_call):
            await AKShareAdapter()._fill_fund_size("510300", fd)

        assert calls["n"] == 0 and fd.fund_size_history == []


# ═══════════════════════════════════════════════════════════════════
# 6. 告警分类三处真实样本回归（2026-09-24 系统错误告警复查）
# ═══════════════════════════════════════════════════════════════════

class TestAlertCategoryRealSamples:
    """按 error_logs 里的真实异常文本回归归类

    - py_mini_racer 喂到风控 HTML 页 → JSParseException 的另一变体，此前既不被
      识别为限连（于是退避重试 2 次，加重封禁），也被归到"其他"掩盖风控信号；
    - 本机 Python 缺 CA 的 CERTIFICATE_VERIFY_FAILED 含 "verify"，被极验标记
      误判成"限流"，使环境问题长期伪装成风控告警。
    """

    JSPARSE_HTML = (
        "<anonymous>:2: SyntaxError: Unexpected token '<'\n"
        "<!doctype html>\n<html><head><title>403</title></head></html>"
    )
    SSL_CERT = (
        "<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify "
        "failed: unable to get local issuer certificate (_ssl.c:1129)>"
    )

    async def _attempts_and_category(self, message: str):
        """以固定异常文本跑一次 _call → (实际尝试次数, 落库 category)"""
        import backend.services.error_log_service as els
        from backend.data_sources.akshare_adapter import AKShareAdapter

        adapter = AKShareAdapter.__new__(AKShareAdapter)  # 跳过 __init__（避免网络）
        state = {"n": 0, "category": None}

        async def fake_run(func, *args, timeout=None, **kwargs):
            state["n"] += 1
            raise Exception(message)

        def fake_log(module, message, category=None, severity=None, detail=None):
            state["category"] = category

        with patch("backend.utils.concurrency.run_with_timeout", new=fake_run), \
                patch.object(els, "log_source_failure", new=fake_log):
            with pytest.raises(Exception):
                await adapter._call(lambda: None, _max_attempts=3)
        return state

    @pytest.mark.asyncio
    async def test_jsparse_html_skips_retry_and_marks_rate_limit(self):
        state = await self._attempts_and_category(self.JSPARSE_HTML)
        assert state["n"] == 1          # 不重试（防加重封禁）
        assert state["category"] == "rate_limit"

    @pytest.mark.asyncio
    async def test_cert_error_labelled_network_not_rate_limit(self):
        state = await self._attempts_and_category(self.SSL_CERT)
        # 证书失败是确定性本地故障，重试同样无益 → 仍跳过；但归类必须诚实
        assert state["n"] == 1
        assert state["category"] == "network"

    def test_classify_helpers(self):
        from backend.data_sources.akshare_adapter import _is_cert_error, _is_rate_limited
        from backend.services.error_log_service import classify_source_error

        js = Exception(self.JSPARSE_HTML)
        assert _is_rate_limited(js) is True
        ssl = Exception(self.SSL_CERT)
        assert _is_cert_error(ssl) is True
        assert classify_source_error(f"URLError: {self.SSL_CERT}") == "network"
        # 真·极验页仍归限流
        assert classify_source_error("RuntimeError: 请完成滑块验证 verify") == "rate_limit"
