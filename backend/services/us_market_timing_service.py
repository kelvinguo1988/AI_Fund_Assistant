"""美股择时风险面板 — 市场层「风险状态 → 本月动作」，不输出权益比例

判据与全部数字来自 2026-10-11 历史回放（`docs/US_TIMING_REPLAY_2026-10.md`，
复现脚本 `scripts/us_timing_replay.py`）。三条决定性结论：

1. **不给权益比例，但给节奏动作。** 外部模板的三档矩阵（100/75/50）换成人民币口径 + 本仓场外真实费率后，
   相对"静态同权重"基线的超额只有标普 500 +0.11pp/年（噪声级）、纳指 100 −1.25pp/年、
   纳指综合 −0.26pp/年，预注册判据不过。归因显示纳指是**择时技巧本身为负**，不是成本问题。
   被否掉的是"仓位该到哪个数"；**"破位当月先停买、存量不动"没有被否掉**，它是回放里成本最低
   且不依赖预测的那一半，所以 `STATE_ACTIONS` 只写这一类月频节奏规则（见 §7 边界）。
2. **趋势破位的价值只在风险端，且是真的。** 标普 500 最大回撤 −55.8%→−42.0%（改善 24.7%）、
   纳指 100 −25.6%→−18.6%（改善 27.3%），代价是年化少赚 0.5~3 个点。两个数随 payload 下发，
   界面和推送都必须把代价写出来，不能只报"破位防御"。
3. **VIX 逆向加仓要带方向条件。** `VIX>30 且已回落` 后 1 月 +2.64%/胜率 80%（标普、纳指综合
   各 n=5）；`VIX>30 且仍上行` 只有 +0.84%≈基准且含 −16.7% 的接刀月（2008-09、2020-02 恰好
   都是 VIX 首次破 30 的那个月）。所以只有"回落"才点亮加仓提示，并如实标注样本量。

口径纪律：
- 信号用**美元价**算 —— 币种切换只让趋势翻档 2.7~5.0%，但两种口径的收益差 9 个点（2022）。
  ⇒ 汇率不在本服务的关键路径上，USDCNY 取不到不影响读数。
- 月频决策：用**最新完整月**的月末点定档，进行中的当月不参与（见 `month_end_series`）。
- 均线取 MA12（主）与 MA18（慢一档参考）两层，MA 长度按 P0 网格定，不做在线参数搜索。

上游预算：每次刷新 **3 个请求**（新浪 `.INX` / `.NDX` 各 1 + CBOE VIX 1），类级缓存 1h +
失败冷却 120s，命中缓存 0 请求；月度信号下实际每日 ≤1 次刷新。VIX 取不到时降级为只报趋势
（`vix_available=false`），因为 FRED/CBOE 从本机与 NAS 都不稳定 —— 降级是设计而不是缺陷。
"""

import calendar
import logging
import time
from datetime import date
from typing import Optional

from backend.utils.timezone import format_beijing

logger = logging.getLogger(__name__)

# 面板标的：`.NDX` 是 QDII 真实跟踪对象（新浪仅 2014-02 起），`.IXIC` 在回放里用作
# 含 GFC 的长窗验证代理，两者结论同向，线上展示取真实跟踪对象。
US_INDICES: list[dict] = [
    {"key": "spx", "name": "标普500", "symbol": ".INX"},
    {"key": "ndx", "name": "纳斯达克100", "symbol": ".NDX"},
]

MA_MAIN = 12        # 主趋势线（月末点滚动均值）
MA_SLOW = 18        # 慢一档，只作对照
MIN_MONTHS = MA_SLOW  # 预热：不足则整块不出数

VIX_PANIC = 30.0     # 恐慌阈值（P0 网格 25/30/35 里的事件研究定档）
VIX_ELEVATED = 25.0
VIX_CALM = 15.0

# 回放里的"翻档保护带"：月末距当月日历月末超过这个天数即视为未收盘的当月，剔除
INCOMPLETE_MONTH_DAYS = 5

