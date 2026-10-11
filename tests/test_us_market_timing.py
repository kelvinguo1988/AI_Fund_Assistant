"""美股择时风险面板 —— 口径 / 降级 / 缓存测试（全程零上游请求）

这个面板的全部风险也在口径上，而且比 A 股温度计多两条：
① 它必须**不出仓位建议**（P0 回放判据没过，docs/US_TIMING_REPLAY_2026-10.md §0），
   所以"没有仓位数字"本身就是一条要锁住的断言，防止后人手滑加回 100/75/50；
② VIX 是境外源，必须能单独失败并降级成"只报趋势"，不能让整块面板消失。

纯函数直接喂构造月末点；缓存/冷却用例 stub 模块级 `_fetch_all`（配合
`real_us_timing_service` 解除 conftest 的屏蔽），一次网络请求都不会发。
"""

import calendar
import os
import re
import time
from datetime import date

import pytest

from backend.services.us_market_timing_service import (
    ADD_BACK_EVIDENCE,
    MA_MAIN,
    MA_SLOW,
    MIN_MONTHS,
    US_INDICES,
    UsMarketTimingService,
    build_payload,
    format_us_timing_line,
    month_end_series,
    rolling_mean,
    vix_state,
)


def _month_end(y: int, m: int) -> str:
    return date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def _points(n: int, start=(2020, 1), base: float = 100.0, step: float = 1.0):
    """连续 n 个月末点（日期就是日历月末，故不会被当成未收盘当月剔掉）"""
    out, (y, m) = [], start
    for i in range(n):
        out.append((_month_end(y, m), base + step * i))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _series_map(closes_a, closes_b=None):
    """按标的给收盘价列表，生成月末点"""
    out = {US_INDICES[0]["symbol"]: list(zip([d for d, _ in _points(len(closes_a))], closes_a))}
    b = closes_b if closes_b is not None else closes_a
    out[US_INDICES[1]["symbol"]] = list(zip([d for d, _ in _points(len(b))], b))
    return out


# ── month_end_series ────────────────────────────────────────────────
def test_month_end_keeps_last_observation_of_each_month():
    pts = [("2026-01-05", 10.0), ("2026-01-30", 11.0), ("2026-02-02", 12.0), ("2026-02-27", 13.0)]
    assert month_end_series(pts) == [("2026-01-30", 11.0), ("2026-02-27", 13.0)]


def test_month_end_drops_incomplete_current_month():
    """进行中的当月不能定档：10-09 距日历 10-31 差 22 天 → 整个 2026-10 桶被剔除"""
    pts = _points(20) + [("2026-10-09", 999.0)]
    out = month_end_series(pts)
    assert "2026-10" not in [d[:7] for d, _ in out]
    assert len(out) == 20 and out[-1][0] == _month_end(2021, 8)


def test_month_end_keeps_weekend_and_holiday_month_end():
    """月末落在周末/短休时只差 1~3 天，必须保留，否则序列凭空少一个月"""
    pts = _points(20) + [("2026-08-31", 140.0)]  # 周一 = 日历月末
    assert month_end_series(pts)[-1] == ("2026-08-31", 140.0)
    pts2 = _points(20)[:-1] + [("2026-07-31", 139.0), ("2026-08-28", 140.0)]  # 8 月最后观察 8-28（周五）
    assert month_end_series(pts2)[-1][0] == "2026-08-28"


def test_month_end_skips_nonpositive_and_null_closes():
    """0 / None 脏点不进均线，且同月要以"最后一个有效点"定档"""
    pts = [("2026-01-15", 5.0), ("2026-01-30", 0.0),
           ("2026-02-10", None), ("2026-02-27", None),
           ("2026-03-05", 8.0), ("2026-03-31", 9.0)]
    assert month_end_series(pts) == [("2026-01-15", 5.0), ("2026-03-31", 9.0)]


