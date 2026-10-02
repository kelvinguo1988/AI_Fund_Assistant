"""Q7 命中口径整改回归（第二批 2A-2）

覆盖三件事：
1. 双口径判定与落库（hit_abs / hit_excess 并存，hit 列随 system_config 开关）
2. 固定 30 **净值日**评估窗口（含"窗口未走完则本轮跳过"，避免同一批样本反复拉净值）
3. 回填链路的上游预算：净值按基金去重、只与只之间随机 sleep、无到期样本时 0 请求
4. 基准侧与复盘同源含息（Q11）：股息率进 `benchmark_change_pct`，能翻掉超额判定

测试红线：全部 monkeypatch 净值/基准取数，不发真实请求。
"""

import sqlite3
import threading
from datetime import timedelta

import pytest

from backend.services import advice_learning_service as mod
from backend.utils.timezone import beijing_today


def _store(tmp_path):
    """独立连接的 AdviceLearningStore（object.__new__ 绕开单例，不碰真实库）

    Q7 的口径开关存在 system_config（生产由 SQLAlchemy 建表），测试库手工补齐。
    """
    store = object.__new__(mod.AdviceLearningStore)
    store._w = threading.Lock()
    store._conn = sqlite3.connect(str(tmp_path / "q7.db"), check_same_thread=False)
    store._conn.executescript(mod._SCHEMA)
    store._conn.execute(
        "CREATE TABLE IF NOT EXISTS system_config ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " config_key TEXT NOT NULL UNIQUE, config_value TEXT NOT NULL,"
        " description TEXT, updated_at TEXT)")
    store._conn.commit()
    return store


def _dates(count: int):
    """以今天为末点的 count 个连续日期（升序，仅作净值日序号用）"""
    return [(beijing_today() - timedelta(days=count - 1 - i)).isoformat()
            for i in range(count)]


def _dates_until(days_ago_last: int, count: int):
    """末点落在 `days_ago_last` 天前的 count 个日期：模拟净值停更（QDII 长假/断供）"""
    return [(beijing_today() - timedelta(days=days_ago_last + count - 1 - i)).isoformat()
            for i in range(count)]


def _nav_series(count: int, start: float, step: float):
    return [(d, round(start + i * step, 6)) for i, d in enumerate(_dates(count))]


def _nav_series_stopped(days_ago_last: int, count: int, start: float, step: float):
    return [(d, round(start + i * step, 6))
            for i, d in enumerate(_dates_until(days_ago_last, count))]


# ── 判定函数（纯函数，无 IO）──────────────────────────────────────────

class TestJudgeHit:
    @pytest.mark.parametrize("action,fund,bench,abs_hit,excess_hit", [
        ("sell", -3.5, 0.5, 1, 1),    # 跌且跑输：两口径都对
        ("sell", -3.5, -8.0, 1, 0),   # 跌得比基准更多：绝对命中，实际是卖错
        ("sell", 1.0, 5.0, 0, 1),     # 涨幅跑输基准：卖对了，但旧口径记不中
        ("buy", 1.0, 5.0, 1, 0),      # 上涨市里的绝对命中 = beta，不是选基能力
        ("buy", 1.0, -2.0, 1, 1),
        ("hold", 1.0, -2.0, 0, 0),    # hold 不参与命中统计
    ])
    def test_quadrants(self, action, fund, bench, abs_hit, excess_hit):
        assert mod.judge_hit(action, fund, bench, "abs") == abs_hit
        assert mod.judge_hit(action, fund, bench, "excess") == excess_hit

    def test_default_mode_is_excess(self):
        assert mod.DEFAULT_HIT_MODE == "excess"
        assert mod.judge_hit("buy", 1.0, 5.0) == 0


