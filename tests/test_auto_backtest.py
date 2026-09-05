"""自动全量回测回归测试 — 配置读写/逐只落库覆盖/失败行/防重入"""

import sys, os
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.models.backtest_result import BacktestResult
from backend.models.fund import Fund
from backend.models.system_config import SystemConfig
from backend.services.auto_backtest_service import (
    AutoBacktestService, get_auto_config,
    CFG_ENABLED, CFG_MIN_INTERVAL, CFG_MAX_INTERVAL,
)


@pytest_asyncio.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from backend.database import Base
    import backend.models

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _fund(id, code, name=""):
    return Fund(id=id, code=code, name=name, status="active")


class _FakeSummary:
    """模拟 BacktestService.run_backtest 的返回"""
    period = 365
    effectiveness_window = 5
    total_nav_return = 10.0
    total_strategy_return = 15.0
    excess_return = 5.0
    max_drawdown = 3.2
    signal_count = 12
    avg_effectiveness = 66.0
    buy_effectiveness = 70.0
    sell_effectiveness = 60.0
    effectiveness_rate = 75.0


@pytest.mark.asyncio
async def test_config_roundtrip(db_session):
    db_session.add(SystemConfig(config_key=CFG_ENABLED, config_value="true"))
    db_session.add(SystemConfig(config_key=CFG_MIN_INTERVAL, config_value="30"))
    await db_session.commit()
    cfg = await get_auto_config(db_session)
    assert cfg["enabled"] is True
    assert cfg["min_interval"] == 30.0
    assert cfg["max_interval"] == 60.0  # 默认值兜底

    # 坏值不崩溃
    db_session.add(SystemConfig(config_key=CFG_MAX_INTERVAL, config_value="abc"))
    await db_session.commit()
    cfg = await get_auto_config(db_session)
    assert cfg["max_interval"] == 60.0


@pytest.mark.asyncio
async def test_upsert_overwrites_per_fund(db_session):
    """同基金第二轮结果覆盖旧行（周期性覆盖 + 完成时间更新）"""
    db_session.add(_fund(1, "018994", "中欧数字经济混合发起C"))
    await db_session.commit()

    svc = AutoBacktestService(db_session)
    f1 = _fund(1, "018994", "中欧数字经济混合发起C")

    await svc._upsert_result(f1, _FakeSummary())
    await svc._upsert_result(f1, _FakeSummary())  # 第二轮

    from sqlalchemy import select as _select
    rows = list((await db_session.execute(_select(BacktestResult))).scalars().all())
    assert len(rows) == 1, "同基金应只保留一行"
    assert rows[0].total_strategy_return == 15.0
    assert rows[0].ok is True
    assert rows[0].finished_at is not None


@pytest.mark.asyncio
async def test_error_row_keeps_old_values(db_session):
    """失败行保留旧数值仅更新错误状态"""
    db_session.add(_fund(2, "016874", "广发远见智选混合C"))
    await db_session.commit()

    svc = AutoBacktestService(db_session)
    f2 = _fund(2, "016874", "广发远见智选混合C")
    await svc._upsert_result(f2, _FakeSummary())
    await svc._upsert_error(f2, "净值拉取超时")

    rows = list((await db_session.execute(
        __import__("sqlalchemy").select(BacktestResult)
    )).scalars().all())
    assert len(rows) == 1
    assert rows[0].ok is False
    assert rows[0].error == "净值拉取超时"
    assert rows[0].total_strategy_return == 15.0, "旧结果应保留"


@pytest.mark.asyncio
async def test_run_full_skips_when_running(db_session, monkeypatch):
    """运行中重复触发被跳过（防重入）"""
    AutoBacktestService._running = True
    try:
        svc = AutoBacktestService(db_session)
        stats = await svc.run_full_backtest()
        assert stats["skipped"] == 1
    finally:
        AutoBacktestService._running = False


@pytest.mark.asyncio
async def test_run_full_end_to_end(db_session, monkeypatch):
    """全流程：活跃基金逐只回测落库，间隔 sleep 被 mock（测试不等待）"""
    from backend.schemas.analysis import ReviewReport  # noqa: F401 触发依赖导入

    db_session.add_all([_fund(1, "018994"), _fund(2, "016874"), _fund(3, "510300", "沪深300ETF")])
    await db_session.commit()

    calls = []

    class _FakeBacktestSvc:
        def __init__(self, db):
            pass
        async def run_backtest(self, fund_id, **kw):
            calls.append(fund_id)
            if fund_id == 3:
                raise RuntimeError("模拟净值拉取失败")
            return _FakeSummary()

    import backend.services.backtest_service as bs_mod
    monkeypatch.setattr(bs_mod, "BacktestService", _FakeBacktestSvc)
    # sleep 置零
    import backend.services.auto_backtest_service as ab_mod
    monkeypatch.setattr(ab_mod.asyncio, "sleep", _noop_sleep)

    svc = AutoBacktestService(db_session)
    stats = await svc.run_full_backtest()
    assert stats == {"total": 3, "ok": 2, "failed": 1, "skipped": 0}
    assert calls == [1, 2, 3]

    rows = list((await db_session.execute(
        __import__("sqlalchemy").select(BacktestResult).order_by(BacktestResult.fund_id)
    )).scalars().all())
    assert len(rows) == 3
    assert rows[0].ok and rows[1].ok
    assert not rows[2].ok and "模拟净值拉取失败" in rows[2].error


async def _noop_sleep(*a, **k):
    return None