# ── 趋势与预热 ───────────────────────────────────────────────────────
def test_rolling_mean_needs_full_window():
    assert rolling_mean([1.0] * (MA_SLOW - 1), MA_SLOW) is None
    assert rolling_mean([1.0] * MA_SLOW, MA_SLOW) == 1.0


def test_uptrend_is_all_ok_and_reports_gaps():
    closes = [100.0 + i for i in range(24)]
    p = build_payload(_series_map(closes), _points(24, base=15.0, step=0.0), 1_800_000_000)
    assert p["state"] == "all_ok" and p["state_name"] == "趋势完好"
    row = p["indices"][0]
    assert row["trend_main_ok"] and row["trend_slow_ok"]
    # MA12 = 均值 of 最后 12 个 = 117.5，末点 123 → +4.7%
    assert row["ma12"] == 117.5 and row["gap_main_pct"] == 4.7


def test_break_below_both_mas_is_both_break():
    closes = [100.0 + i for i in range(21)] + [70.0, 68.0, 66.0]
    p = build_payload(_series_map(closes), None, 1_800_000_000)
    assert p["state"] == "both_break"
    assert not p["indices"][0]["trend_main_ok"]
    assert p["indices"][0]["gap_main_pct"] < 0


def test_main_break_only_when_above_slow_ma():
    """跌破 MA12 但仍在 MA18 上方 = 介于两者之间的一档，不能误报成全面破位

    构造要成立必须让慢线含住更早的低点：前 9 个月 50（把 MA18 压低），
    再 14 个月 100→113，最后一个月回落到 106 —— 于是
    MA12 = 107.8 > 106（主线破位），MA18 = 97.1 < 106（慢线仍在线上）。
    """
    closes = [50.0] * 9 + [100.0 + i for i in range(14)] + [106.0]
    p = build_payload(_series_map(closes), None, 1_800_000_000)
    row = p["indices"][0]
    assert row["trend_main_ok"] is False and row["trend_slow_ok"] is True
    assert row["gap_main_pct"] < 0 and row["gap_slow_pct"] > 0
    assert p["state"] == "main_break" and p["state_name"] == "主线上破位"
    line = format_us_timing_line(p)
    assert "破位" in line and p["cost_note"] in line   # 这一档起必须同屏报代价


def test_split_state_when_two_indices_disagree():
    up = [100.0 + i for i in range(24)]
    down = [100.0 + i for i in range(21)] + [70.0, 68.0, 66.0]
    p = build_payload(_series_map(up, down), None, 1_800_000_000)
    assert p["state"] == "split" and p["state_name"] == "两指数状态分化"


def test_warmup_short_series_does_not_judge():
    p = build_payload(_series_map([100.0 + i for i in range(MIN_MONTHS - 1)]), None, 1_800_000_000)
    assert p["state"] == "warmup"
    assert all(not r["warmup_ok"] for r in p["indices"])
    assert format_us_timing_line(p) is None


def test_missing_one_index_keeps_other_and_marks_warmup():
    """新浪单指数偶发缺数不能让整块面板消失：缺的那行 warmup_ok=false"""
    smap = {US_INDICES[0]["symbol"]: _points(24)}
    p = build_payload(smap, _points(24), 1_800_000_000)
    assert p["indices"][0]["warmup_ok"] is True
    assert p["indices"][1]["warmup_ok"] is False
    # 决策月只认"两个标的都收池"的那个月：缺一个时不能让面板宣称未收盘的月份
    assert p["date"] == _month_end(2021, 12)
    assert p["state"] == "all_ok"


# ── VIX ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("val,lvl", [(12.0, "低位"), (20.0, "正常"), (27.0, "偏高"), (45.0, "恐慌")])
def test_vix_level_bands(val, lvl):
    assert vix_state(val, val)["level"] == lvl


def test_vix_direction_has_tolerance_band():
    assert vix_state(31.0, 32.0)["direction"] == "回落"
    assert vix_state(32.0, 31.0)["direction"] == "上行"
    assert vix_state(30.4, 30.0)["direction"] == "持平"  # 0.4 < 0.5 抖动带
    assert vix_state(30.4, None)["direction"] == "无对比"


