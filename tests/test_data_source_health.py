"""数据源健康快照端点测试（2026-09-29 审查 P3）

背景：冷却/降级状态散在各模块的类属性里，排查"仪表盘一片空"只能靠猜。
新增只读快照端点，这里锁定两件事：
1. 冷却项只在真正处于冷却时出现（过期项不得残留成"永远在冷却"）；
2. 读取方式必须能反映运行时更新（`from x import name` 会把值冻结在导入瞬间）。
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from backend.services import fund_manager_service  # noqa: E402
from backend.services.data_source_health import collect_health  # noqa: E402
from backend.services.fund_realtime_service import FundRealtimeService  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_state():
    """跑完把改动的进程内状态还原，避免污染同进程其他用例"""
    src = dict(FundRealtimeService._source_fail_until)
    gz = FundRealtimeService._fundgz_fail_until
    fail_ts = fund_manager_service._last_fail_time
    yield
    FundRealtimeService._source_fail_until.clear()
    FundRealtimeService._source_fail_until.update(src)
    FundRealtimeService._fundgz_fail_until = gz
    fund_manager_service._last_fail_time = fail_ts


def _cooldown_names(h):
    return {c["name"] for c in h["cooldowns"]}


class TestCooldowns:
    def test_active_breaker_reported_with_remaining(self):
        FundRealtimeService._source_fail_until["tencent"] = time.time() + 600
        h = collect_health()
        assert "realtime.tencent" in _cooldown_names(h)
        item = next(c for c in h["cooldowns"] if c["name"] == "realtime.tencent")
        assert 590 < item["remaining_seconds"] <= 600
        assert h["summary"]["cooldown_count"] == len(h["cooldowns"])

    def test_expired_cooldown_not_listed(self):
        FundRealtimeService._source_fail_until["tencent"] = time.time() - 1
        assert "realtime.tencent" not in _cooldown_names(collect_health())

    def test_manager_failure_reflects_live_module_attr(self):
        """经理表失败冷却靠模块属性读取：改成 from ... import 会冻结导入值而漏报"""
        fund_manager_service._last_fail_time = time.time()
        h = collect_health()
        assert "managers.fund_manager_em" in _cooldown_names(h)


class TestCaches:
    def test_fresh_and_stale_marked(self):
        from backend.services.index_valuation_service import IndexValuationService

        cache, ts = IndexValuationService._cache, IndexValuationService._ts
        try:
            IndexValuationService._cache = [{"index": "沪深300"}]
            IndexValuationService._ts = time.time()
            entry = next(c for c in collect_health()["caches"] if c["name"] == "index_valuation")
            assert entry["stale"] is False and entry["entries"] == 1

            IndexValuationService._ts = time.time() - IndexValuationService._TTL - 1
            entry = next(c for c in collect_health()["caches"] if c["name"] == "index_valuation")
            assert entry["stale"] is True
        finally:
            IndexValuationService._cache, IndexValuationService._ts = cache, ts


class TestEndpoint:
    @pytest.mark.asyncio
    async def test_envelope_shape(self):
        from backend.routers.system_config import data_source_health

        res = await data_source_health()
        assert res.code == 0
        data = res.data
        assert set(data) >= {"generated_at", "cooldowns", "caches", "scheduler", "summary"}
        assert isinstance(data["cooldowns"], list)
        assert data["scheduler"]["daily_fail_limit"] == 3

    @pytest.mark.asyncio
    async def test_scheduler_trip_reported(self):
        from backend.routers.system_config import data_source_health
        from backend.scheduler.task_scheduler import task_scheduler
        from backend.utils.timezone import beijing_today

        key = (beijing_today().isoformat(), 77)
        saved = dict(task_scheduler._daily_fail)
        try:
            task_scheduler._daily_fail[key] = task_scheduler.DAILY_FAIL_LIMIT
            data = (await data_source_health()).data
            assert key[1] in [t["schedule_id"] for t in data["scheduler"]["tripped_today"]]
        finally:
            task_scheduler._daily_fail.clear()
            task_scheduler._daily_fail.update(saved)
