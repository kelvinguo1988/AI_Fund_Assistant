"""① 无净值基金跳过评分 + data_missing 埋点

背景：数据源在 pingzhongdata 与 f10/lsjz 两套策略都拿不到净值时不抛异常，
而是回一个空壳 FundData（池中已失效的代码，如 968049）。此前这些基金照常
进因子计算：11 个因子对空序列一致返回 0.0 → 既作为离群样本参与截面标准化，
又给自己留下永远不会变的"观望"行。
"""

import json

import pytest
from sqlalchemy import select

from backend.data_sources.base import FundData
from backend.engines.factor_engine import FactorScoreResult
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
import backend.services.analysis_service as asis


def _nav_fund_data(code: str) -> FundData:
    hist = [1.0 + i * 0.01 for i in range(40)]
    return FundData(code=code, name=f"净值基金{code}", date="2026-09-25",
                    close=hist[-1], close_history=hist)


def _empty_fund_data(code: str) -> FundData:
    """数据源兜底返回的空壳：无净值、无收盘、无序列"""
    return FundData(code=code, name=f"无净值基金{code}")


class _StubSource:
    def __init__(self, mapping: dict):
        self._mapping = mapping
        self.requested: list[str] = []

    async def get_fund_data(self, code, fund_type=None):
        self.requested.append(code)
        return self._mapping[code]


class _FakeEngine:
    """记录进入因子计算与截面标准化的基金，替代真实 numpy 计算"""

    def __init__(self):
        self.calculated: list[str] = []
        self.normalized: list[str] = []

    def calculate_all(self, fund_data, factors):
        self.calculated.append(fund_data.code)
        return [FactorScoreResult(
            factor_code="momentum", factor_name="动量",
            raw_value=0.5, score=0.5, direction="positive",
        )]

    def normalize_cross_sectional(self, all_results, factors):
        self.normalized = sorted(all_results.keys())
        return all_results


async def _mk_funds(db, codes: list[str]):
    funds = []
    for c in codes:
        f = Fund(code=c, name=f"基金{c}", fund_type="otc", status="active")
        db.add(f)
        funds.append(f)
    await db.flush()
    return funds


def _stub_cfg(monkeypatch):
    cfg = asis._AnalysisConfig(
        active_factors=[{"code": "momentum", "name": "动量", "weight": 1.0,
                         "direction": "positive", "params": "{}", "signal_rules": []}],
        thresholds_json="",
        qf=None,
        regime_snapshot=None,
        regime_factors=[{"code": "momentum", "name": "动量", "weight": 1.0,
                         "direction": "positive", "params": "{}", "signal_rules": []}],
    )

    async def _fake(self):
        return cfg
    monkeypatch.setattr(asis.AnalysisService, "_load_analysis_config", _fake)
    return cfg


def _read_error_logs(tmp_path):
    import sqlite3
    db_file = next(tmp_path.glob("error_logs_test.db"), None)
    if db_file is None:
        return []
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute("select module, category, severity, message from error_logs").fetchall()
    finally:
        conn.close()


class TestNoNavReason:
    def test_empty_fund_data_detected(self):
        assert asis._no_nav_reason(_empty_fund_data("968049")) == "无净值序列"

    def test_nav_series_ok(self):
        assert asis._no_nav_reason(_nav_fund_data("004011")) is None

    def test_single_latest_nav_still_scores(self):
        """只有最新净值、无历史序列时部分因子仍可算，不当作断供"""
        assert asis._no_nav_reason(FundData(code="1", close=1.234)) is None


class TestBatchPath:
    @pytest.mark.asyncio
    async def test_no_nav_fund_skipped_before_scoring(self, db_session, monkeypatch, tmp_path):
        funds = await _mk_funds(db_session, ["004011", "968049"])
        _stub_cfg(monkeypatch)
        engine = _FakeEngine()
        monkeypatch.setattr(asis, "factor_engine", engine)

        svc = asis.AnalysisService(db_session)
        svc.data_source = _StubSource({
            "004011": _nav_fund_data("004011"),
            "968049": _empty_fund_data("968049"),
        })
        scored: list[str] = []

        async def _fake_score(self, fund, cfg, **kwargs):
            scored.append(fund.code)
            return None
        monkeypatch.setattr(asis.AnalysisService, "_score_and_store", _fake_score)

        await svc.run_analysis()

        assert engine.calculated == ["004011"]
        assert engine.normalized == ["004011"]
        assert scored == ["004011"]
        assert (await db_session.execute(select(AnalysisResult))).scalars().all() == []

    @pytest.mark.asyncio
    async def test_data_missing_logged(self, db_session, monkeypatch, tmp_path):
        await _mk_funds(db_session, ["968049"])
        _stub_cfg(monkeypatch)
        monkeypatch.setattr(asis, "factor_engine", _FakeEngine())

        svc = asis.AnalysisService(db_session)
        svc.data_source = _StubSource({"968049": _empty_fund_data("968049")})

        async def _fake_score(self, fund, cfg, **kwargs):
            return None
        monkeypatch.setattr(asis.AnalysisService, "_score_and_store", _fake_score)

        await svc.run_analysis()
        await _drain_log_tasks()

        rows = [r for r in _read_error_logs(tmp_path) if r[0] == "analysis.data_missing"]
        assert len(rows) == 1
        assert rows[0][1] == "data"
        assert rows[0][2] == "warning"
        assert "968049" in rows[0][3]


class TestStreamingPath:
    @pytest.mark.asyncio
    async def test_no_nav_code_reported_as_failed(self, db_session, monkeypatch):
        await _mk_funds(db_session, ["004011", "968049"])
        _stub_cfg(monkeypatch)
        engine = _FakeEngine()
        monkeypatch.setattr(asis, "factor_engine", engine)

        svc = asis.AnalysisService(db_session)
        svc.data_source = _StubSource({
            "004011": _nav_fund_data("004011"),
            "968049": _empty_fund_data("968049"),
        })

        async def _fake_score(self, fund, cfg, **kwargs):
            return None
        monkeypatch.setattr(asis.AnalysisService, "_score_and_store", _fake_score)

        events = [json.loads(e.replace("data: ", "").strip())
                  for e in await _collect(svc.run_analysis_streaming()) if e.startswith("data:")]
        complete = [e for e in events if e["type"] == "complete"][0]

        assert engine.calculated == ["004011"]
        assert complete["failed"] == ["968049"]


async def _collect(agen) -> list[str]:
    out = []
    async for chunk in agen:
        out.append(chunk)
    return out


async def _drain_log_tasks():
    """log_source_failure 在事件循环里派发了后台写库任务，等它落地"""
    import asyncio
    for _ in range(50):
        if not asis_log_tasks_pending():
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)


def asis_log_tasks_pending() -> bool:
    from backend.services.error_log_service import _BG_LOG_TASKS
    return len(_BG_LOG_TASKS) > 0
