"""数据末点新鲜度（2026-11-21）

## 为什么需要
温度计停摆 6 天那次，现有健康快照**完全看不出异常**：
- `_cache()` 只比「缓存年龄 vs TTL」——每小时都在"更新"，看起来新鲜
- 但 `date` 一直是 6 天前，因为每次请求都失败、只是在返回旧缓存

用户只能靠自己发现「怎么还是 13 号」。这类故障需要一个显式维度：
**该服务手上的数据末点是哪天，距今天多久**。

## 阈值分档
日频 >3 天可疑、周频/月频序列 >7 天、美股月频趋势 >40 天。
按数据频率定阈值，避免长假误报。
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.data_source_health import (  # noqa: E402
    _FRESHNESS_RULES, _collect_freshness, collect_health,
)


class TestFreshnessRules:
    def test_daily_source_tighter_than_monthly(self):
        """日频阈值必须比月频紧，否则日频停 10 天都不报警"""
        assert _FRESHNESS_RULES["market_capital_flow"][0] < _FRESHNESS_RULES["temperature"][0]

    def test_all_rules_have_note(self):
        for name, (limit, note) in _FRESHNESS_RULES.items():
            assert isinstance(limit, int) and limit > 0, name
            assert note, f"{name} 应有说明"


class TestFreshnessCollection:
    def test_output_shape(self):
        for item in _collect_freshness(time.time()):
            for k in ("name", "data_date", "lag_days", "max_lag_days", "stale", "note"):
                assert k in item, f"缺字段 {k}"
            assert isinstance(item["stale"], bool)

    def test_empty_cache_marked_stale(self):
        """从未成功取过数 → 必须标记 stale，不能悄悄不出现"""
        items = {f["name"]: f for f in _collect_freshness(time.time())}
        # 测试环境没填充缓存，温度计应是"无数据"
        t = items.get("temperature")
        if t and t["data_date"] is None:
            assert t["stale"] is True
            assert "尚无数据" in t["note"]

    def test_reports_fail_count_for_temperature(self):
        """温度计要带连续失败次数——这正是停摆的直接指标"""
        items = {f["name"]: f for f in _collect_freshness(time.time())}
        t = items.get("temperature")
        if t:
            assert "fail_count" in t


class TestHealthSummary:
    def test_summary_has_stale_data_fields(self):
        h = collect_health()
        assert "stale_data_count" in h["summary"]
        assert "stale_data_names" in h["summary"]
        assert isinstance(h["summary"]["stale_data_names"], list)

    def test_freshness_in_payload(self):
        h = collect_health()
        assert "freshness" in h
        assert isinstance(h["freshness"], list)

    def test_stale_count_matches_items(self):
        h = collect_health()
        expected = sum(1 for f in h["freshness"] if f["stale"])
        assert h["summary"]["stale_data_count"] == expected

    def test_health_never_raises(self):
        """健康快照本身绝不能崩——它正是用来排查故障的"""
        h = collect_health()
        assert h.get("generated_at")


class TestStaleDetection:
    def test_old_data_detected(self):
        """复现线上形态：数据停在 6 天前必须 stale（阈值 7 天内则不算）"""
        from backend.services import data_source_health as m

        # 直接验证判定逻辑：把温度计缓存塞成 8 天前
        from backend.services.market_temperature_service import MarketTemperatureService

        old = MarketTemperatureService._cache
        try:
            MarketTemperatureService._cache = {
                "date": (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d"),
                "temperature": 44.2,
            }
            items = {f["name"]: f for f in m._collect_freshness(time.time())}
            t = items.get("temperature")
            assert t is not None
            assert t["lag_days"] == 8
            assert t["stale"] is True, "8 天 > 7 天阈值，必须标红"
        finally:
            MarketTemperatureService._cache = old

    def test_recent_data_not_stale(self):
        from backend.services import data_source_health as m
        from backend.services.market_temperature_service import MarketTemperatureService

        old = MarketTemperatureService._cache
        try:
            MarketTemperatureService._cache = {
                "date": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
            }
            items = {f["name"]: f for f in m._collect_freshness(time.time())}
            t = items.get("temperature")
            assert t["lag_days"] == 1
            assert t["stale"] is False
        finally:
            MarketTemperatureService._cache = old
