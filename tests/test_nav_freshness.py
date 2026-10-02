"""Q9 净值新鲜度门槛（陈旧净值不得拿到今天的信号）

背景：`_no_nav_reason` 只判"空序列"，全仓没有任何地方把 `fund_data.date`（净值
as-of 日）与今天比较。上游静默回旧缓存、或基金暂停披露时，会以 `analysis_date=今天`
写入一条陈旧信号，回测再把这段陈旧尾巴当真实交易日重放。

口径（2026-10-02 拍板）：落后 ≤5 个交易日只标注，>10 个交易日跳过评分并埋
`analysis.data_missing`；QDII/跨境/96 开头互认基金 T+2 才披露是常态，两档各 +5 放宽；
缺口按**交易日**计且优先用库内 holiday_calendar，不为此新增上游请求。
回滚键 `nav_staleness_max_trading_days=0`（整个门槛关闭）。
"""

import json
from datetime import date

import pytest
from sqlalchemy import select

from backend.data_sources.base import FundData
from backend.data_sources.trading_calendar import (
    count_missing_trading_days,
    load_off_day_dates,
)
from backend.engines.factor_engine import FactorScoreResult
from backend.engines.quality_filter import QUALITY_CONFIG, QualityFilter, eval_nav_staleness
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.models.holiday_calendar import HolidayCalendar
import backend.services.analysis_service as asis

TODAY = date(2026, 10, 2)          # 周五；10-01（周四）是国庆休市日
SPRING_FESTIVAL_OFF = {            # 2026 春节：02-15(日)~02-23(一) 休市
    date(2026, 2, 15), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 21), date(2026, 2, 22),
    date(2026, 2, 23),
}


def _fund_data(code: str, nav_date: str) -> FundData:
    hist = [1.0 + i * 0.01 for i in range(40)]
    return FundData(
        code=code, name=f"测试基金{code}", date=nav_date,
        close=hist[-1], close_history=hist,
    )


def _mk_cfg(off_days=frozenset(), **qf_overrides) -> asis._AnalysisConfig:
    cfg_dict = dict(QUALITY_CONFIG)
    cfg_dict.update(qf_overrides)
    factor = {"code": "momentum", "name": "动量", "weight": 1.0,
              "direction": "positive", "params": "{}", "signal_rules": []}
    return asis._AnalysisConfig(
        active_factors=[factor], thresholds_json="", qf=QualityFilter(config=cfg_dict),
        regime_snapshot=None, regime_factors=[factor], off_days=off_days,
    )


async def _mk_fund(db, code: str = "004011", **kw) -> Fund:
    fund = Fund(code=code, name=kw.pop("name", f"基金{code}"), fund_type="otc",
                status="active", **kw)
    db.add(fund)
    await db.flush()
    return fund


def _one_factor_score() -> list[FactorScoreResult]:
    return [FactorScoreResult(
        factor_code="momentum", factor_name="动量", raw_value=0.5, score=0.5,
        direction="positive",
    )]


# ═══════════════════════════════════════════════════════════════════════
# 缺口计数：按交易日、两端都不计、周末与节假日剔除
# ═══════════════════════════════════════════════════════════════════════

class TestCountMissingTradingDays:
    def test_latest_trading_day_is_zero(self):
        """净值 = 上一交易日 → 缺口 0（今天当天不发，不该算滞后）"""
        # 09-25 周五 → 09-28 周一：中间无开市日
        assert count_missing_trading_days(date(2026, 9, 25), date(2026, 9, 28)) == 0

    def test_weekend_and_holiday_excluded(self):
        """09-25(五) → 10-05(一)，仅 10-01 休市：09-28/29/30 + 10-02 = 4 个缺口"""
        assert count_missing_trading_days(date(2026, 9, 25), date(2026, 10, 5),
                                          frozenset({date(2026, 10, 1)})) == 4

    def test_same_day_and_future_are_zero(self):
        assert count_missing_trading_days(TODAY, TODAY) == 0
        assert count_missing_trading_days(date(2026, 10, 9), TODAY) == 0

    def test_long_holiday_not_counted_as_staleness(self):
        """春节连休 9 天：真实缺口只有节后一天，长假本身不该把基金打死"""
        assert count_missing_trading_days(
            date(2026, 2, 13), date(2026, 2, 25), SPRING_FESTIVAL_OFF
        ) == 1

    def test_makeup_workday_saturday_still_closed(self):
        """调休补班的周六（holiday_calendar 里 is_off_day=False）股市不开市

        02-12(四) → 02-17(二)：中间只有 02-13(五) 开市；02-14 是补班周六，
        按"周一至五"的朴素口径会被多算一天。
        """
        assert count_missing_trading_days(
            date(2026, 2, 12), date(2026, 2, 17), SPRING_FESTIVAL_OFF
        ) == 1