def test_add_back_triggers_only_on_panic_and_falling():
    """加仓触发的唯一条件是 VIX>30 且已回落（P0 §5：仍上行那格后 1 月 −0.11%、最差 −16.7%）"""
    falling = vix_state(33.0, 44.0)
    assert falling["panic"] and falling["falling"]
    p = build_payload(_series_map([100.0 + i for i in range(24)]),
                      [("2026-08-31", 44.0), ("2026-09-30", 33.0)], 1_800_000_000)
    assert p["add_back"]["triggered"] is True
    assert f"n={ADD_BACK_EVIDENCE['n']}" in p["add_back"]["text"] or "5 次" in p["add_back"]["text"]
    assert "分批" in p["add_back"]["caveat"]

    rising = build_payload(_series_map([100.0 + i for i in range(24)]),
                           [("2026-08-31", 28.0), ("2026-09-30", 40.0)], 1_800_000_000)
    assert rising["add_back"]["triggered"] is False
    assert "不是加仓触发" in rising["add_back"]["text"]

    calm = build_payload(_series_map([100.0 + i for i in range(24)]),
                         [("2026-08-31", 16.0), ("2026-09-30", 15.0)], 1_800_000_000)
    assert calm["add_back"] is None


def test_vix_missing_degrades_not_fails():
    up = [100.0 + i for i in range(24)]
    p = build_payload(_series_map(up), None, 1_800_000_000)
    assert p["vix_available"] is False
    assert p["state"] == "all_ok"          # 趋势层照常出数
    assert p["vix"]["value"] is None and p["vix"]["level"] == "缺数"
    line = format_us_timing_line(p)
    assert "VIX 缺数" in line and "只报趋势" in line


# ── 载荷文案与"不出仓位"红线 ─────────────────────────────────────────
def test_payload_dates_are_reading_month_not_sample_range():
    """读数时效与样本范围必须物理分开（A 股温度计 2026-10-10 的教训）"""
    up = [100.0 + i for i in range(24)]
    p = build_payload(_series_map(up), _points(24), 1_800_000_000)
    assert p["date"] in p["caliber"]
    assert p["date"] == p["indices"][0]["decision_date"]
    # 样本起点只出现在 sample_note / indices 里，绝不混进 caliber
    assert p["indices"][0]["first_point"] not in p["caliber"]
    assert f'{p["indices"][0]["months"]} 个月末点' in p["sample_note"]
    assert p["indices"][0]["first_point"] in p["sample_note"]


def test_updated_is_beijing_even_in_utc_container():
    saved = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        p = build_payload(_series_map([100.0 + i for i in range(24)]), None, 1_800_000_000)
    finally:
        if saved is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = saved
        time.tzset()
    assert p["updated"] == "2027-01-15 16:00:00"


def test_no_position_numbers_anywhere():
    """P0 判据没过 → 载荷与推送都不许出现仓位数字或"参考权益仓位"字样

    动作层（action）也必须过这条：它能说"这个月停买"，不能说"权益降到 X%"。
    """
    up = [100.0 + i for i in range(24)]
    p = build_payload(_series_map(up), _points(24, base=33.0, step=-0.5), 1_800_000_000)
    for key in ("suggested_equity_pct", "suggested_position", "target_equity_pct", "position_pct"):
        assert key not in p
        assert all(key not in r for r in p["indices"])
    assert "action" in p and "suggested_equity_pct" not in p["action"]
    blob = repr(p) + repr(format_us_timing_line(p))
    assert "参考权益仓位" not in blob and "建议仓位" not in blob
    assert not re.search(r"仓位\s*[=:为]?\s*\d", blob)