class TestEvalWindow:
    def test_fixed_thirty_nav_days(self):
        series = _nav_series(120, 1.0, 0.001)
        advice_date = series[10][0]
        win = mod.build_eval_window(series, advice_date)
        assert win["base_date"] == advice_date
        assert win["end_date"] == series[40][0]
        assert win["trading_days"] == mod.EVAL_HORIZON_TRADING_DAYS
        assert win["change_pct"] == pytest.approx(
            (series[40][1] / series[10][1] - 1) * 100)

    def test_base_is_last_nav_on_or_before_advice(self):
        """建议日当天净值尚未公布 → 基线取之前最后一个净值日"""
        series = _nav_series(60, 1.0, 0.01)
        gap_day = (beijing_today() - timedelta(days=30)).isoformat()
        win = mod.build_eval_window(series, gap_day)
        assert win["base_date"] == series[29][0]  # 序列里 <= 建议日的最后一点

    def test_window_not_finished_returns_none(self):
        series = _nav_series(60, 1.0, 0.01)
        assert mod.build_eval_window(series, series[50][0]) is None

    def test_advice_before_series_returns_none(self):
        series = _nav_series(60, 1.0, 0.01)
        too_old = (beijing_today() - timedelta(days=200)).isoformat()
        assert mod.build_eval_window(series, too_old) is None

    def test_zero_base_nav_returns_none(self):
        series = [(d, 0.0 if i < 5 else 1.0) for i, d in enumerate(_dates(60))]
        assert mod.build_eval_window(series, series[1][0]) is None

    def test_empty_series(self):
        assert mod.build_eval_window([], "2026-01-01") is None

    def test_window_change_pct_alignment(self):
        series = _nav_series(50, 2.0, 0.01)
        chg = mod.window_change_pct(series, series[10][0], series[20][0])
        assert chg == pytest.approx((series[20][1] / series[10][1] - 1) * 100)
        assert mod.window_change_pct(series, series[10][0], series[10][0]) is None
        assert mod.window_change_pct([], "2026-01-01", "2026-02-01") is None


# ── 双口径落库 + 开关 ─────────────────────────────────────────────────

