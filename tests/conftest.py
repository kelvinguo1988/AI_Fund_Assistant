"""pytest 全局配置 — 测试防限流防线

2026-08-31：测试中任何真实重量级数据源调用都可能触发数据源限流
（用户红线：测试可以慢但不要触发限流）。本文件全局屏蔽两个
"单次请求量大"的调用源：

- OtcTradeStatusService.get_status_map  真实调用 = 拉全市场 24056 只申购状态
- IndexValuationService.get_valuations  真实调用 = 拉乐咕 4 指数 PE 历史
  （乐咕已实测对短时重复请求限流）

需要验证真实网络行为的测试应显式覆盖（monkeypatch 覆盖本 fixture 的补丁）。
其余轻量接口（ETF spot/个股快照/fundgz 单只）由各测试自行 mock。
"""

import pytest


@pytest.fixture(autouse=True)
def _block_heavy_network(monkeypatch):
    import backend.services.index_valuation_service as _iv

    async def _empty_map(self, force: bool = False):
        return {}

    async def _empty_vals(force: bool = False):
        return []

    monkeypatch.setattr(_iv.OtcTradeStatusService, "get_status_map", _empty_map)
    monkeypatch.setattr(_iv.IndexValuationService, "get_valuations", _empty_vals)
    # 雪球基本信息交叉源（每次标签刷新逐基金请求）
    import backend.services.fund_tag_service as _ft
    monkeypatch.setattr(_ft, "fetch_xq_basic", lambda code: None)


@pytest.fixture(autouse=True)
def _isolate_error_log_store(tmp_path, monkeypatch):
    """error_logs 测试隔离：埋点写临时库，不污染生产错误日志表

    2026-09-21 排查：导出日志 538 条中约 95% 为测试污染——
    test_logging_and_retry 的 _call(lambda) 与 test_realtime 的
    _mark_source_fail("eastmoney", "test") 直写真实 fund_quant.db，
    刷爆铃铛计数并淹没真实限流告警。
    """
    from backend.services import error_log_service as _els

    monkeypatch.setattr(_els, "DB_PATH", tmp_path / "error_logs_test.db")
    with _els.ErrorLogStore._lock:
        stale = _els.ErrorLogStore._instance
        _els.ErrorLogStore._instance = None
    if stale is not None:
        try:
            stale._conn.close()
        except Exception:
            pass
    yield
    with _els.ErrorLogStore._lock:
        inst = _els.ErrorLogStore._instance
        _els.ErrorLogStore._instance = None
    if inst is not None:
        try:
            inst._conn.close()
        except Exception:
            pass


@pytest.fixture
async def db_session():
    """共享内存库会话（原 4 个测试文件各自复制粘贴，统一于此）"""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from backend.database import Base
    import backend.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()
