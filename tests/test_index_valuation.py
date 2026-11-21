"""指数估值端点回归 — H1（未定义 db 参数 NameError 500）修复锁定"""

import sys, os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.mark.asyncio
async def test_index_valuations_endpoint(db_session, monkeypatch):
    """端点可调（H1: 原 _feature_flag(db,...) 引用未定义 db → NameError 500）"""
    from backend.routers.system_config import index_valuations

    async def _fake_flag(db, key, default=True):
        return True

    async def _fake_vals(force=False):
        return [{"index": "沪深300", "pe": 12.8, "pe_percentile": 55.0,
                 "zone": "合理", "advice": "合理区间", "updated": "2026-09-12"}]

    import backend.services.fund_realtime_service as _mod
    monkeypatch.setattr(_mod, "_feature_flag", _fake_flag)
    from backend.services.index_valuation_service import IndexValuationService
    monkeypatch.setattr(IndexValuationService, "get_valuations",
                        staticmethod(_fake_vals))

    resp = await index_valuations(force=False, db=db_session)
    assert resp.data[0]["index"] == "沪深300"


@pytest.mark.asyncio
async def test_index_valuations_gated(db_session, monkeypatch):
    """otc 开关关闭 → 空列表且不拉取"""
    from backend.routers.system_config import index_valuations

    async def _fake_flag(db, key, default=True):
        return False

    called = []
    async def _fake_vals(force=False):
        called.append(1)
        return []

    import backend.services.fund_realtime_service as _mod
    monkeypatch.setattr(_mod, "_feature_flag", _fake_flag)
    from backend.services.index_valuation_service import IndexValuationService
    monkeypatch.setattr(IndexValuationService, "get_valuations",
                        staticmethod(_fake_vals))

    resp = await index_valuations(force=False, db=db_session)
    assert resp.data == []
    assert not called


def test_merge_order_declared_with_zz800():
    """_merge 按声明顺序输出（中证800 增补后 5 个指数，部分成功不跳序）"""
    from backend.services.index_valuation_service import (
        IndexValuationService, SUPPORTED_INDEXES,
    )

    assert list(SUPPORTED_INDEXES) == ["沪深300", "中证500", "上证50", "中证1000", "中证800"]
    IndexValuationService._cache = None
    IndexValuationService._ts = 0.0
    try:
        # 仅部分成功：中证800 + 沪深300 抓到，其余沿用旧值（此处为空）
        merged = IndexValuationService._merge(
            [{"index": "中证800", "pe": 13.7}, {"index": "沪深300", "pe": 12.8}],
            now=1e9,
        )
        assert [m["index"] for m in merged] == ["沪深300", "中证800"]
    finally:
        IndexValuationService._cache = None


# ── 失败埋点（2026-11-21 对齐温度计）：1.93 数据停更却无线索的修复 ──

def _seed_cls(monkeypatch, cache=None):
    """隔离类级缓存状态（monkeypatch 自动还原）"""
    from backend.services.index_valuation_service import IndexValuationService
    monkeypatch.setattr(IndexValuationService, "_cache", cache)
    monkeypatch.setattr(IndexValuationService, "_ts", 0.0)
    monkeypatch.setattr(IndexValuationService, "_fail_until", 0.0)


def _patch_log_sink(monkeypatch):
    logged = []
    import backend.services.error_log_service as els
    monkeypatch.setattr(
        els, "log_source_failure",
        lambda module, message, category="other", severity="error", detail="":
        logged.append({"module": module, "message": message,
                       "category": category, "severity": severity,
                       "detail": detail}),
    )
    return logged


@pytest.mark.asyncio
async def test_valuation_total_failure_logged(real_valuation_services, monkeypatch):
    """乐咕整体失败 → error_logs 埋点 + 沿用旧缓存 + 失败冷却"""
    from backend.services.index_valuation_service import IndexValuationService
    import backend.utils.concurrency as conc

    old = [{"index": "沪深300", "pe": 12.8, "pe_percentile": 55.0,
            "zone": "合理", "advice": "x", "sample_points": 238,
            "series_start": "d", "updated": "2026-11-13"}]
    _seed_cls(monkeypatch, cache=old)
    logged = _patch_log_sink(monkeypatch)

    async def _boom(fn, timeout=0):
        raise TimeoutError("乐咕超时")

    monkeypatch.setattr(conc, "run_with_timeout", _boom)

    out = await IndexValuationService.get_valuations(force=True)
    assert out == old, "失败时沿用旧缓存（前端不空白）"
    assert IndexValuationService._fail_until > 0
    assert len(logged) == 1
    assert logged[0]["module"] == "valuation.leigu"
    assert logged[0]["category"] == "network"
    assert logged[0]["severity"] == "warning"
    assert "TimeoutError" in logged[0]["detail"]


@pytest.mark.asyncio
async def test_valuation_partial_failure_logged(real_valuation_services, monkeypatch):
    """部分指数失败 → 合并成功项 + 一条汇总埋点（哪些指数挂了在 detail）"""
    from backend.services.index_valuation_service import IndexValuationService
    import backend.utils.concurrency as conc

    _seed_cls(monkeypatch)
    logged = _patch_log_sink(monkeypatch)

    async def _fake(fn, timeout=0):
        return (
            [{"index": "沪深300", "pe": 12.8, "pe_percentile": 55.0,
              "zone": "合理", "advice": "x", "sample_points": 238,
              "series_start": "d", "updated": "d"}],
            ["中证500(TimeoutError: boom)"],
        )

    monkeypatch.setattr(conc, "run_with_timeout", _fake)

    out = await IndexValuationService.get_valuations(force=True)
    assert [v["index"] for v in out] == ["沪深300"]
    assert len(logged) == 1
    assert logged[0]["message"].startswith("指数高低估部分指数取数失败")
    assert "中证500" in logged[0]["detail"]


@pytest.mark.asyncio
async def test_valuation_empty_rows_logged_data_quality(real_valuation_services, monkeypatch):
    """接口通但 0 行 → data_quality 埋点（区别于网络失败）"""
    from backend.services.index_valuation_service import IndexValuationService
    import backend.utils.concurrency as conc

    _seed_cls(monkeypatch)
    logged = _patch_log_sink(monkeypatch)

    async def _fake(fn, timeout=0):
        return ([], [])

    monkeypatch.setattr(conc, "run_with_timeout", _fake)

    out = await IndexValuationService.get_valuations(force=True)
    assert out == []
    assert len(logged) == 1
    assert logged[0]["category"] == "data_quality"
