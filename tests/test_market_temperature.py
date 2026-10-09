"""中证800 市场温度计 —— 口径与缓存测试（全程零上游请求）

温度计的全部风险都在口径上：分位窗口选错、用了未来信息、样本不足硬算，
都会让"52.5°"这种数字看起来很权威但毫无依据，所以这些用例锁的就是这几件事。
纯函数直接喂构造序列；缓存/冷却用例 monkeypatch run_with_timeout（与
test_cache_merge_and_negative_cache 同法），不会打乐咕。
"""

import os
import time

import pandas as pd
import pytest

from backend.services.market_temperature_service import (
    FORWARD_MONTHS,
    MIN_WINDOW_MONTHS,
    ROLLING_WINDOW_MONTHS,
    MarketTemperatureService,
    _align_series,
    _build_payload,
    forward_return_by_zone,
    format_temperature_line,
    monthly_temperatures,
    percentile_in_window,
    rebalance_hint,
    zone_of,
)


def _frames(rows: list[tuple[str, float, float, float]]):
    """rows = (日期, 点位, PE, PB) → 两张与乐咕同列名的表"""
    pe_df = pd.DataFrame(
        [{"日期": d, "指数": c, "滚动市盈率": pe} for d, c, pe, _ in rows]
    )
    pb_df = pd.DataFrame([{"日期": d, "市净率": pb} for d, _, _, pb in rows])
    return pe_df, pb_df


def _series(n: int = MIN_WINDOW_MONTHS + FORWARD_MONTHS + 6):
    """线性上行序列：分位可手算，便于断言"""
    rows = []
    for i in range(n):
        d = f"20{i // 12:02d}-{i % 12 + 1:02d}-01"
        rows.append((d, 1000.0 + i * 10, 10.0 + i * 0.1, 1.0 + i * 0.01))
    return rows


@pytest.fixture
def clean_service():
    saved = (
        MarketTemperatureService._cache, MarketTemperatureService._ts,
        MarketTemperatureService._fail_until,
    )
    MarketTemperatureService.reset_cache()
    yield
    (
        MarketTemperatureService._cache, MarketTemperatureService._ts,
        MarketTemperatureService._fail_until,
    ) = saved


class TestPercentileCaliber:
    def test_none_before_warmup(self):
        series = [1.0] * (MIN_WINDOW_MONTHS - 1)
        assert percentile_in_window(series, len(series) - 1, ROLLING_WINDOW_MONTHS) is None
        # 刚好够预热即可计算，且含当前值自身（<= 口径）
        assert percentile_in_window(series + [1.0], MIN_WINDOW_MONTHS - 1, None) == pytest.approx(100.0)

    def test_uses_only_history_up_to_i(self):
        """未来出现极端值不得改变历史月份的分位 —— 前视是这类回放最常见的错"""
        pe = [10.0 + (i % 5) for i in range(MIN_WINDOW_MONTHS)]
        before = percentile_in_window(pe, MIN_WINDOW_MONTHS - 1, None)
        pe_with_future = pe + [9999.0] * 20
        after = percentile_in_window(pe_with_future, MIN_WINDOW_MONTHS - 1, None)
        assert before == pytest.approx(after)

    def test_rolling_window_excludes_old_extremes(self):
        """同一份数据，滚动窗口与扩展窗口必须给出不同分位（窗口是主变量不是细节）"""
        n = ROLLING_WINDOW_MONTHS + 12
        pe = [1.0] * n
        pe[0] = 100.0          # 早年一次孤立高点
        pe[-1] = 2.0           # 当前值，低于那个高点
        expanding = percentile_in_window(pe, n - 1, None)
        rolling = percentile_in_window(pe, n - 1, ROLLING_WINDOW_MONTHS)
        assert expanding == pytest.approx(100.0 * (n - 1) / n)  # 那个孤立高点仍在扩展窗口里
        assert rolling == pytest.approx(100.0)                   # 滚动 10 年已把它移出窗口

    def test_temperature_is_mean_of_pe_and_pb(self):
        rows = _series()
        pe = [r[2] for r in rows]; pb = [r[3] for r in rows]
        temps = monthly_temperatures(pe, pb)
        i = len(rows) - 1
        pe_p = percentile_in_window(pe, i, ROLLING_WINDOW_MONTHS)
        pb_p = percentile_in_window(pb, i, ROLLING_WINDOW_MONTHS)
        assert temps[i] == pytest.approx((pe_p + pb_p) / 2)

    def test_warmup_months_are_none(self):
        temps = monthly_temperatures(*[[r[k] for r in _series()] for k in (2, 3)])
        assert all(t is None for t in temps[:MIN_WINDOW_MONTHS - 1])
        assert temps[MIN_WINDOW_MONTHS - 1] is not None


