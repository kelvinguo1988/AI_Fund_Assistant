"""中证800 市场温度计 — 市场层「行动参考」，不参与任何单只基金的买卖判定

口径（2026-10-09 历史回放定案，全部数字与复现方法见 README「市场温度计的历史回放口径」一节）：
- 数据源：乐咕乐股指数 PE / PB 历史（`stock_index_pe_lg` / `stock_index_pb_lg`，
  两者都支持中证800 = 000906.SH），**月频 238 点、2007-01 起**，PE 表自带 `指数` 点位列
  ⇒ 一次抓取即可同时得到点位 + PE + PB，全服务只用 2 个上游请求。
- 温度 = (PE 分位 + PB 分位) / 2，取值 0~100。
- **分位窗口是主变量不是细节**：同一份数据，扩展窗口当下算出 41.0°，滚动 10 年算出 52.5°，
  差 11.5°。主口径取滚动 10 年（`ROLLING_WINDOW_MONTHS`），扩展窗口只作为参照字段一并返回。
- **point-in-time**：第 i 个月的分位只用截至第 i 个月的历史，绝不使用全样本未来信息，
  否则分档收益表全是前视伪值。
- 预热：温度至少需要 `MIN_WINDOW_MONTHS` 个月历史，不足时为 None（本序列有效起点 2012-01）。

为什么只做成"看"的：回放里温度对**其后 12 个月指数收益**的方向性关系是真的
（50-70 档胜率 12%、0-20 档胜率 72%），但非重叠独立观测只有 14 个，
每档 1~5 个样本 —— 撑不起任何自动下单动作，所以这里只输出仓位参考与历史证据。

缓存纪律与 IndexValuationService 一致：类级缓存 1h + 失败冷却 120s。
失败不推进缓存时间戳会让每个页面请求都重打乐咕（前端无轮询但仪表盘/推送都会读），
把一次风控抖动放大成请求风暴。
"""

import logging
import time
from typing import Optional

from backend.utils.stats import percentile_rank_inclusive

logger = logging.getLogger(__name__)

# 温度计基准指数（乐咕 symbol，PE/PB 两个接口共用同一张映射表）
TEMPERATURE_INDEX = "中证800"

# 分位窗口：滚动 10 年 = 120 个月点；扩展窗口只用 MIN_WINDOW_MONTHS 做预热下限
ROLLING_WINDOW_MONTHS = 120
MIN_WINDOW_MONTHS = 60

# 参考权益仓位 = 100 − 温度（DeepSeek 方案的仓位映射，保留原样便于对照）
EQUITY_CEILING_PCT = 100.0

# 调仓迟滞：相对上一月末点变化不足 REBALANCE_IGNORE_DEG 度时视作噪音不动作，
# 达到 REBALANCE_TRIGGER_DEG 度才提示"该动一次"（月频数据下日内的分位抖动没有意义）
REBALANCE_IGNORE_DEG = 5.0
REBALANCE_TRIGGER_DEG = 10.0

# 档位 → 行动参考。阈值沿用回放定案的 20/30/50/70 四刀，
# 与 DASHBOARD_PE_LOW/HIGH_PERCENTILE（展示用低估/高估两档）不是同一套分区。
ZONE_RULES: list[tuple[float, float, str, str]] = [
    (0.0, 20.0, "极度低估", "可分批加仓，权益可上到 8 成以上"),
    (20.0, 30.0, "低估", "正常定投 + 适度加码"),
    (30.0, 50.0, "合理偏低", "正常持有，按计划定投"),
    (50.0, 70.0, "合理偏高", "停止加仓，只减不加"),
    (70.0, 101.0, "高估", "分批止盈，权益压到 3 成以下"),
]

# 分档前视收益的观察窗口（月）：回放里 12 个月的信号最干净
FORWARD_MONTHS = 12


def rebalance_hint(delta: Optional[float]) -> str:
    """环比变化的调仓提示：变化不足 5° 是噪音，达到 10° 才提示动一次"""
    if delta is None:
        return "暂无上月末参照点，不判断调仓"
    if abs(delta) >= REBALANCE_TRIGGER_DEG:
        return f"较上月变化 {delta:+.1f}°，超过 {REBALANCE_TRIGGER_DEG:.0f}° → 建议按目标权益仓调一次"
    if abs(delta) < REBALANCE_IGNORE_DEG:
        return f"较上月变化 {delta:+.1f}°，不足 {REBALANCE_IGNORE_DEG:.0f}° → 视为噪音，不动"
    return f"较上月变化 {delta:+.1f}°，未达 {REBALANCE_TRIGGER_DEG:.0f}° → 只观察，不调仓"