class TestLoadOffDayDates:
    @pytest.mark.asyncio
    async def test_reads_only_off_days_in_window(self, db_session):
        for d, off, name in [
            ("2026-09-30", False, "调休补班"),
            ("2026-10-01", True, "国庆节"),
            ("2026-10-02", True, "国庆节"),
            ("2026-12-25", True, "窗口外"),
        ]:
            db_session.add(HolidayCalendar(holiday_date=d, is_off_day=off, holiday_name=name))
        await db_session.commit()

        off_days = await load_off_day_dates(db_session, date(2026, 9, 1), date(2026, 10, 31))
        assert off_days == frozenset({date(2026, 10, 1), date(2026, 10, 2)})

    @pytest.mark.asyncio
    async def test_empty_table_returns_empty_set(self, db_session):
        assert await load_off_day_dates(db_session, date(2026, 9, 1), TODAY) == frozenset()


# ═══════════════════════════════════════════════════════════════════════
# 判档：>5 标注 / >10 否决 / QDII 放宽 / 0 关闭
# ═══════════════════════════════════════════════════════════════════════

class TestEvalNavStaleness:
    def test_within_warn_threshold_ok(self):
        assert eval_nav_staleness(5, QUALITY_CONFIG)[0] == "ok"

    def test_beyond_warn_threshold_annotates(self):
        level, msg = eval_nav_staleness(6, QUALITY_CONFIG, nav_date="2026-09-23")
        assert level == "warn"
        assert "2026-09-23" in msg and "6" in msg

    def test_beyond_max_threshold_vetoes(self):
        level, msg = eval_nav_staleness(11, QUALITY_CONFIG, nav_date="2026-09-16")
        assert level == "veto"
        assert "落后 11 个交易日" in msg

    def test_unknown_date_not_treated_as_stale(self):
        """missing_days=None（净值无日期/解析失败）→ 不判，避免格式一变就整池否决"""
        assert eval_nav_staleness(None, QUALITY_CONFIG) == ("off", "")

    def test_rollback_key_closes_the_gate(self):
        cfg = dict(QUALITY_CONFIG, nav_staleness_max_trading_days=0)
        assert eval_nav_staleness(999, cfg) == ("off", "")

    def test_qdii_gets_extra_tolerance(self):
        cfg = dict(QUALITY_CONFIG, nav_staleness_slow_disclosure_extra_days=5)
        # 普通基金 12 日已否决，QDII 放宽 5 日后只是标注
        assert eval_nav_staleness(12, cfg, slow_disclosure=False)[0] == "veto"
        assert eval_nav_staleness(12, cfg, slow_disclosure=True)[0] == "warn"
        assert eval_nav_staleness(6, cfg, slow_disclosure=True)[0] == "ok"

    def test_extra_zero_keeps_strict(self):
        cfg = dict(QUALITY_CONFIG, nav_staleness_slow_disclosure_extra_days=0)
        assert eval_nav_staleness(12, cfg, slow_disclosure=True)[0] == "veto"


# ═══════════════════════════════════════════════════════════════════════
# 服务接线：慢披露识别 + 陈旧跳过评分/标注落库
# ═══════════════════════════════════════════════════════════════════════

class TestSlowDisclosureRecognition:
    @pytest.mark.asyncio
    async def test_qdii_by_name(self, db_session):
        fund = await _mk_fund(db_session, "110011", name="易方达中概互联QDII")
        assert asis._is_slow_nav_disclosure(fund) is True

    @pytest.mark.asyncio
    async def test_cross_border_code(self, db_session):
        assert asis._is_slow_nav_disclosure(await _mk_fund(db_session, "968049")) is True

    @pytest.mark.asyncio
    async def test_plain_domestic_not_slow(self, db_session):
        assert asis._is_slow_nav_disclosure(await _mk_fund(db_session, "004011")) is False

    @pytest.mark.asyncio
    async def test_qdii_in_tags_counts(self, db_session):
        fund = await _mk_fund(db_session, "006335", tags="QDII,科技")
        assert asis._is_slow_nav_disclosure(fund) is True