class TestZoneEvidence:
    def test_forward_returns_and_two_sample_counts(self):
        """分档表必须同时给重叠 n 与非重叠 n —— 只报前者会把 14 个独立样本吹成 160 个"""
        rows = _series(90)
        close = [r[1] for r in rows]
        # 全部落在同一档位，前视收益由点位直接决定
        temps = [35.0] * 90
        out = forward_return_by_zone(temps, close, forward_months=12)
        row = next(r for r in out if r["zone"] == "合理偏低")
        assert row["n"] == 90 - 12
        assert row["n_independent"] == (90 - 12 - 0 + 11) // 12
        assert row["avg_forward_12m_pct"] == pytest.approx(
            sum((close[i + 12] / close[i] - 1) * 100 for i in range(78)) / 78, abs=0.01
        )

    def test_unobserved_zone_has_no_fake_numbers(self):
        out = forward_return_by_zone([35.0] * 74, [1000.0] * 74, forward_months=12)
        never = next(r for r in out if r["zone"] == "高估")
        assert never["n"] == 0 and never["avg_forward_12m_pct"] is None
        assert never["win_rate_pct"] is None and never["worst_forward_12m_pct"] is None

    def test_last_forward_months_are_excluded(self):
        """末 forward_months 个月没有完整的后视窗口，必须落在样本之外（不然均值被短窗污染）"""
        temps = [35.0] * 74
        out = forward_return_by_zone(temps, [1000.0] * 74, forward_months=12)
        assert next(r for r in out if r["zone"] == "合理偏低")["n"] == 74 - 12