class TestHitModeStorage:
    def test_mode_defaults_excess_without_config_table(self, tmp_path):
        """老库/测试库没有 system_config 行时不能报错，一律回落默认口径"""
        store = object.__new__(mod.AdviceLearningStore)
        store._w = threading.Lock()
        store._conn = sqlite3.connect(str(tmp_path / "bare.db"))
        store._conn.executescript(mod._SCHEMA)
        assert store.get_hit_mode() == "excess"

    def test_roundtrip_and_rollback(self, tmp_path):
        store = _store(tmp_path)
        assert store.get_hit_mode() == "excess"
        assert store.set_hit_mode("abs") == "abs"          # 回滚到旧口径
        assert store.get_hit_mode() == "abs"
        store.set_hit_mode("EXCESS ")                       # 大小写/空白容错
        assert store.get_hit_mode() == "excess"
        with pytest.raises(ValueError):
            store.set_hit_mode("beta")

    def test_dirty_value_falls_back(self, tmp_path):
        store = _store(tmp_path)
        store._conn.execute(
            "INSERT INTO system_config (config_key, config_value, updated_at) "
            "VALUES (?, 'json', 'x')", (mod.HIT_MODE_KEY,))
        store._conn.commit()
        assert store.get_hit_mode() == "excess"

    def test_record_outcome_writes_both_columns(self, tmp_path):
        store = _store(tmp_path)
        advice_id = store.log_advice("000001", "buy", "测试买入", 1.0)
        out = store.record_outcome(advice_id, "buy", 1.0, 5.0)
        assert out == {"hit": 0, "hit_abs": 1, "hit_excess": 0, "mode": "excess"}

        store.set_hit_mode("abs")
        out2 = store.record_outcome(advice_id, "buy", 1.0, 5.0)
        assert out2["hit"] == 1 and out2["hit_abs"] == 1 and out2["hit_excess"] == 0
        # hit 列跟着口径走，两列原始判定始终保留 → 切回 excess 无需重算历史

    def test_stats_reports_both_modes(self, tmp_path):
        store = _store(tmp_path)
        # 逐只不同代码：log_advice 同日同方向去重，重复灌同一只会只剩一条
        for i in range(4):
            aid = store.log_advice(f"b{i:06d}", "buy", "b")
            store.record_outcome(aid, "buy", 1.0, 5.0)   # abs 全中 / excess 全不中
        for i in range(2):
            aid = store.log_advice(f"s{i:06d}", "sell", "s")
            store.record_outcome(aid, "sell", -1.0, 5.0)  # 两口径都命中
        st = store.stats()
        assert st["evaluated"] == 6
        assert st["by_mode"]["abs"]["buy"] == {"total": 4, "hits": 4}
        assert st["by_mode"]["excess"]["buy"] == {"total": 4, "hits": 0}
        assert st["by_mode"]["abs"]["sell"]["hits"] == 2
        assert st["by_mode"]["excess"]["sell"]["hits"] == 2

    def test_legacy_null_rows_excluded_from_denominator(self, tmp_path):
        """改造前的历史行 abs/excess 为 NULL：计入 evaluated，但不该被当成"未命中"拉低命中率"""
        store = _store(tmp_path)
        ts = (beijing_today() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        cur = store._conn.execute(
            "INSERT INTO advice_log (ts, fund_code, action) VALUES (?, '000009', 'buy')",
            (ts,))
        store._conn.execute(
            "INSERT INTO advice_outcomes (advice_id, eval_date, fund_change_pct, "
            "benchmark_change_pct, hit) VALUES (?, ?, 1.0, 0.0, 1)",
            (cur.lastrowid, ts))
        store._conn.commit()
        st = store.stats()
        assert st["evaluated"] == 1
        assert st["buy_total"] == 1 and st["buy_hits"] == 1     # 旧 hit 列仍可读
        assert st["by_mode"]["abs"]["buy"] == {"total": 0, "hits": 0}
        assert st["by_mode"]["excess"]["buy"] == {"total": 0, "hits": 0}

    def test_calibrate_follows_active_mode(self, tmp_path):
        """同一批样本：abs 口径下 buy 全中（阈值向默认回退），excess 口径下全不中（收紧止盈线）"""
        store = _store(tmp_path)
        ts = (beijing_today() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        for i in range(mod.CALIBRATION_MIN_SAMPLES):
            cur = store._conn.execute(
                "INSERT INTO advice_log (ts, fund_code, action) VALUES (?, ?, 'buy')",
                (ts, f"b{i}"))
            store._conn.execute(
                "INSERT INTO advice_outcomes (advice_id, eval_date, fund_change_pct, "
                "benchmark_change_pct, hit, hit_abs, hit_excess) VALUES (?, ?, 1.0, 5.0, ?, 1, 0)",
                (cur.lastrowid, ts, 0))
        store._conn.commit()

        store.set_hit_mode("excess")
        out = store.calibrate()
        assert out["hit_mode"] == "excess"
        changes = {c["key"]: c for c in out["changes"]}
        assert changes["profit_take_pct"]["to"] < mod.DEFAULT_PARAMS["profit_take_pct"]

        store.set_hit_mode("abs")
        store._conn.execute("DELETE FROM advice_calibration")
        store._conn.commit()
        out_abs = store.calibrate()
        assert out_abs["hit_mode"] == "abs"
        # abs 口径命中率 100% → 只做向默认值回退，不会动止盈下限
        assert out_abs["adjusted"] is False


# ── 工单入账去重 ───────────────────────────────────────────────────────

class TestLogAdviceDedupe:
    def test_same_day_same_action_once(self, tmp_path):
        store = _store(tmp_path)
        assert store.log_advice("000001", "sell", "第一次") is not None
        assert store.log_advice("000001", "sell", "同日重算") is None
        assert store.log_advice("000001", "buy", "反向") is not None
        assert store.log_advice("000002", "sell", "另一只") is not None

    def test_dedupe_can_be_disabled(self, tmp_path):
        store = _store(tmp_path)
        store.log_advice("000001", "sell", "a")
        assert store.log_advice("000001", "sell", "b", dedupe_same_day=False) is not None


# ── 回填链路：上游预算 + 窗口口径 ──────────────────────────────────────

class _Bench:
    """基准数据源 stub：记录被取了几次，可指定抛错"""

    def __init__(self, series, raises: bool = False):
        self._series = list(series)
        self._raises = raises
        self.bench_calls = 0

    async def bench(self):
        self.bench_calls += 1
        if self._raises:
            raise RuntimeError("数据源不可用")
        return self._series


@pytest.fixture
def no_sleep(monkeypatch):
    """记录 sleep 而不真等：防封间隔的**次数与位置**就是被测不变量"""
    import asyncio
    calls: list[float] = []

    async def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return calls


@pytest.fixture
def patched_sources(monkeypatch):
    """stub 净值/基准取数：返回 {"calls": 净值调用记录, "policy": 口径, "install": 安装函数}

    install(adapter) 里的 adapter 只提供基准数据与基准调用计数 —— 被测代码自己
    `AKShareAdapter()` 构造实例，所以这里替换的是类本身，不传实例。

    口径（Q11）默认股息 0：本文件的用例验证的是 Q7 的窗口/预算逻辑，基准数字要
    与价格序列严格一致；含息基准另在 tests/test_return_caliber.py 覆盖。
    """
    nav_calls: list[tuple[str, int, str]] = []
    policy = {"nav_adjusted": True, "bench_div_yield_pct": 0.0}

    def install(source):
        class _StubAdapter:
            def __init__(self):
                pass

            async def get_benchmark_series(self, symbol="sh000300", period=600):
                return list(await source.bench())

        monkeypatch.setattr(
            "backend.data_sources.akshare_adapter.AKShareAdapter", _StubAdapter)

        async def _policy(_db=None):
            return dict(policy)

        monkeypatch.setattr(
            "backend.services.caliber_service.load_caliber", _policy)

        async def fake_fetch(_adapter, code, days, start_date=None, *, adjusted=True):
            nav_calls.append((code, int(days), start_date or ""))
            if code == "111111":
                return _nav_series(120, 1.0, 0.01)          # 一路上行
            if code == "222222":
                return _nav_series(120, 3.0, -0.005)        # 一路下跌
            if code == "333333":
                # 净值止于 25 天前：51 天前那条建议的 30 净值日窗口走不完
                return _nav_series_stopped(25, 60, 1.0, 0.001)
            return []

        monkeypatch.setattr(
            "backend.services.review_service._fetch_nav_series", fake_fetch)

    return {"calls": nav_calls, "policy": policy, "install": install}


def _seed_pending(store, code, action, advice_date):
    ts = f"{advice_date} 09:30:00"
    cur = store._conn.execute(
        "INSERT INTO advice_log (ts, fund_code, action) VALUES (?, ?, ?)",
        (ts, code, action))
    store._conn.commit()
    return cur.lastrowid


class TestRunAdviceBackfill:
    async def test_no_pending_means_zero_requests(self, tmp_path, patched_sources, no_sleep):
        store = _store(tmp_path)
        source = _Bench([])
        patched_sources["install"](source)
        out = await mod.run_advice_backfill(store)
        assert out["evaluated"] == 0 and out["pending"] == 0
        assert patched_sources["calls"] == []          # 净值 0 次
        assert source.bench_calls == 0                 # 基准 0 次
        assert no_sleep == []

    async def test_nav_fetched_once_per_fund(self, tmp_path, patched_sources, no_sleep):
        store = _store(tmp_path)
        dates = _dates(120)
        # 同一基金两条到期建议（相隔 5 个净值日）+ 另一只基金一条
        _seed_pending(store, "111111", "buy", dates[10])
        _seed_pending(store, "111111", "sell", dates[15])
        _seed_pending(store, "222222", "sell", dates[20])
        patched_sources["install"](_Bench(_nav_series(120, 4000.0, 2.0)))

        out = await mod.run_advice_backfill(store)
        assert out["pending"] == 3 and out["evaluated"] == 3
        assert [c[0] for c in patched_sources["calls"]] == ["111111", "222222"]
        assert out["funds_fetched"] == 2
        # 防封间隔只在基金之间，首只之前不 sleep
        assert len(no_sleep) == 1
        assert mod.BACKFILL_SLEEP_RANGE[0] <= no_sleep[0] <= mod.BACKFILL_SLEEP_RANGE[1]

    async def test_earliest_advice_date_drives_fetch(self, tmp_path, patched_sources, no_sleep):
        store = _store(tmp_path)
        dates = _dates(120)
        _seed_pending(store, "111111", "buy", dates[30])
        _seed_pending(store, "111111", "sell", dates[10])
        patched_sources["install"](_Bench(_nav_series(120, 4000.0, 2.0)))
        await mod.run_advice_backfill(store)
        assert patched_sources["calls"][0][2] == dates[10]

    async def test_immature_sample_skipped_and_not_recorded(
            self, tmp_path, patched_sources, no_sleep):
        """到期但窗口未走完（净值停更）：本轮跳过、不落库，下周再取一次"""
        store = _store(tmp_path)
        dates = _dates(120)
        mature = _seed_pending(store, "111111", "buy", dates[10])
        # 333333 的净值序列止于 25 天前 → 51 天前那条建议的 30 净值日窗口走不完
        immature_ts = (beijing_today() - timedelta(days=51)).isoformat()
        immature = _seed_pending(store, "333333", "buy", immature_ts)
        patched_sources["install"](_Bench(_nav_series(120, 4000.0, 2.0)))

        out = await mod.run_advice_backfill(store)
        assert out["evaluated"] == 1 and out["skipped_window_incomplete"] == 1
        rows = {r[0] for r in store._conn.execute(
            "SELECT advice_id FROM advice_outcomes")}
        assert rows == {mature} and immature not in rows

    async def test_excess_vs_abs_diverge_on_real_series(self, tmp_path, patched_sources, no_sleep):
        """上涨行情 + 基准涨得更快：buy 的绝对命中为真、超额命中为假"""
        store = _store(tmp_path)
        dates = _dates(120)
        aid = _seed_pending(store, "111111", "buy", dates[10])
        # 基金同区间 +27%，基准 +100% → 绝对口径记"选中了上涨的基金"，超额口径判为跑输
        bench = _nav_series(120, 4000.0, 200.0)
        patched_sources["install"](_Bench(bench))

        out = await mod.run_advice_backfill(store)
        assert out["evaluated"] == 1
        row = store._conn.execute(
            "SELECT hit, hit_abs, hit_excess, eval_date FROM advice_outcomes "
            "WHERE advice_id = ?", (aid,)).fetchone()
        assert row[1] == 1 and row[2] == 0
        assert row[0] == 0                       # 默认口径 = excess
        assert row[3] == dates[40]               # eval_date = 窗口结束日，不是补跑日

    async def test_benchmark_side_uses_dividend_carry(
            self, tmp_path, patched_sources, no_sleep):
        """Q11：回填的基准侧与复盘同源含息 —— 平价价格指数也能因股息翻掉超额判定

        基金区间 +27%。股息率 0 时基准 0%（超额命中）；把 caliber 调到 300%/年，
        基准 30 个交易日约 +42.6%，同一条 buy 建议判为跑输基准。
        """
        store = _store(tmp_path)
        dates = _dates(120)
        flat = [(d, 4000.0) for d in dates]
        patched_sources["install"](_Bench(flat))

        patched_sources["policy"]["bench_div_yield_pct"] = 0.0
        first = _seed_pending(store, "111111", "buy", dates[10])
        await mod.run_advice_backfill(store)

        patched_sources["policy"]["bench_div_yield_pct"] = 300.0
        second = _seed_pending(store, "111111", "buy", dates[10])
        await mod.run_advice_backfill(store)

        rows = dict((r[0], r[1:]) for r in store._conn.execute(
            "SELECT advice_id, benchmark_change_pct, hit_abs, hit_excess "
            "FROM advice_outcomes"))
        assert rows[first][0] == 0.0 and rows[first][2] == 1
        assert rows[second][0] == pytest.approx(42.6, abs=1.0)
        assert rows[second][1] == 1 and rows[second][2] == 0

    async def test_missing_benchmark_counts_and_degrades(self, tmp_path, patched_sources, no_sleep):
        store = _store(tmp_path)
        dates = _dates(120)
        aid = _seed_pending(store, "111111", "buy", dates[10])
        patched_sources["install"](_Bench([]))   # 基准取不到

        out = await mod.run_advice_backfill(store)
        assert out["benchmark_missing"] == 1 and out["evaluated"] == 1
        row = store._conn.execute(
            "SELECT benchmark_change_pct, hit_excess FROM advice_outcomes WHERE advice_id = ?",
            (aid,)).fetchone()
        assert row[0] == 0.0 and row[1] == 1     # 基准按 0 处理 → 退化为绝对口径

    async def test_benchmark_failure_is_not_fatal(self, tmp_path, patched_sources, no_sleep):
        store = _store(tmp_path)
        dates = _dates(120)
        _seed_pending(store, "111111", "buy", dates[10])
        patched_sources["install"](_Bench([], raises=True))
        out = await mod.run_advice_backfill(store)
        assert out["evaluated"] == 1 and out["benchmark_missing"] == 1

    async def test_nav_fetch_failure_counts_immature_not_crash(
            self, tmp_path, patched_sources, no_sleep):
        """取不到净值（代码错误/数据源断供）：按窗口未走完处理，下轮再来，不抛异常"""
        store = _store(tmp_path)
        dates = _dates(120)
        _seed_pending(store, "999999", "buy", dates[10])   # fake_fetch 对未知代码返回 []
        patched_sources["install"](_Bench(_nav_series(120, 4000.0, 2.0)))
        out = await mod.run_advice_backfill(store)
        assert out["evaluated"] == 0 and out["skipped_window_incomplete"] == 1
        assert out["pending"] == 1


# ── 工单入账（闭环的输入端）────────────────────────────────────────────

class TestWorklistLogging:
    def test_only_sell_buy_logged_and_deduped(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from backend.ai.rebalance import RebalanceService
        store = _store(tmp_path)
        monkeypatch.setattr(mod, "AdviceLearningStore", lambda: store)
        report = SimpleNamespace(
            sells=[{"code": "000001", "reasons": ["评分低", "跌破止损"], "score": -3.2}],
            buys=[{"code": "000002", "reasons": [], "score": 4.1, "reason": "高分且可申购"}],
            watch=[{"code": "000003"}], buy_blocked=[{"code": "000004"}],
        )
        RebalanceService._record_advice_worklist(report)
        rows = store._conn.execute(
            "SELECT fund_code, action, reasons, score FROM advice_log").fetchall()
        assert [(r[0], r[1]) for r in rows] == [("000001", "sell"), ("000002", "buy")]
        assert "评分低" in rows[0][2] and "高分且可申购" in rows[1][2]

        RebalanceService._record_advice_worklist(report)   # 同日重跑同一个任务
        assert store._conn.execute("SELECT COUNT(*) FROM advice_log").fetchone()[0] == 2

    def test_store_failure_does_not_break_worklist(self, monkeypatch):
        """账本写不进去（库锁等）不能连带吃掉给用户看的工单"""
        from types import SimpleNamespace
        from backend.ai.rebalance import RebalanceService

        class _Boom:
            def log_advice(self, *a, **k):
                raise RuntimeError("database is locked")

        monkeypatch.setattr(mod, "AdviceLearningStore", lambda: _Boom())
        RebalanceService._record_advice_worklist(
            SimpleNamespace(sells=[{"code": "1"}], buys=[], watch=[], buy_blocked=[]))