# P0 实证代价，随读数下发（窗口 = 各标的均线预热后的那段，见 docs §3）
RISK_EVIDENCE = {
    "spx": {"window": "2005-01 ~ 2026-09", "dd_hold": -55.8, "dd_timing": -42.0,
            "dd_improve_pct": 24.7, "cagr_cost_pp": -0.49, "vs_static_pp": 0.11,
            "note": "三档矩阵相对静态同权仅 +0.11pp/年，属噪声级"},
    "ndx": {"window": "2015-02 ~ 2026-09", "dd_hold": -25.6, "dd_timing": -18.6,
            "dd_improve_pct": 27.3, "cagr_cost_pp": -3.02, "vs_static_pp": -1.25,
            "note": "择时 alpha −0.97pp/年，负的是技巧不是成本"},
}
ADD_BACK_EVIDENCE = {
    "months_after_pct": 2.64, "win_rate_pct": 80.0, "n": 5,
    "baseline_pct": 0.85, "sample": "标普500 2004-01~2026-09，VIX>30 且已回落的决策月",
    "danger_cell": "VIX>30 且仍上行：后 1 月 −0.11%（破位子集）、最差 −16.7%",
}

STATE_LABELS = {
    "all_ok": ("趋势完好", "两层均线都在上方，无风险层提示"),
    "main_break": ("主线上破位", "跌破 MA12 但仍在 MA18 上方 —— 回放里这是滞后保护开始生效的位置"),
    "both_break": ("趋势全面破位", "MA12 与 MA18 同时失守 —— 历史保护最强的一档"),
    "split": ("两指数状态分化", "标普与纳指不同向，按各自敞口分别看"),
    "warmup": ("样本不足", "月末点不足 MA18 预热期，暂不判档"),
}

# 状态 → 本月动作。这里给的是**月频节奏规则**（新增资金进不进、存量动不动、什么条件下恢复），
# 不是权益比例：预注册回放判据没过（docs §5，标普 vs 静态同权 +0.11pp/年、纳指100 −1.25pp/年），
# 任何"权益该到多少"的数字都没有统计依据，而"这个月先停一停"是回放里成本最低、
# 且不依赖预测的那一半（不动存量还顺带避开 <365 天 0.5% 的赎回费档）。
STATE_ACTIONS: dict[str, dict[str, str]] = {
    "all_ok": {
        "short": "本月不需要动作",
        "do": "按既定计划持有 / 定投，面板不改变你的节奏。",
        "restore": f"改为动作的触发条件：下一个月末点收盘跌破 MA{MA_MAIN}。",
        "avoid": "不要因为「趋势完好 + 涨了很多」就临时加码 —— 这一档只说「没有风险层提示」，"
                  "不说现在便宜，估值高低不在本面板口径内。",
    },
    "main_break": {
        "short": "暂停新增买入，存量一份不卖",
        "do": f"本月起停掉美股基金的定投/加投，存量不动。这是回放里代价最低的保护动作："
              f"不动存量就避开 <365 天 0.5% 的赎回费档，"
              f"也不承担纳指100 择时技巧 −0.97pp/年 的损耗。",
        "restore": f"下一个月末点收盘重新站上 MA{MA_MAIN} → 恢复定投。"
                   f"未破 MA{MA_SLOW} 之前不要把这一档升级成减仓。",
        "avoid": "不要在这一档卖出存量：2018-Q4 快速 V 型里，月度均线的滞后让择时档比持有"
                 "差 4.8pp 且回撤更深（docs §6 分段表）—— 破第一层就动存量是负期望。",
    },
    "both_break": {
        "short": "停买 + 进入减仓评估窗口",
        "do": "新增资金不进美股；存量是否减是你的回撤承受力问题，不是本面板的结论 —— "
              "面板不给减仓比例，因为判据没过（见下方口径说明）。"
              "若决定减，优先减持有满 1 年的份额（赎回费 0.25%，不满 1 年 0.5%），分批而不是一次清。",
        "restore": f"收盘重新站上 MA{MA_MAIN} 才算恢复信号（MA{MA_SLOW} 是第二道确认）。"
                   "历史保护确实最强：标普最大回撤 −55.8% → −42.0%，代价年化 −0.49pp。",
        "avoid": "不要在破位当月一次性清仓：回放里「破位即全退」把最大回撤压到 −17.6%（改善 68.5%），"
                 "但年化只有 8.23%、21 年累计费率 7.75% —— 判据不过主要就输在这里。",
    },
    "split": {
        "short": "分敞口各自动，不合成一个结论",
        "do": "跟踪纳指100 的基金只看纳斯达克100 的状态，跟踪标普500 的只看标普500 的状态；"
              "两边都持有就分别执行各自的档。",
        "restore": "以各自指数的 MA 关系为准，任一指数状态变化只影响对应敞口的动作。",
        "avoid": "不要取平均、也不要「挑好看的那只」来放宽动作 —— 分化期最容易把另一只的趋势"
                 "当成自己敞口的买入理由。",
    },
    "warmup": {
        "short": "不出行动参考",
        "do": f"月末点不足 {MIN_MONTHS} 个（MA{MA_SLOW} 预热期未完成），"
              f"这一档的动作没有任何统计依据，只列数据不列动作。",
        "restore": "样本回到预热长度后面板自动出档，不需要人工干预。",
        "avoid": "不要因为面板没数就改用新闻情绪或盘中涨跌来决定加减速。",
    },
}