def test_every_state_ships_a_full_action_block():
    """面板不能只报状态不报动作；唯一例外是预热不足，那时明确写不出行动参考"""
    up = [100.0 + i for i in range(24)]
    down = [100.0 + i for i in range(21)] + [70.0, 68.0, 66.0]
    break12 = [50.0] * 9 + [100.0 + i for i in range(14)] + [106.0]   # 破 MA12、未破 MA18
    cases = {
        "all_ok": _series_map(up),
        "main_break": _series_map(break12),
        "both_break": _series_map(down),
        "split": _series_map(up, down),
        "warmup": _series_map([100.0 + i for i in range(6)]),
    }
    for key, mp in cases.items():
        p = build_payload(mp, _points(24), 1_800_000_000)
        assert p["state"] == key, (key, p["state"])
        act = p["action"]
        assert act["short"] and act["do"] and act["restore"] and act["avoid"]
        if key == "warmup":
            assert act["short"] == "不出行动参考"
            assert format_us_timing_line(p) is None
        else:
            assert p["action"]["short"] in format_us_timing_line(p)


def test_main_break_stops_new_money_but_never_touchs_stocks():
    """破第一层的动作是停买，不是卖：动存量要吃 0.5% 短持有期赎回费 + 负技巧"""
    break12 = [50.0] * 9 + [100.0 + i for i in range(14)] + [106.0]
    p = build_payload(_series_map(break12), None, 1_800_000_000)
    act = p["action"]
    assert p["state"] == "main_break"
    assert "暂停新增买入" in act["short"] and "不卖" in act["short"]
    assert "0.5%" in act["do"] and "赎回费" in act["do"]
    assert f"MA{MA_MAIN}" in act["restore"] and "恢复定投" in act["restore"]
    assert "2018-Q4" in act["avoid"]      # 假保护的那段必须同屏，否则是在推销择时


def test_add_back_unlocks_an_exception_inside_the_action_text():
    """VIX>30 且已回落时，动作正文要带上唯一那个逆向例外；否则不许出现"加仓窗口" """
    up = [100.0 + i for i in range(24)]
    panic_falling = build_payload(_series_map(up),
                                  [("2026-08-31", 44.0), ("2026-09-30", 33.0)], 1_800_000_000)
    assert "逆向加仓窗口" in panic_falling["action"]["do"]
    assert f"n={ADD_BACK_EVIDENCE['n']}" in panic_falling["action"]["do"]
    assert "不动底仓" in panic_falling["action"]["do"] and "不加杠杆" in panic_falling["action"]["do"]

    calm = build_payload(_series_map(up),
                         [("2026-08-31", 16.0), ("2026-09-30", 15.0)], 1_800_000_000)
    assert "逆向加仓窗口" not in calm["action"]["do"]


def test_partial_coverage_action_says_which_index_is_missing():
    """一只指数没预热好时不能让"本月不需要动作"覆盖掉另一只的敞口缺口"""
    up = [100.0 + i for i in range(24)]
    p = build_payload({US_INDICES[0]["symbol"]: list(zip([d for d, _ in _points(24)], up)),
                       US_INDICES[1]["symbol"]: []}, None, 1_800_000_000)
    assert p["state"] == "all_ok"
    assert US_INDICES[1]["name"] in p["action"]["coverage_warning"]
    assert "coverage_warning" not in build_payload(_series_map(up), None, 1_800_000_000)["action"]


def test_next_check_is_the_month_after_the_reading():
    up = [100.0 + i for i in range(24)]     # 2020-01 起 24 个月末点 → 决策月 2021-12
    p = build_payload(_series_map(up), None, 1_800_000_000)
    assert p["date"].startswith("2021-12")
    assert "2022-01 月末收盘后" in p["next_check"]
    assert f"MA{MA_MAIN}" in p["next_check"]


def test_push_line_carries_cost_only_when_defense_is_signaled():
    up = [100.0 + i for i in range(24)]
    ok = build_payload(_series_map(up), _points(24), 1_800_000_000)
    line = format_us_timing_line(ok)
    assert "历史代价" not in line and "不给仓位比例" in line
    assert ok["indices"][0]["name"] in line and ok["indices"][1]["name"] in line

    down = [100.0 + i for i in range(21)] + [70.0, 68.0, 66.0]
    bad = build_payload(_series_map(down), _points(24), 1_800_000_000)
    assert bad["state"] == "both_break"
    line2 = format_us_timing_line(bad)
    assert "历史代价" in line2 and "pp/年" in line2 and "回撤" in line2
    assert bad["cost_note"] in line2