class TestAlignAndPayload:
    def test_inner_join_on_date(self):
        rows = _series()
        pe_df, pb_df = _frames(rows)
        # PB 缺当月点 → 对齐后末点应是上月，而不是错位比较
        pb_df = pb_df.iloc[:-1]
        s = _align_series(pe_df, pb_df)
        assert s["dates"][-1] == rows[-2][0]
        assert len(s["dates"]) == len(rows) - 1
        assert s["pe"][-1] == pytest.approx(rows[-2][2])
        assert s["pb"][-1] == pytest.approx(rows[-2][3])

    def test_rejects_short_series(self):
        pe_df, pb_df = _frames(_series(MIN_WINDOW_MONTHS + FORWARD_MONTHS - 1))
        assert _align_series(pe_df, pb_df) is None

    def test_payload_reproduces_temperature_and_position(self):
        rows = _series()
        s = _align_series(*_frames(rows))
        p = _build_payload(s, time.time())
        temps = monthly_temperatures(s["pe"], s["pb"])
        assert p["temperature"] == pytest.approx(round(temps[-1], 1))
        assert p["suggested_equity_pct"] == round(max(0.0, min(100.0, 100.0 - temps[-1])))
        assert p["pe_percentile"] == pytest.approx(
            round(percentile_in_window(s["pe"], len(rows) - 1, ROLLING_WINDOW_MONTHS), 1)
        )
        assert p["zone"] == zone_of(temps[-1])[0]
        assert p["caliber"].startswith("滚动")
        # 文案必须自证"读数就是当期"：曾因 caliber 只写历史区间被读成"数据停在预热起点"
        assert p["date"] in p["caliber"]
        assert rows[0][0] in p["sample_note"] and f"{len(rows)} 点" in p["sample_note"]
        # 参照口径必须一起给出：两个窗口差 11.5° 是这套数字最大的不确定性来源
        assert p["temperature_expanding"] is not None

    def test_updated_is_beijing_even_in_utc_container(self):
        """缓存时间戳必须显式 +8：NAS 容器 TZ=UTC，用 time.localtime 会比界面其他时刻早 8 小时

        本机是 +8，不钉住 TZ 的话 localtime 也能蒙过这条，故显式切 UTC 再断言。
        """
        saved = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        try:
            p = _build_payload(_align_series(*_frames(_series())), 1_800_000_000)
        finally:
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            time.tzset()
        assert p["updated"] == "2027-01-15 16:00:00"

    def test_rebalance_hint_thresholds(self):
        """<5° 视作噪音；5~10° 只观察；>=10° 才提示动一次；无参照点不判断"""
        assert "视为噪音" in rebalance_hint(3.0)
        assert "视为噪音" in rebalance_hint(-4.9)
        assert "未达" in rebalance_hint(-7.0)
        assert "超过" in rebalance_hint(-12.0)
        assert "超过" in rebalance_hint(11.5)
        assert "暂无上月末参照点" in rebalance_hint(None)

    def test_push_line_shape(self):
        rows = _series()
        p = _build_payload(_align_series(*_frames(rows)), time.time())
        line = format_temperature_line(p)
        assert line.startswith("- 市场温度计：中证800")
        assert f"{p['temperature']}°" in line and p["zone"] in line
        assert "仅市场层参考" in line
        assert p["date"] in line
        assert format_temperature_line(None) is None


class TestCacheAndCooldown:
    """驱动服务本体：需要解除 conftest 的 autouse 屏蔽（网络层仍由 run_with_timeout stub）"""

    @pytest.mark.asyncio
    async def test_failure_cooldowns_and_force_breaks_it(
        self, real_temperature_service, monkeypatch, clean_service
    ):
        calls = []

        async def _boom(func, timeout=None, **kw):
            calls.append(1)
            raise RuntimeError("乐咕风控")
        monkeypatch.setattr("backend.utils.concurrency.run_with_timeout", _boom)

        assert await MarketTemperatureService.get_temperature() is None
        assert await MarketTemperatureService.get_temperature() is None
        assert calls == [1]
        assert MarketTemperatureService._fail_until > time.time()
        await MarketTemperatureService.get_temperature(force=True)
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_unalignable_result_also_cooldowns(
        self, real_temperature_service, monkeypatch, clean_service
    ):
        """接口通了但对不齐/太短：与失败同等对待，否则每个页面请求重打一次"""
        calls = []

        async def _short(func, timeout=None, **kw):
            calls.append(1)
            return _frames(_series(10))
        monkeypatch.setattr("backend.utils.concurrency.run_with_timeout", _short)

        assert await MarketTemperatureService.get_temperature() is None
        assert await MarketTemperatureService.get_temperature() is None
        assert calls == [1]

    @pytest.mark.asyncio
    async def test_success_cached_for_ttl(
        self, real_temperature_service, monkeypatch, clean_service
    ):
        calls = []

        async def _ok(func, timeout=None, **kw):
            calls.append(1)
            return _frames(_series())
        monkeypatch.setattr("backend.utils.concurrency.run_with_timeout", _ok)

        first = await MarketTemperatureService.get_temperature()
        second = await MarketTemperatureService.get_temperature()
        assert first is second and first is not None
        assert calls == [1]
        assert MarketTemperatureService._fail_until == 0.0