def month_end_series(points: list[tuple[str, float]],
                     now: Optional[date] = None) -> list[tuple[str, float]]:
    """日频 (ISO 日期, 收盘) → 月末点序列；进行中的当月剔除

    判据用"距当月日历月末的天数"而不是"是否等于今天"：回放里就是这样定的，
    且容器时钟是 UTC 时也不会把已收盘的月末误当进行中。
    """
    by_month: dict[str, tuple[str, float]] = {}
    for d, close in points:
        if close is None or close <= 0:
            continue
        key = str(d)[:7]
        if key not in by_month or str(d) > by_month[key][0]:
            by_month[key] = (str(d), float(close))
    out = [by_month[k] for k in sorted(by_month)]
    if not out:
        return out
    last_date, _ = out[-1]
    y, m = int(last_date[:4]), int(last_date[5:7])
    month_end = date(y, m, calendar.monthrange(y, m)[1])
    if (month_end - date.fromisoformat(last_date)).days > INCOMPLETE_MONTH_DAYS:
        out = out[:-1]
    del now  # 只依赖数据本身，避免同一份缓存在不同调用日给出不同档
    return out


def rolling_mean(series: list[float], n: int) -> Optional[float]:
    if len(series) < n:
        return None
    return sum(series[-n:]) / float(n)


def vix_state(value: Optional[float], prev: Optional[float]) -> dict:
    """VIX 水平 + 方向：加仓提示要求"高位且已回落"两个条件同时成立"""
    level = "缺数"
    if value is not None:
        if value > VIX_PANIC:
            level = "恐慌"
        elif value > VIX_ELEVATED:
            level = "偏高"
        elif value > VIX_CALM:
            level = "正常"
        else:
            level = "低位"
    delta = None if value is None or prev is None else round(value - prev, 2)
    if delta is None:
        direction = "无对比"
    elif delta <= -0.5:
        direction = "回落"
    elif delta >= 0.5:
        direction = "上行"
    else:
        direction = "持平"
    panic = value is not None and value > VIX_PANIC
    return {
        "value": None if value is None else round(value, 2),
        "prev_month": None if prev is None else round(prev, 2),
        "delta_vs_prev_month": delta,
        "direction": direction,
        "level": level,
        "panic": panic,
        "falling": direction == "回落",
        "threshold": VIX_PANIC,
    }


def _evaluate_index(cfg: dict, month_ends: list[tuple[str, float]]) -> dict:
    closes = [c for _, c in month_ends]
    ma_main = rolling_mean(closes, MA_MAIN)
    ma_slow = rolling_mean(closes, MA_SLOW)
    last = closes[-1] if closes else None
    row = {
        "key": cfg["key"],
        "name": cfg["name"],
        "symbol": cfg["symbol"],
        "close": None if last is None else round(last, 2),
        "ma12": None if ma_main is None else round(ma_main, 2),
        "ma18": None if ma_slow is None else round(ma_slow, 2),
        "gap_main_pct": None,
        "gap_slow_pct": None,
        "trend_main_ok": None,
        "trend_slow_ok": None,
        "months": len(closes),
        "first_point": month_ends[0][0] if month_ends else None,
        "decision_date": month_ends[-1][0] if month_ends else None,
        "warmup_ok": len(closes) >= MIN_MONTHS and ma_slow is not None,
        "risk_evidence": RISK_EVIDENCE.get(cfg["key"]),
    }
    if last and ma_main:
        row["gap_main_pct"] = round((last / ma_main - 1) * 100, 1)
        row["trend_main_ok"] = last >= ma_main
    if last and ma_slow:
        row["gap_slow_pct"] = round((last / ma_slow - 1) * 100, 1)
        row["trend_slow_ok"] = last >= ma_slow
    return row