# ── 缓存 / 冷却 / 降级（stub _fetch_all，零请求）─────────────────────
@pytest.fixture
def cleared(real_us_timing_service):
    real_us_timing_service.UsMarketTimingService.reset_cache()
    yield real_us_timing_service
    real_us_timing_service.UsMarketTimingService.reset_cache()


@pytest.mark.asyncio
async def test_cache_hit_and_ttl(cleared, monkeypatch):
    calls = []
    monkeypatch.setattr(cleared, "_fetch_all",
                        lambda: (calls.append(1) or (_series_map([100.0 + i for i in range(24)]), _points(24))))
    p1 = await UsMarketTimingService.get_panel()
    p2 = await UsMarketTimingService.get_panel()
    assert p1 is p2 and len(calls) == 1
    assert p1["state"] == "all_ok"
    await UsMarketTimingService.get_panel(force=True)
    assert len(calls) == 2
    UsMarketTimingService._ts = time.time() - UsMarketTimingService._TTL - 1
    await UsMarketTimingService.get_panel()
    assert len(calls) == 3          # TTL 过期才允许再打上游


@pytest.mark.asyncio
async def test_fetch_failure_sets_cooldown_and_returns_stale(cleared, monkeypatch):
    """失败要回 stale 并进冷却，且冷却期内绝不重打上游（新浪日线是大请求）"""
    calls = []
    fail = {"on": False}

    def fetch():
        calls.append(1)
        if fail["on"]:
            raise RuntimeError("新浪 502")
        return _series_map([100.0 + i for i in range(24)]), _points(24)

    monkeypatch.setattr(cleared, "_fetch_all", fetch)
    ok = await UsMarketTimingService.get_panel()
    fail["on"] = True
    UsMarketTimingService._ts = 0.0            # 假装 TTL 过期，强制走取数分支
    await UsMarketTimingService.get_panel()    # 失败 → 进冷却，缓存时间戳不推进
    assert UsMarketTimingService._fail_until > time.time()
    UsMarketTimingService._ts = 0.0
    stale = await UsMarketTimingService.get_panel()
    assert stale is ok and len(calls) == 2     # 冷却挡住重取，回 stale
    UsMarketTimingService._fail_until = 0.0
    fail["on"] = False
    recovered = await UsMarketTimingService.get_panel()
    assert recovered["state"] == ok["state"] and len(calls) == 3
    assert UsMarketTimingService._fail_until == 0.0   # 成功即清冷却


@pytest.mark.asyncio
async def test_empty_aggregation_is_treated_as_failure(cleared, monkeypatch):
    """接口通了但聚不出月末点：要冷却，否则每个仪表盘请求都重打新浪"""
    calls = []
    monkeypatch.setattr(cleared, "_fetch_all", lambda: (calls.append(1) or ({}, [])))
    assert await UsMarketTimingService.get_panel() is None
    assert await UsMarketTimingService.get_panel() is None
    assert len(calls) == 1
    assert UsMarketTimingService._fail_until > time.time()


@pytest.mark.asyncio
async def test_vix_failure_degrades_panel_instead_of_failing_it(cleared, monkeypatch):
    """VIX 是境外源：它挂了趋势层必须照常出数（缓存里 vix_available=false，不算失败）"""
    monkeypatch.setattr(cleared, "_fetch_all",
                        lambda: (_series_map([100.0 + i for i in range(24)]), None))
    p = await UsMarketTimingService.get_panel()
    assert p is not None and p["vix_available"] is False
    assert p["state"] == "all_ok"
    assert UsMarketTimingService._fail_until == 0.0   # 降级不是失败，不进冷却