class TestServiceGate:
    @pytest.mark.asyncio
    async def test_fresh_nav_passes(self, db_session, monkeypatch):
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)
        svc = asis.AnalysisService(db_session)
        fund = await _mk_fund(db_session)
        # 09-30（周三）→ 10-02：中间开市日只有 10-01，而它休市
        cfg = _mk_cfg(off_days=frozenset({date(2026, 10, 1)}))
        as_of, level, _ = svc._nav_freshness(fund, _fund_data("004011", "2026-09-30"), cfg)
        assert (as_of, level) == ("2026-09-30", "ok")

    @pytest.mark.asyncio
    async def test_stale_nav_vetoes_and_skips_scoring(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)
        svc = asis.AnalysisService(db_session)
        fund = await _mk_fund(db_session)
        cfg = _mk_cfg()

        out = await svc._score_and_store(
            fund, cfg, _one_factor_score(), _fund_data("004011", "2026-09-15"), []
        )
        await db_session.commit()
        await _drain_log_tasks()

        assert out is None
        assert (await db_session.execute(select(AnalysisResult))).scalars().all() == []
        rows = [r for r in _read_error_logs(tmp_path) if r[0] == "analysis.data_missing"]
        assert len(rows) == 1
        assert "落后" in rows[0][3] and "004011" in rows[0][3]

    @pytest.mark.asyncio
    async def test_moderately_stale_scores_with_warning(self, db_session, monkeypatch):
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)
        svc = asis.AnalysisService(db_session)
        fund = await _mk_fund(db_session)
        cfg = _mk_cfg()

        # 09-22 → 10-02：落后 7 个交易日（>5 标注，≤10 不否决）
        out = await svc._score_and_store(
            fund, cfg, _one_factor_score(), _fund_data("004011", "2026-09-22"), []
        )
        await db_session.commit()
        assert out is not None

        row = (await db_session.execute(select(AnalysisResult))).scalars().one()
        assert row.nav_as_of_date == "2026-09-22"
        warnings = json.loads(row.quality_warnings)
        assert any("净值新鲜度" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_qdii_survives_the_veto_threshold(self, db_session, monkeypatch):
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)
        svc = asis.AnalysisService(db_session)
        fund = await _mk_fund(db_session, "110011", name="某中概QDII")
        cfg = _mk_cfg()

        out = await svc._score_and_store(
            fund, cfg, _one_factor_score(), _fund_data("110011", "2026-09-15"), []
        )
        await db_session.commit()
        assert out is not None   # 普通基金在 12 日已被否决，QDII 放宽到 15

        row = (await db_session.execute(select(AnalysisResult))).scalars().one()
        assert "净值新鲜度" in (row.quality_warnings or "")

    @pytest.mark.asyncio
    async def test_missing_nav_date_scores_without_annotation(self, db_session, monkeypatch):
        """无 as-of 日期时不判：防线缺失优先于误杀整池"""
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)
        svc = asis.AnalysisService(db_session)
        fund = await _mk_fund(db_session)
        cfg = _mk_cfg()

        fd = _fund_data("004011", "")
        out = await svc._score_and_store(fund, cfg, _one_factor_score(), fd, [])
        await db_session.commit()
        assert out is not None
        row = (await db_session.execute(select(AnalysisResult))).scalars().one()
        assert row.nav_as_of_date is None
        assert row.quality_warnings is None

    @pytest.mark.asyncio
    async def test_gate_off_stores_nav_as_of_only(self, db_session, monkeypatch):
        """回滚键置 0：既不否决也不标注，但 as-of 日期照常落库（诊断不受回滚影响）"""
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)
        svc = asis.AnalysisService(db_session)
        fund = await _mk_fund(db_session)
        cfg = _mk_cfg(nav_staleness_max_trading_days=0)

        out = await svc._score_and_store(
            fund, cfg, _one_factor_score(), _fund_data("004011", "2025-01-06"), []
        )
        await db_session.commit()
        assert out is not None
        row = (await db_session.execute(select(AnalysisResult))).scalars().one()
        assert row.nav_as_of_date == "2025-01-06"
        assert row.quality_warnings is None

    @pytest.mark.asyncio
    async def test_off_days_loaded_only_when_gate_enabled(self, db_session, monkeypatch):
        """门槛关闭时不查节假日表（省一次 DB 读，也保证回滚后行为面最小）"""
        monkeypatch.setattr(asis, "beijing_today", lambda: TODAY)
        svc = asis.AnalysisService(db_session)
        called: list = []

        async def _spy(session, start, end):
            called.append((start, end))
            return frozenset()
        monkeypatch.setattr(asis, "load_off_day_dates", _spy)

        assert await svc._load_off_days({"nav_staleness_max_trading_days": 0}) == frozenset()
        assert called == []

        await svc._load_off_days({"nav_staleness_max_trading_days": 10})
        assert called == [(date(2026, 7, 4), TODAY)]


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


async def _drain_log_tasks():
    """log_source_failure 在事件循环里派发了后台写库任务，等它落地"""
    import asyncio
    from backend.services.error_log_service import _BG_LOG_TASKS
    for _ in range(50):
        if not _BG_LOG_TASKS:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)