def _state_key(rows: list[dict]) -> str:
    """综合状态：先看两层趋势，两指数不同向时单列分化（不合成一个数）"""
    live = [r for r in rows if r["warmup_ok"]]
    if not live:
        return "warmup"
    main = [r["trend_main_ok"] for r in live]
    slow = [r["trend_slow_ok"] for r in live]
    if len(set(main)) > 1 or len(set(slow)) > 1:
        return "split"
    if all(main) and all(slow):
        return "all_ok"
    if not any(main) and not any(slow):
        return "both_break"
    return "main_break"


def _add_back_hint(vix: dict) -> Optional[dict]:
    """逆向加仓提示：只在 VIX>30 且已回落时点亮；仍在上行时给反向说明"""
    if not vix["panic"]:
        return None
    if vix["falling"]:
        return {
            "triggered": True,
            "title": "逆向加仓窗口（假设级）",
            "text": (f"VIX {vix['value']} 仍在恐慌区但较上月回落 —— 回放里这一格"
                     f"（VIX>30 且已回落）后 1 月均值 +{ADD_BACK_EVIDENCE['months_after_pct']}%、"
                     f"胜率 {ADD_BACK_EVIDENCE['win_rate_pct']:.0f}%"
                     f"（基准 +{ADD_BACK_EVIDENCE['baseline_pct']}%），"
                     f"但 22 年样本里只有 {ADD_BACK_EVIDENCE['n']} 次"),
            "caveat": "仅提示可用闲钱分批，不改变底仓，不加杠杆；样本 n=5，属假设级",
        }
    return {
        "triggered": False,
        "title": "恐慌区但 VIX 仍在上行",
        "text": (f"VIX {vix['value']} 处于恐慌区但较上月未回落 —— 回放里这一格不是加仓触发："
                 f"{ADD_BACK_EVIDENCE['danger_cell']}（2008-09、2020-02 的首次破 30 都在其中）"),
        "caveat": "等回落再判断，不因为'跌了很多'就加仓",
    }


def _next_decision_month(decision_date: Optional[str]) -> Optional[str]:
    """决策月的下一个月（YYYY-MM）：面板只按月给动作，复核时点必须写清楚"""
    if not decision_date:
        return None
    y, m = int(decision_date[:4]), int(decision_date[5:7])
    return f"{y + 1}-01" if m >= 12 else f"{y}-{m + 1:02d}"