def format_temperature_line(data: Optional[dict]) -> Optional[str]:
    """推送用一行（与 push_service 里 市场环境 其余三行同格式，口径细节留给仪表盘）"""
    if not data:
        return None
    delta = data.get("delta_vs_prev_month")
    delta_part = "" if delta is None else f"，较上月 {delta:+.1f}°"
    return (
        f"- 市场温度计：{data['index']} **{data['temperature']}°（{data['zone']}）**"
        f"→ {data['action']}；参考权益仓位 {data['suggested_equity_pct']:.0f}%"
        f"{delta_part}（{data['date']} 读数，滚动 10 年 PE+PB 分位，仅市场层参考）"
    )


def zone_of(temperature: float) -> tuple[str, str]:
    """温度 → (档位名, 行动参考)"""
    for lo, hi, name, action in ZONE_RULES:
        if lo <= temperature < hi:
            return name, action
    return ZONE_RULES[-1][2], ZONE_RULES[-1][3]


def percentile_in_window(series: list[float], i: int, window: Optional[int]) -> Optional[float]:
    """第 i 期值在 point-in-time 窗口内的分位（0~100）；历史不足 MIN_WINDOW_MONTHS 时 None

    window=None 取扩展窗口（截至 i 的全部历史），否则取 [i-window+1, i]。
    """
    if i + 1 < MIN_WINDOW_MONTHS:
        return None
    lo = 0 if window is None else max(0, i - window + 1)
    rank = percentile_rank_inclusive(series[lo:i + 1], series[i])
    return None if rank is None else rank * 100.0


def monthly_temperatures(
    pe: list[float], pb: list[float], window: Optional[int] = ROLLING_WINDOW_MONTHS
) -> list[Optional[float]]:
    """逐月温度序列（point-in-time）；预热不足的月份为 None"""
    out: list[Optional[float]] = []
    for i in range(len(pe)):
        pe_pct = percentile_in_window(pe, i, window)
        pb_pct = percentile_in_window(pb, i, window)
        if pe_pct is None or pb_pct is None:
            out.append(None)
        else:
            out.append((pe_pct + pb_pct) / 2)
    return out


