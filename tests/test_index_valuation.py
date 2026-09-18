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
        return [{"index": "沪深300", "pe": 12.8, "percentile_1y": 55.0,
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
