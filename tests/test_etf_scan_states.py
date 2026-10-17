"""潜力 ETF 扫描三态可观测性回归（2026-10-17 根因修复）

用户看到的"扫描出来 0 条数据"实际有三种完全不同的原因，此前返回体同形：
1. `system_config.etf_hints_enabled = "false"` → 端点首行短路（生产/开发库都是这种）；
2. 东财全市场快照被熔断/拒绝 → 只有 error 文案，且写死"约 10 分钟"与真实冷却无关；
3. 快照到手但全是腾讯降级行（不含量比/换手等场内字段）→ 整批被跳过。

补丁域名那条另见 test_url_safety.TestEastMoneyPush2DelayHost。

本文件零真实上游请求：`_get_etf_spot` 全部 monkeypatch。
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from backend.models.system_config import SystemConfig  # noqa: E402
from backend.routers.fund import etf_scan  # noqa: E402
from backend.services import fund_realtime_service as _frs  # noqa: E402
from backend.services.etf_signal_service import scan_potential  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_breakers():
    """熔断状态是类属性，跑完必须还原，否则会让同进程其他用例误判源不可用"""
    saved = dict(_frs.FundRealtimeService._source_fail_until)
    yield
    _frs.FundRealtimeService._source_fail_until.clear()
    _frs.FundRealtimeService._source_fail_until.update(saved)


def _forbid_upstream(monkeypatch):
    """把快照入口换成"调用即失败"，用来锁定短路行为"""
    async def _boom(*a, **kw):
        raise AssertionError("开关关闭时不得发起全市场快照请求")
    monkeypatch.setattr(_frs.FundRealtimeService, "_get_etf_spot", _boom)


class TestClosedFlagState:
    async def test_disabled_returns_reason_and_no_upstream(self, db_session, monkeypatch):
        db_session.add(SystemConfig(config_key="etf_hints_enabled", config_value="false"))
        await db_session.commit()
        _forbid_upstream(monkeypatch)

        d = (await etf_scan(db_session)).data
        assert d["enabled"] is False
        assert "功能开关" in d["reason"], "关闭态必须告诉用户去哪里开"
        assert (d["scanned"], d["snapshot_rows"], d["skipped_no_fields"]) == (0, 0, 0)
        assert d["movers"] == [] and d["unusual"] == []
        assert "error" not in d, "关闭态不是故障态，不得同时报 error"


class TestSourceUnavailableState:
    async def test_error_names_breaking_source_and_remaining_seconds(
        self, db_session, monkeypatch
    ):
        async def _none(self, codes=None):
            return None
        monkeypatch.setattr(_frs.FundRealtimeService, "_get_etf_spot", _none)
        _frs.FundRealtimeService._source_fail_until[_frs.EM_SOURCE] = time.time() + 300

        d = (await etf_scan(db_session)).data
        assert d["enabled"] is True and d["scanned"] == 0
        assert _frs.EM_SOURCE in d["error"]
        assert "剩余" in d["error"], "不能再写死「约 10 分钟」"
        assert "10 分钟后自动恢复" not in d["error"]

    async def test_no_breaker_still_reports_honestly(self, db_session, monkeypatch):
        async def _none(self, codes=None):
            return None
        monkeypatch.setattr(_frs.FundRealtimeService, "_get_etf_spot", _none)

        d = (await etf_scan(db_session)).data
        assert "上游拒绝连接" in d["error"]


class TestDegradedSnapshotState:
    async def test_tencent_rows_without_fields_are_counted_not_hidden(
        self, db_session, monkeypatch
    ):
        """快照收到 2 行但都不含量价字段 → scanned=0 且必须能自证原因"""
        spot = {
            "510300": {"name": "沪深300ETF", "price": 4.0, "pct": 1.0},
            "510500": {"name": "中证500ETF", "price": 6.0, "pct": -0.5},
        }

        async def _degraded(self, codes=None):
            return spot
        monkeypatch.setattr(_frs.FundRealtimeService, "_get_etf_spot", _degraded)

        d = (await etf_scan(db_session)).data
        assert (d["scanned"], d["snapshot_rows"], d["skipped_no_fields"]) == (0, 2, 2)
        assert "error" not in d, "拿到快照就不是数据源不可用，不能报 error"


class TestScanCounters:
    def test_counters_match_row_split(self):
        full = {"name": "A", "pct": 3.0, "volume_ratio": 2.0,
                "main_inflow_pct": 12.0, "turnover_rate": 1.0, "amount": 2e8}
        lean = {"name": "B", "pct": 1.0, "price": 1.0}
        r = scan_potential({"510001": full, "510002": lean, "510003": lean}, set())
        assert (r["scanned"], r["snapshot_rows"], r["skipped_no_fields"]) == (1, 3, 2)

    def test_empty_map_counters(self):
        r = scan_potential({}, set())
        assert (r["scanned"], r["snapshot_rows"], r["skipped_no_fields"]) == (0, 0, 0)