def forward_return_by_zone(
    temps: list[Optional[float]], close: list[float], forward_months: int = FORWARD_MONTHS
) -> list[dict]:
    """分档 → 其后 forward_months 个月指数收益分布（重叠 + 非重叠两套 n）

    重叠 n 会因为相邻样本互相共享未来区间而虚增显著性，所以必须同时给出**非重叠**
    （自有效起点起每 forward_months 个月取一个观测）的独立样本数：本序列只有 14 个，
    这也是温度计只能当行动参考、不能当自动信号的直接原因。
    """
    buckets: dict[str, dict[str, list[float]]] = {}
    for lo, hi, name, _ in ZONE_RULES:
        buckets[name] = {"overlap": [], "nonoverlap": []}
    first_valid = next((k for k, v in enumerate(temps) if v is not None), 0)
    for i, t in enumerate(temps):
        if t is None or i + forward_months >= len(close) or close[i] <= 0:
            continue
        fwd = (close[i + forward_months] / close[i] - 1) * 100
        name = zone_of(t)[0]
        buckets[name]["overlap"].append(fwd)
        # 非重叠：以有效起点为原点，按 forward_months 步长取样
        if i >= first_valid and (i - first_valid) % forward_months == 0:
            buckets[name]["nonoverlap"].append(fwd)
    rows = []
    for lo, hi, name, _ in ZONE_RULES:
        v = buckets[name]["overlap"]
        nv = buckets[name]["nonoverlap"]
        rows.append({
            "zone": name,
            "range": f"{lo:.0f}-{hi:.0f}" if hi <= 100 else f">{lo:.0f}",
            "n": len(v),
            "n_independent": len(nv),
            "avg_forward_12m_pct": round(sum(v) / len(v), 2) if v else None,
            "median_forward_12m_pct": round(sorted(v)[len(v) // 2], 2) if v else None,
            "win_rate_pct": round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else None,
            "worst_forward_12m_pct": round(min(v), 2) if v else None,
        })
    return rows


class MarketTemperatureService:
    """中证800 温度（月频序列日内一次足够，类级缓存 1h）"""

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
    async def get_temperature(cls, force: bool = False) -> Optional[dict]:
        now = time.time()
        if not force and cls._cache is not None and now - cls._ts < cls._TTL:
            return cls._cache
        if not force and now < cls._fail_until:
            return cls._cache

        def _fetch():
            import akshare as ak
            pe_df = ak.stock_index_pe_lg(symbol=TEMPERATURE_INDEX)
            pb_df = ak.stock_index_pb_lg(symbol=TEMPERATURE_INDEX)
            return pe_df, pb_df

        from backend.utils.concurrency import run_with_timeout

        try:
            pe_df, pb_df = await run_with_timeout(_fetch, timeout=90.0)
        except Exception as e:
            logger.warning(f"市场温度计取数失败: {e}")
            cls._fail_until = now + cls._FAIL_COOLDOWN
            return cls._cache

        series = _align_series(pe_df, pb_df)
        if series is None:
            # 接口通了但对不齐/全空：与失败同等对待，需要冷却而不是下个请求再打一遍
            cls._fail_until = now + cls._FAIL_COOLDOWN
            return cls._cache

        payload = _build_payload(series, now)
        cls._cache = payload
        cls._ts = now
        cls._fail_until = 0.0
        return payload


def _align_series(pe_df, pb_df) -> Optional[dict]:
    """PE 表（日期/指数/滚动市盈率）与 PB 表（日期/市净率）按日期内对齐

    两张表的最后一行同为"当月至今"，历史行为月末；内连接而不是 zip，
    避免任一表缺当月点时把分位算错位。
    """
    try:
        pe_rows = {
            str(r["日期"]): (float(r["指数"]), float(r["滚动市盈率"]))
            for _, r in pe_df.iterrows()
            if r.get("滚动市盈率") is not None and str(r.get("滚动市盈率")) != "nan"
        }
        pb_rows = {
            str(r["日期"]): float(r["市净率"])
            for _, r in pb_df.iterrows()
            if r.get("市净率") is not None and str(r.get("市净率")) != "nan"
        }
    except Exception as e:
        logger.warning(f"市场温度计列名不识别: {e}")
        return None
    dates = sorted(set(pe_rows) & set(pb_rows))
    if len(dates) < MIN_WINDOW_MONTHS + FORWARD_MONTHS:
        logger.warning(f"市场温度计样本不足：对齐后仅 {len(dates)} 个月点")
        return None
    return {
        "dates": dates,
        "close": [pe_rows[d][0] for d in dates],
        "pe": [pe_rows[d][1] for d in dates],
        "pb": [pb_rows[d] for d in dates],
    }


def _build_payload(series: dict, fetched_at: float) -> dict:
    """把对齐后的月频序列折算成展示用载荷

    _align_series 已保证样本 >= MIN_WINDOW_MONTHS + FORWARD_MONTHS，故这里末点温度必不为 None。
    """
    dates, close, pe, pb = series["dates"], series["close"], series["pe"], series["pb"]
    i = len(dates) - 1
    temps = monthly_temperatures(pe, pb)
    temps_expanding = monthly_temperatures(pe, pb, window=None)
    temperature = temps[i]

    zone_name, action = zone_of(temperature)
    suggested_equity = int(round(max(0.0, min(EQUITY_CEILING_PCT, EQUITY_CEILING_PCT - temperature))))

    # 环比：与上一月末点比（月频序列下这才是有意义的"变化"）
    prev_temp = temps[i - 1] if i >= 0 else None
    delta = None if prev_temp is None else round(temperature - prev_temp, 1)
    hint = rebalance_hint(delta)

    first_valid = next((k for k, v in enumerate(temps) if v is not None), None)
    pe_pct = percentile_in_window(pe, i, ROLLING_WINDOW_MONTHS)
    pb_pct = percentile_in_window(pb, i, ROLLING_WINDOW_MONTHS)
    zone_rows = forward_return_by_zone(temps, close)
    current_row = next((r for r in zone_rows if r["zone"] == zone_name), None)
    valid_from = dates[first_valid] if first_valid is not None else dates[0]
    # 当期读数真正的窗口起点：滚动 120 点在序列够长时就是 i-119
    window_from = dates[max(0, i - ROLLING_WINDOW_MONTHS + 1)]
    # 分档证据的最后一个可用月点：还要留出 forward_months 才有"其后收益"
    evidence_to = dates[len(dates) - 1 - FORWARD_MONTHS]

    return {
        "index": TEMPERATURE_INDEX,
        "date": dates[i],
        "close": round(close[i], 2),
        "pe": round(pe[i], 2),
        "pb": round(pb[i], 2),
        "pe_percentile": round(pe_pct, 1),
        "pb_percentile": round(pb_pct, 1),
        "temperature": round(temperature, 1),
        "temperature_expanding": None if temps_expanding[i] is None else round(temps_expanding[i], 1),
        "zone": zone_name,
        "action": action,
        "suggested_equity_pct": round(suggested_equity, 0),
        "delta_vs_prev_month": delta,
        "rebalance_hint": hint,
        "history_by_zone": zone_rows,
        "current_zone_evidence": current_row,
        "caliber": (
            f"滚动 {ROLLING_WINDOW_MONTHS} 个月末点分位 · (PE+PB)/2"
            f" · 窗口 {window_from} ~ {dates[i]}（当月至今）"
        ),
        "sample_note": (
            f"乐咕月频全序列 {len(dates)} 点（{dates[0]} ~ {dates[i]}）；"
            f"温度自 {valid_from} 起可算（{MIN_WINDOW_MONTHS} 点预热）；"
            f"分档证据要留 {FORWARD_MONTHS} 个月前瞻，最晚到 {evidence_to}"
        ),
        "note": "市场层行动参考，不参与单只基金买卖判定",
        "updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(fetched_at)),
    }
