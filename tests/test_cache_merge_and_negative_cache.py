"""批次1-6：失败负缓存 + 部分结果按 key 合并 + 行情快照不写全空帧

三条同源问题（2026-10-01 审查 P2）：
1. 取数失败不推进任何时间戳 → 下一个请求原路再打一次，前端轮询把一次风控
   抖动放大成请求风暴（防封禁预算要求的是**减少**上游压力）；
2. 部分成功整批覆盖/丢弃 → 60 只里挂 3 只时阶段涨幅被写成 None、4 个指数
   里挂 1 个时卡片忽隐忽现；
3. 行情五板块全空时照旧落库并推进 updated_at → 仪表盘永远空白却显示
   "刚刚更新"，故障被伪装成数据已最新。
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from backend.schemas.market import MarketAdvDecline  # noqa: E402
from backend.services import fund_cache_service as fcs  # noqa: E402
from backend.services.index_valuation_service import (  # noqa: E402
    IndexValuationService, OtcTradeStatusService,
)


@pytest.fixture
def _clean_ivs(real_valuation_services):
    """解除 conftest 的 get_valuations/get_status_map 屏蔽 + 还原类级状态"""
    saved = (
        IndexValuationService._cache, IndexValuationService._ts,
        IndexValuationService._fail_until,
        OtcTradeStatusService._cache, OtcTradeStatusService._ts,
        OtcTradeStatusService._fail_until,
    )
    IndexValuationService._cache = None
    IndexValuationService._ts = 0.0
    IndexValuationService._fail_until = 0.0
    OtcTradeStatusService._cache = None
    OtcTradeStatusService._ts = 0.0
    OtcTradeStatusService._fail_until = 0.0
    yield
    (
        IndexValuationService._cache, IndexValuationService._ts,
        IndexValuationService._fail_until,
        OtcTradeStatusService._cache, OtcTradeStatusService._ts,
        OtcTradeStatusService._fail_until,
    ) = saved


def _row(name, pe=12.0, pct=40.0):
    return {
        "index": name, "pe": pe, "percentile_1y": pct, "zone": "合理",
        "advice": "合理区间", "updated": "2026-09-30",
    }


class TestIndexValuationNegativeCache:
    @pytest.mark.asyncio
    async def test_failure_sets_cooldown_and_stops_retrying(self, monkeypatch, _clean_ivs):
        calls: list[int] = []

        async def _boom(func, timeout=None, **kw):
            calls.append(1)
            raise RuntimeError("上游风控")
        monkeypatch.setattr("backend.utils.concurrency.run_with_timeout", _boom)

        assert await IndexValuationService.get_valuations() == []
        assert calls == [1]
        assert IndexValuationService._fail_until > time.time()

        # 冷却窗口内第二个请求不得再打上游
        assert await IndexValuationService.get_valuations() == []
        assert calls == [1]

        # force=True 是用户手动刷新，允许突破冷却
        await IndexValuationService.get_valuations(force=True)
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_empty_result_also_cooldowns(self, monkeypatch, _clean_ivs):
        """接口通了但一行没有：同样是失败，不能下个请求再来一遍"""
        calls: list[int] = []

        async def _empty(func, timeout=None, **kw):
            calls.append(1)
            return []
        monkeypatch.setattr("backend.utils.concurrency.run_with_timeout", _empty)

        assert await IndexValuationService.get_valuations() == []
        assert await IndexValuationService.get_valuations() == []
        assert calls == [1]

    @pytest.mark.asyncio
    async def test_partial_success_keeps_missing_index(self, monkeypatch, _clean_ivs):
        """4 个指数抓成 3 个：缺的那个沿用上轮条目，而不是整批只剩 3 个"""
        async def _partial(func, timeout=None, **kw):
            return [_row("沪深300", pe=13.1), _row("中证500", pe=22.0)]
        monkeypatch.setattr("backend.utils.concurrency.run_with_timeout", _partial)

        IndexValuationService._cache = [
            _row("沪深300", pe=12.0), _row("中证500", pe=21.0),
            _row("上证50", pe=9.9), _row("中证1000", pe=30.0),
        ]
        IndexValuationService._ts = time.time() - 7200   # 过 TTL 待刷新，但仍在合并保留期内

        out = await IndexValuationService.get_valuations()

        assert [r["index"] for r in out] == ["沪深300", "中证500", "上证50", "中证1000"]
        assert out[0]["pe"] == 13.1          # 本轮新值覆盖
        assert out[2]["pe"] == 9.9           # 本轮缺失沿用旧值
        assert IndexValuationService._fail_until == 0.0

    @pytest.mark.asyncio
    async def test_stale_prev_cache_not_merged(self, monkeypatch, _clean_ivs):
        """上一轮缓存超过保留期时不合并：否则下线指数会永远挂在页面上"""
        async def _partial(func, timeout=None, **kw):
            return [_row("沪深300")]
        monkeypatch.setattr("backend.utils.concurrency.run_with_timeout", _partial)

        IndexValuationService._cache = [_row("沪深300"), _row("上证50")]
        IndexValuationService._ts = time.time() - IndexValuationService._STALE_KEEP - 1

        out = await IndexValuationService.get_valuations()

        assert [r["index"] for r in out] == ["沪深300"]


class TestOtcStatusNegativeCache:
    @pytest.mark.asyncio
    async def test_failure_cooldown(self, monkeypatch, _clean_ivs):
        calls: list[int] = []

        async def _boom(func, timeout=None, **kw):
            calls.append(1)
            raise RuntimeError("东财不可达")
        monkeypatch.setattr("backend.utils.concurrency.run_with_timeout", _boom)

        assert await OtcTradeStatusService.get_status_map() == {}
        assert await OtcTradeStatusService.get_status_map() == {}
        assert calls == [1]
        assert OtcTradeStatusService._fail_until > time.time()

    @pytest.mark.asyncio
    async def test_success_clears_cooldown(self, monkeypatch, _clean_ivs):
        async def _ok(func, timeout=None, **kw):
            return {"004011": {"purchase": "开放申购", "redeem": "开放赎回", "fee": "0.15%"}}
        monkeypatch.setattr("backend.utils.concurrency.run_with_timeout", _ok)

        m = await OtcTradeStatusService.get_status_map()

        assert m["004011"]["purchase"] == "开放申购"
        assert OtcTradeStatusService._fail_until == 0.0


class TestHealthSnapshotListsCooldowns:
    def test_new_cooldowns_registered(self, monkeypatch, _clean_ivs):
        from backend.services.data_source_health import collect_health

        IndexValuationService._fail_until = time.time() + 60
        names = {c["name"] for c in collect_health()["cooldowns"]}
        assert "index_valuation.leiguru" in names

        IndexValuationService._fail_until = time.time() - 1
        names = {c["name"] for c in collect_health()["cooldowns"]}
        assert "index_valuation.leiguru" not in names


class TestMarketSummaryCacheFrame:
    def test_has_data_detects_all_empty(self):
        empty = fcs.build_market_summary_payload(None, [], None, None, None)
        assert fcs.market_cache_has_data(empty) is False
        assert fcs.market_cache_has_data(None) is False
        partial = fcs.build_market_summary_payload(
            None, [], None, MarketAdvDecline(up_count=1, down_count=1, total_count=2), None
        )
        assert fcs.market_cache_has_data(partial) is True

    @pytest.mark.asyncio
    async def test_all_empty_frame_keeps_old_row(self, db_session):
        """全空帧不写库、不推进 updated_at（旧行保留）"""
        good = fcs.build_market_summary_payload(
            None, [], None, MarketAdvDecline(up_count=3000, down_count=1500, total_count=4500), None
        )
        first_at = await fcs.store_market_summary(db_session, good)
        assert first_at

        old_at = await fcs.store_market_summary(
            db_session, fcs.build_market_summary_payload(None, [], None, None, None)
        )
        cached, updated_at = await fcs.get_cached_json(db_session, fcs.CACHE_KEY_MARKET_SUMMARY)

        assert old_at == first_at
        assert updated_at == first_at
        assert cached["adv_decline"]["up_count"] == 3000

    @pytest.mark.asyncio
    async def test_legacy_all_empty_row_not_served_as_cache(self, db_session):
        """历史遗留的全空行要能被实拉覆盖，否则永远自愈不了"""
        await fcs.set_cached_json(db_session, fcs.CACHE_KEY_MARKET_SUMMARY, {
            "market_flow": None, "sector_flow": [], "hsgt_flow": None,
            "adv_decline": None, "turnover": None,
        })
        cache, _at = await fcs.get_cached_json(db_session, fcs.CACHE_KEY_MARKET_SUMMARY)
        assert fcs.market_cache_has_data(cache) is False


class TestPeriodReturnsMerge:
    @pytest.mark.asyncio
    async def test_missing_code_keeps_previous_values(self, db_session, monkeypatch):
        """60 只里挂 3 只时不得把那 3 只的缓存清成 None"""
        await fcs.set_cached_json(db_session, fcs.CACHE_KEY_PERIOD_RETURNS, [
            {"code": "004011", "name": "A", "return_1m": 9.9,
             "return_3m": 9.9, "return_6m": 9.9, "return_1y": 9.9},
            {"code": "510300", "name": "B", "return_1m": 1.1,
             "return_3m": 2.2, "return_6m": 3.3, "return_1y": 4.4},
        ])

        async def _texts(codes):
            return {"004011": "JS_A"}                     # 510300 本轮抓取失败
        monkeypatch.setattr(fcs, "fetch_all_js_texts", _texts)
        monkeypatch.setattr(
            fcs, "_parse_period_returns",
            lambda t: {"return_1m": 1.0, "return_3m": 2.0, "return_6m": 3.0, "return_1y": 4.0},
        )

        rows, _ = await fcs.update_period_returns_cache(
            db_session, ["004011", "510300"], {"004011": "A", "510300": "B"}
        )

        b = next(r for r in rows if r["code"] == "510300")
        assert b["return_1y"] == 4.4          # 未被 None 覆盖
        a = next(r for r in rows if r["code"] == "004011")
        assert a["return_1y"] == 4.0          # 本轮抓到的以本轮为准
        cached, _at = await fcs.get_cached_period_returns(db_session)
        assert next(r for r in cached if r["code"] == "510300")["return_1y"] == 4.4

    @pytest.mark.asyncio
    async def test_extended_detail_cache_merges_by_code(self, db_session, monkeypatch):
        monkeypatch.setattr(
            fcs, "_parse_extended_data",
            lambda t: {"grand_total": 1.5, "fluctuation_scale": 0.2},
        )
        await fcs.set_cached_json(db_session, fcs.CACHE_KEY_EXTENDED_DETAIL, {
            "968049": {"grand_total": 3.3, "fluctuation_scale": 0.1, "name": "老基金"},
        })

        fresh = await fcs.update_extended_detail_cache(
            db_session, {"004011": "JS"}, {"004011": "A"}
        )
        cached, _ = await fcs.get_cached_json(db_session, fcs.CACHE_KEY_EXTENDED_DETAIL)

        assert list(fresh) == ["004011"]      # 返回值只含本轮，下游按它落季度表
        assert set(cached) == {"968049", "004011"}   # 缓存按代码合并，不整表覆盖


class TestClearCacheKeepsBreakers:
    def test_failure_cooldown_survives_plain_clear(self):
        from backend.services.market_service import MarketService

        saved_fail = dict(MarketService._fail_cache)
        saved = dict(MarketService._cache)
        try:
            MarketService._cache["k"] = (time.time(), object())
            MarketService._fail_cache["market_flow"] = time.time() + 120
            MarketService.clear_cache()
            assert MarketService._cache == {}
            assert "market_flow" in MarketService._fail_cache

            MarketService.clear_cache(include_failures=True)
            assert MarketService._fail_cache == {}
        finally:
            MarketService._cache.clear()
            MarketService._cache.update(saved)
            MarketService._fail_cache.clear()
            MarketService._fail_cache.update(saved_fail)