def build_payload(month_ends: dict[str, list[tuple[str, float]]],
                  vix_points: Optional[list[tuple[str, float]]],
                  fetched_at: float) -> dict:
    """月末点 + VIX 月末点 → 展示载荷（纯函数，测试可直接喂构造数据）

    month_ends 的键与 US_INDICES 的 symbol 对齐；缺某个标的时该行进 payload 但
    warmup_ok=false，不让整块面板消失。
    """
    rows = [_evaluate_index(cfg, month_ends.get(cfg["symbol"], [])) for cfg in US_INDICES]

    vix_me = vix_points or []
    vix_value = vix_me[-1][1] if vix_me else None
    vix_prev = vix_me[-2][1] if len(vix_me) >= 2 else None
    vix = vix_state(vix_value, vix_prev)

    state_key = _state_key(rows)
    state_name, state_desc = STATE_LABELS[state_key]
    # 决策月取"所有可算标的都已收池"的那个月：两个指数的新浪序列总是一起更新，
    # 但任一个缺数时不能让面板宣称一个还没收盘的月份。
    live = [r["decision_date"] for r in rows if r["warmup_ok"] and r["decision_date"]]
    decision_date = min(live) if live else (vix_me[-1][0] if vix_me else None)

    # 代价必须与状态同屏：只报"破位该防"而不报少赚多少，就是在推销择时
    cost_parts = [f"{r['name']} {r['risk_evidence']['cagr_cost_pp']}pp/年"
                  for r in rows if r["risk_evidence"]]
    dd_parts = [f"{r['risk_evidence']['dd_hold']}%→{r['risk_evidence']['dd_timing']}%"
                for r in rows if r["risk_evidence"]]
    if cost_parts:
        cost_note = (f"{'、'.join(cost_parts)}（回撤 {'、'.join(dd_parts)}；"
                     f"相对买入持有，人民币口径含场外费率）")
    else:
        cost_note = "无回放数据"

    sample_parts = [f"{r['name']} {r['months']} 个月末点（{r['first_point']} 起）" for r in rows]

    # 动作层：状态档 + 逆向加仓例外 + 覆盖缺口。三者必须拼在一起下发，
    # 否则会出现"卡片说要停买、旁边 VIX 格子说可以补一笔"的自相矛盾。
    add = _add_back_hint(vix)
    action = dict(STATE_ACTIONS[state_key])
    if add and add["triggered"]:
        action["do"] += (
            f" 例外：VIX>30 且已回落，是回放里唯一有统计底气的逆向加仓窗口"
            f"（后 1 月 +{ADD_BACK_EVIDENCE['months_after_pct']}%、"
            f"胜率 {ADD_BACK_EVIDENCE['win_rate_pct']:.0f}%，"
            f"但样本仅 n={ADD_BACK_EVIDENCE['n']}，属假设级）"
            f"—— 可在既定节奏之外用闲钱补一笔，不动底仓、不加杠杆。"
        )
    missing_names = [r["name"] for r in rows if not r["warmup_ok"]]
    if missing_names and state_key != "warmup":
        action["coverage_warning"] = (
            f"{'、'.join(missing_names)} 月末点未预热或缺数 —— "
            f"本月动作只覆盖有数的指数，缺数那只按你自己的持有敞口保守处理"
        )

    return {
        "date": decision_date,
        "updated": format_beijing(fetched_at),
        "vix_available": bool(vix_me),
        "state": state_key,
        "state_name": state_name,
        "state_desc": state_desc,
        "action": action,
        "next_check": (f"下一决策点：{_next_decision_month(decision_date) or '待定'} 月末收盘后复核"
                       f"（月内盘中跌破/站回 MA{MA_MAIN} 都不作为动作依据）"),
        "indices": rows,
        "vix": vix,
        "add_back": add,
        "cost_note": cost_note,
        "risk_evidence": RISK_EVIDENCE,
        "add_back_evidence": ADD_BACK_EVIDENCE,
        "caliber": (f"美元价月末点 · 趋势 = 收盘 vs MA{MA_MAIN}（对照 MA{MA_SLOW}）"
                    f" · 决策月 {decision_date}（当月未收盘不参与）"),
        "sample_note": (f"{'，'.join(sample_parts)}；VIX 月末点 {len(vix_me)} 个；"
                        f"预热需 {MIN_MONTHS} 个月末点"),
        "note": ("市场层风险状态 → 月频节奏规则（新增资金进不进、存量动不动、什么条件下恢复），"
                 "不给权益仓位比例 —— 回放判据：择时相对静态同权超额"
                 " 标普 +0.11pp/年、纳指100 −1.25pp/年，未过预注册门"),
    }


def format_us_timing_line(data: Optional[dict]) -> Optional[str]:
    """推送一行：状态 → 本月动作 + 两层趋势 + VIX + （仅在真有风险层提示时）历史代价

    动作短句必须与状态同屏 —— 只报"破位"不报"那这个月怎么做"，面板就只是一块温度计。
    趋势完好时不播报代价 —— 那时防御动作并没有发生，附上"少赚 3 个点"只是噪声；
    破位或加仓触发时必须附代价：只报保护不报少赚，就是在推销择时。
    """
    if not data or data.get("state") == "warmup":
        return None
    parts = []
    for r in data["indices"]:
        if not r["warmup_ok"]:
            continue
        gap = r.get("gap_main_pct")
        mark = "线上" if r["trend_main_ok"] else "破位"
        parts.append(f"{r['name']} {mark}{f' {gap:+.1f}%' if gap is not None else ''}")
    idx_part = "、".join(parts) or "无可算指数"

    vix = data["vix"]
    vix_part = (f"VIX {vix['value']} {vix['level']}·{vix['direction']}"
                if data["vix_available"] else "VIX 缺数（已降级，只报趋势）")

    tail = ""
    add = data.get("add_back")
    if add:
        tail = ("→ **逆向加仓窗口**（回放 n=5，假设级）" if add["triggered"]
                else "→ 恐慌但 VIX 未回落，回放里这一格不是加仓触发")
    cost = ""
    if data.get("state") != "all_ok":
        cost = f"；防御的历史代价 {data['cost_note']}"
    act = (data.get("action") or {}).get("short") or ""
    act_part = f" → **{act}**" if act else ""
    return (
        f"- 美股择时：{data['state_name']}{act_part} ｜ {idx_part} ｜ {vix_part}{tail}{cost}"
        f"（{data['date']} 月末读数，月频节奏参考、不给仓位比例）"
    )


