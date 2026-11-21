"""数据源运行时健康快照 — 只读聚合进程内的冷却 / 降级 / 缓存新鲜度（2026-09-29 审查 P3）

与 `/api/system/connectivity` 的分工：那个端点回答"现在能不能连上"（会真的发
探测请求）；本模块回答"刚刚被谁限了、还要冷却多久、手上的数据有多旧"，全部从
既有状态变量读取，**零网络请求**。排查"仪表盘一片空"时应先看这里，避免用探测
接口把已经在冷却的源再打一遍（那正是二次封禁的成因）。

实现取舍：这些冷却/缓存状态本来就散在各自模块的类属性里（进程单例语义），此处
直接读取而不逐处加 public getter —— 集中一处便于排查，代价是新增冷却状态时必须
记得在 `_collect_*` 里登记，否则界面上会漏项。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from backend.utils.timezone import now_beijing


def _cooldown(name: str, until_ts: float, now: float, note: str = "") -> Optional[dict[str, Any]]:
    """仍处于冷却期才输出一条；已过期返回 None"""
    remaining = until_ts - now
    if remaining <= 0:
        return None
    return {"name": name, "remaining_seconds": round(remaining, 1), "note": note}


def _cache(name: str, ts: float, ttl: float, size: int, now: float) -> dict[str, Any]:
    age = round(now - ts, 1) if ts else None
    return {
        "name": name,
        "age_seconds": age,
        "ttl_seconds": round(ttl, 1),
        "entries": size,
        "stale": bool(ts is None or (now - ts) > ttl),
    }


def _collect_cooldowns(now: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    from backend.data_sources.akshare_adapter import AKShareAdapter
    from backend.services import fund_manager_service
    from backend.services.fund_realtime_service import FundRealtimeService
    from backend.services.index_valuation_service import (
        IndexValuationService, OtcTradeStatusService,
    )
    from backend.services.market_service import MarketService

    # 实时行情数据源熔断（腾讯 / 东财 / fundgz 等，单源失败即整源冷却）
    for src, until in FundRealtimeService._source_fail_until.items():
        item = _cooldown(f"realtime.{src}", until, now, "数据源熔断（防封禁）")
        if item:
            out.append(item)

    gz = _cooldown(
        "realtime.fundgz", FundRealtimeService._fundgz_fail_until, now,
        "盘中估值接口反爬冷却",
    )
    if gz:
        out.append(gz)

    # 市场概况四级降级链耗尽
    for key, until in MarketService._fail_cache.items():
        item = _cooldown(f"market.{key}", until, now, "降级链全部失败")
        if item:
            out.append(item)

    # 指数估值 / 场外申购状态整批失败后的短冷却（防重试风暴）
    for label, until in (
        ("index_valuation.leiguru", IndexValuationService._fail_until),
        ("otc_trade_status.eastmoney", OtcTradeStatusService._fail_until),
    ):
        item = _cooldown(label, until, now, "整批取数失败，静默窗口内不重试")
        if item:
            out.append(item)

    # AKShare 全量类接口失败冷却（这些一旦失效每次分析都会重拉，冷却即防风暴）
    for label, ts, cooldown in (
        ("akshare.fund_open_fund_rank_em", AKShareAdapter._fund_name_fail_ts, AKShareAdapter._NAME_FETCH_COOLDOWN),
        ("akshare.bond_yield", AKShareAdapter._bond_yield_fail_ts, AKShareAdapter._BOND_YIELD_FAIL_COOLDOWN),
        ("akshare.probe", AKShareAdapter._probe_fail_ts, AKShareAdapter._PROBE_COOLDOWN),
    ):
        item = _cooldown(label, ts + cooldown, now, "失败后静默，避免重试风暴")
        if item:
            out.append(item)

    # 注意用属性访问：`from x import name` 绑定的是导入瞬间的值，模块内
    # global 重新赋值后就读不到了
    item = _cooldown("managers.fund_manager_em",
                     fund_manager_service._last_fail_time + fund_manager_service._FAIL_COOLDOWN,
                     now, "全量经理表拉取失败，短窗口内不重试")
    if item:
        out.append(item)

    return out


def _collect_caches(now: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    from backend.data_sources.akshare_adapter import AKShareAdapter
    from backend.services import fund_manager_service
    from backend.services.index_valuation_service import (
        IndexValuationService, OtcTradeStatusService,
    )
    from backend.services.market_service import MarketService

    for key, (ts, _val) in sorted(MarketService._cache.items()):
        out.append(_cache(f"market.{key}", ts, MarketService._CACHE_TTL, 1, now))

    out.append(_cache(
        "index_valuation", IndexValuationService._ts, IndexValuationService._TTL,
        len(IndexValuationService._cache or []), now,
    ))
    out.append(_cache(
        "otc_trade_status", OtcTradeStatusService._ts, OtcTradeStatusService._TTL,
        len(OtcTradeStatusService._cache or {}), now,
    ))
    out.append(_cache(
        "managers.fund_manager_em", fund_manager_service._cache_ts,
        fund_manager_service._MANAGER_CACHE_TTL,
        len(fund_manager_service._manager_cache or []), now,
    ))
    out.append(_cache(
        "akshare.fund_name_map", AKShareAdapter._cache_timestamp,
        AKShareAdapter._CACHE_TTL, len(AKShareAdapter._fund_name_map or {}), now,
    ))
    out.append(_cache(
        "akshare.bond_yield", AKShareAdapter._bond_yield_ts,
        AKShareAdapter._SHARED_CACHE_TTL,
        1 if AKShareAdapter._bond_yield_cache is not None else 0, now,
    ))
    for idx_code, (ts, _df) in sorted(AKShareAdapter._index_value_cache.items()):
        out.append(_cache(f"akshare.index_value.{idx_code}", ts,
                          AKShareAdapter._SHARED_CACHE_TTL, 1, now))

    return out


# 数据末点新鲜度（2026-11-21 新增）
#
# 为什么需要这一维度：现有 `_cache()` 只比较「缓存年龄 vs TTL」，而温度计停摆
# 6 天那次恰恰是 **缓存看似新鲜、数据却是 6 天前** —— 每次请求都失败并返回旧
# cache，`_ts` 一直停在 11-15，`date` 停在 11-13，界面上却看不出任何异常。
# 用户只能靠自己发现「怎么还是 13 号」。
#
# 这里登记「该服务手上的数据末点是哪天」，与当天比较给出滞后天数。
# 阈值按数据频率分档：日频 >3 天可疑、周频 >10 天、月频 >45 天。
_FRESHNESS_RULES = {
    # name: (max_lag_days, 说明)
    "temperature": (7, "月频序列，但每月都有新点；>7 天说明上游停摆"),
    "index_valuation": (7, "同温度计同源（乐咕月频）"),
    "market_capital_flow": (3, "日频资金流，>3 天可疑"),
    "us_market_timing": (40, "月频趋势，容忍度宽"),
}


def _collect_freshness(now: float) -> list[dict[str, Any]]:
    """各服务「手上数据的末点日期」vs 当天（纯内存，零网络）"""
    out: list[dict[str, Any]] = []
    today = now_beijing().date()

    def _add(name: str, data_date, extra: Optional[dict] = None) -> None:
        if not data_date:
            # 缓存为空也要露一条：服务还没成功取过数（首次启动 / 一直在失败），
            # 比"列表里干脆没有这一项"更容易发现异常
            out.append({
                "name": name, "data_date": None, "lag_days": None,
                "max_lag_days": _FRESHNESS_RULES.get(name, (7, ""))[0],
                "stale": True, "note": "尚无数据（未成功取过数）",
                **(extra or {}),
            })
            return
        try:
            if isinstance(data_date, str):
                from datetime import datetime
                d = datetime.strptime(str(data_date)[:10], "%Y-%m-%d").date()
            else:
                d = data_date
        except (ValueError, TypeError):
            return
        lag = (today - d).days
        limit, note = _FRESHNESS_RULES.get(name, (7, ""))
        item: dict[str, Any] = {
            "name": name,
            "data_date": d.isoformat(),
            "lag_days": lag,
            "max_lag_days": limit,
            "stale": lag > limit,
            "note": note,
        }
        if extra:
            item.update(extra)
        out.append(item)

    # 温度计
    try:
        from backend.services.market_temperature_service import MarketTemperatureService
        cache = MarketTemperatureService._cache or {}
        _add("temperature", cache.get("date"), {
            "fail_count": MarketTemperatureService._fail_count,
            "cached_at": (
                now_beijing().fromtimestamp(MarketTemperatureService._ts).strftime("%Y-%m-%d %H:%M")
                if MarketTemperatureService._ts else None
            ),
        })
    except Exception:  # noqa: BLE001 - 健康快照本身不能崩
        pass

    # 指数高低估
    try:
        from backend.services.index_valuation_service import IndexValuationService
        rows = IndexValuationService._cache or []
        dates = [r.get("date") for r in rows if isinstance(r, dict) and r.get("date")]
        if dates:
            _add("index_valuation", max(dates))
    except Exception:  # noqa: BLE001
        pass

    # 美股择时
    try:
        from backend.services.us_market_timing_service import UsMarketTimingService
        cache = UsMarketTimingService._cache or {}
        _add("us_market_timing", cache.get("date"))
    except Exception:  # noqa: BLE001
        pass

    return out


def _collect_scheduler(now: float) -> dict[str, Any]:
    """调度器当日熔断：某计划连续失败达上限后当日不再重试（防整天反复打被限的源）"""
    from backend.scheduler.task_scheduler import task_scheduler

    today = now_beijing().strftime("%Y-%m-%d")
    trips = [
        {"schedule_id": sid, "consecutive_failures": fails}
        for (day, sid), fails in task_scheduler._daily_fail.items()
        if day == today and fails >= task_scheduler.DAILY_FAIL_LIMIT
    ]
    return {
        "daily_fail_limit": task_scheduler.DAILY_FAIL_LIMIT,
        "tripped_today": sorted(trips, key=lambda x: x["schedule_id"]),
    }


def collect_health() -> dict[str, Any]:
    """聚合快照（纯内存读取，可在 async 路径直接调用）"""
    now = time.time()
    cooldowns = _collect_cooldowns(now)
    caches = _collect_caches(now)
    freshness = _collect_freshness(now)
    return {
        "generated_at": now_beijing().strftime("%Y-%m-%d %H:%M:%S"),
        "cooldowns": cooldowns,
        "caches": caches,
        "freshness": freshness,
        "scheduler": _collect_scheduler(now),
        "summary": {
            "cooldown_count": len(cooldowns),
            "stale_cache_count": sum(1 for c in caches if c["stale"]),
            # 数据末点滞后超阈值 —— 这次温度计停摆 6 天正是这类，原快照看不出
            "stale_data_count": sum(1 for f in freshness if f["stale"]),
            "stale_data_names": [f["name"] for f in freshness if f["stale"]],
        },
    }