class UsMarketTimingService:
    """月频面板，日内一次足够：类级缓存 1h + 失败冷却 120s"""

    _cache: Optional[dict] = None
    _ts: float = 0.0
    _TTL = 3600.0
    _FAIL_COOLDOWN = 120.0
    _fail_until: float = 0.0

    @classmethod
    def reset_cache(cls) -> None:
        cls._cache = None
        cls._ts = 0.0
        cls._fail_until = 0.0

    @classmethod
    async def get_panel(cls, force: bool = False) -> Optional[dict]:
        now = time.time()
        if not force and cls._cache is not None and now - cls._ts < cls._TTL:
            return cls._cache
        if not force and now < cls._fail_until:
            return cls._cache

        from backend.utils.concurrency import run_with_timeout

        try:
            frames = await run_with_timeout(_fetch_all, timeout=90.0)
        except Exception as e:
            logger.warning(f"美股择时面板取数失败: {e}")
            cls._fail_until = now + cls._FAIL_COOLDOWN
            return cls._cache

        month_ends, vix_points = frames
        if not any(month_ends.values()):
            # 接口通了但聚不出月末点：与失败同等对待，需要冷却而不是下个请求再打一遍
            logger.warning("美股择时面板聚合后无有效月末点")
            cls._fail_until = now + cls._FAIL_COOLDOWN
            return cls._cache

        payload = build_payload(month_ends, vix_points, now)
        cls._cache = payload
        cls._ts = now
        cls._fail_until = 0.0
        return payload


def _fetch_all() -> tuple[dict, Optional[list]]:
    """同步取数（在 run_with_timeout 的线程里跑）：新浪 2 请求 + CBOE 1 请求

    VIX 单独 try —— 它是境外源，失败只让面板降级成"只报趋势"，不能把趋势层一起带走。
    """
    import akshare as ak

    month_ends: dict[str, list] = {}
    for cfg in US_INDICES:
        df = ak.index_us_stock_sina(symbol=cfg["symbol"])
        pts = [(str(r["date"]), float(r["close"]))
               for _, r in df.iterrows() if r.get("close") is not None and str(r["close"]) != "nan"]
        month_ends[cfg["symbol"]] = month_end_series(pts)
    try:
        vix_points = month_end_series(_fetch_vix())
    except Exception as e:
        logger.warning(f"美股择时面板 VIX 取数失败，降级为只报趋势: {e}")
        vix_points = None
    return month_ends, vix_points


VIX_CSV_URL = "http://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"


def _fetch_vix() -> list[tuple[str, float]]:
    """CBOE 官方 VIX 全历史 CSV（1990 起），单请求

    首行是表头；日期 `MM/DD/YYYY`。返回 (ISO 日期, 收盘) 列表。
    """
    from backend.utils.concurrency import random_ua
    import urllib.request

    req = urllib.request.Request(VIX_CSV_URL, headers={"User-Agent": random_ua()})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="ignore")
    out: list[tuple[str, float]] = []
    for line in text.splitlines()[1:]:
        cols = line.split(",")
        if len(cols) < 5 or "/" not in cols[0]:
            continue
        try:
            mm, dd, yy = cols[0].split("/")
            close = float(cols[4])
        except ValueError:
            continue
        out.append((f"{int(yy):04d}-{int(mm):02d}-{int(dd):02d}", close))
    return out
