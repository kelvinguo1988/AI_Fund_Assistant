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

格雷厄姆指数（股债性价比，2026-11-14 新增）:
- 公式 = 盈利收益率(100/PE) ÷ 中国 10Y 国债收益率 —— 引入温度计没有的**利率维度**。
- **阈值必须用自身历史分位，不能用绝对值分档**（如社区流传的">2.3 重仓"）：
  2026-11-14 实测，中 10Y 国债 1.61% 的利率环境下中证800 PE 13.69 算出 4.53
  （97% 分位）——国债 3% 时代同 PE 只算出 ≈2.4。绝对值分档随利率下行系统性漂移。
- **与温度计是双确认不是二选一**：温度(PE/PB 分位)量股票绝对估值，格雷厄姆量
  股债相对性价比。交叉四象限见 `_cross_status`——利率扭曲型"伪低估"必须点破，
  否则单看格雷厄姆 4.53 会误判"历史底部重仓"。
- 上游 +1 请求（bond_zh_us_rate，2014 起日频，月度取月末），国债缺失时格雷厄姆
  块整体降级（temperature 主块不受影响）。
失败不推进缓存时间戳会让每个页面请求都重打乐咕（前端无轮询但仪表盘/推送都会读），
把一次风控抖动放大成请求风暴。
"""

import logging
import time
from datetime import datetime
from typing import Optional

from backend.utils.stats import percentile_rank_inclusive
from backend.utils.timezone import format_beijing

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
# 格雷厄姆交叉状态的温度阈值（复用 ZONE_RULES 的 50 刀：50 以下视为便宜侧）
CROSS_TEMP_THRESHOLD = 50.0
CROSS_GRAHAM_THRESHOLD = 50.0  # 格雷厄姆自身 10 年分位阈值
GRAHAM_BOND_START = "20140101"  # 国债序列起点（乐咕 PE 序列 2007 起，交集决定格雷厄姆起点）

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


def graham_monthly_series(
    pe: list[float], bond_month_end: dict[str, float], dates: list[str]
) -> list[Optional[float]]:
    """格雷厄姆指数月频序列 = (100/PE) ÷ 10Y 国债收益率(%)，point-in-time

    bond_month_end: {YYYY-MM: 月末国债收益率%}（日频取月末）。
    该月无国债数据 → None（分位计算跳过）。
    """
    out: list[Optional[float]] = []
    for d, pe_v in zip(dates, pe):
        ym = d[:7]
        b = bond_month_end.get(ym)
        if not b or b <= 0 or not pe_v or pe_v <= 0:
            out.append(None)
        else:
            out.append((100.0 / pe_v) / b)
    return out


def _cross_status(temp_pct: Optional[float], graham_pct: Optional[float]) -> dict:
    """双指标交叉四象限（阈值各取 50 分位）

    温度量股票绝对估值，格雷厄姆量股债相对性价比——两者正交：
    双低=真低估；温度低+格雷厄姆高=利率扭曲型伪低估（2026-11-14 当前状态）；
    双高=真高估；温度高+格雷厄姆低=罕见（股票贵但利率更高），需人工复核。
    """
    if temp_pct is None or graham_pct is None:
        return {"state": None, "note": "任一指标缺失，交叉状态不可算"}
    temp_low = temp_pct < CROSS_TEMP_THRESHOLD
    graham_low = graham_pct < CROSS_GRAHAM_THRESHOLD
    if temp_low and graham_low:
        st = "true_cheap"
        note = "双指标同向低估——温度与股债性价比都处于低位，定投加码有双确认"
    elif temp_low and not graham_low:
        st = "rate_distorted_cheap"
        note = ("利率扭曲型伪低估——温度计显示便宜但股债性价比分位偏高，"
                "主因是国债收益率处于历史低位抬高了格雷厄姆读数；维持定投节奏，不建议因该读数加码")
    elif not temp_low and graham_low:
        st = "rare_review"
        note = "罕见组合——温度偏高但股债性价比处于低位（利率大幅上行），需人工复核"
    else:
        st = "true_expensive"
        note = "双指标同向偏高——温度与股债性价比都在高位，止盈/减投有双确认"
    return {"state": st, "note": note}


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


# ── 乐咕直连取数（2026-11-26 根因修复）─────────────────────────────
#
# akshare 的 stock_index_pe_lg / stock_index_pb_lg 各自内部调用
# get_cookie_csrf()，即**每个接口都去抓一次乐咕首页**拿 CSRF token。
# 取一次温度计 = 2 次首页请求（PE + PB），页面+定时任务并发时瞬间打 4~6 次，
# 直接撞上限流：首页返回 429/504 → soup.find 得 None → `.attrs` 崩
# （AttributeError），或 API 返回无 data 的错误体（KeyError 'data'）。
#
# 实测（2026-11-26 容器内）：
#   · 连续调用失败率 50%，并发 4 路必现失败
#   · 但首页间隔 8 秒就能连续成功 → 限流窗口是秒级，不是封禁
#   · 原始 API 本身一直正常（PE 39739 字节 / PB 18496 字节，data 字段都在）
#
# 修复：自己拿一次 cookie，PE 与 PB **共用**，把首页请求数从 2 降到 1；
# 并对 cookie 做进程内缓存（TTL 10 分钟），命中时首页请求数降到 0。
_CSRF_CACHE: dict[str, object] = {"ts": 0.0, "payload": None}
_CSRF_TTL = 600.0

# API 级节流（2026-11-26）：乐咕对 API 端点也限流，实测连打 3 次即 429。
# 正常路径下温度计 1 小时才取一次，节流只影响并发/重试场景。
_API_MIN_INTERVAL = 1.2          # 两次 API 调用最小间隔（秒）
_API_LAST_CALL: dict[str, float] = {"ts": 0.0}


def _legulegu_csrf(force: bool = False) -> dict:
    """取乐咕 CSRF（进程内缓存 10 分钟）

    首页是限流的瓶颈，缓存后正常路径下**完全不请求首页**。
    token 本身按日期算（hex(YYYY-MM-DD)），与 cookie 的有效期一致。
    """
    now = time.time()
    if not force and _CSRF_CACHE["payload"] is not None:
        if now - float(_CSRF_CACHE["ts"]) < _CSRF_TTL:  # type: ignore[arg-type]
            return _CSRF_CACHE["payload"]  # type: ignore[return-value]

    # 直接实现首页抓取，不调 akshare 的 get_cookie_csrf（2026-11-26）：
    # 它在首页被限流时 `soup.find(...)` 得 None 后直接 `.attrs` 崩，
    # 抛出的 AttributeError 对上层毫无信息量（既不像限流也不像网络错误）。
    # 这里自己解析，失败给出可辨识的异常，并支持重试。
    import re as _re

    import requests as _rq

    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36")
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            sess = _rq.Session()
            sess.headers.update({"User-Agent": ua})
            resp = sess.get("https://legulegu.com/stockdata/sz50-ttm-lyr", timeout=20)
            if resp.status_code == 429:
                last = RuntimeError("乐咕首页 HTTP 429（限流）")
                if attempt < 2:
                    time.sleep(4.0 if attempt == 0 else 10.0)
                    continue
                raise last
            resp.raise_for_status()
            m = _re.search(
                r'<meta[^>]+name=["\']_csrf["\'][^>]*content=["\']([^"\']+)["\']',
                resp.text,
            ) or _re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*name=["\']_csrf["\']',
                resp.text,
            )
            if not m:
                last = RuntimeError(
                    f"乐咕首页未返回 _csrf（HTTP {resp.status_code}, len={len(resp.text)}）"
                )
                if attempt < 2:
                    time.sleep(4.0 if attempt == 0 else 10.0)
                    continue
                raise last
            hdr = {"User-Agent": ua, "X-CSRF-Token": m.group(1)}
            ck = {"cookies": sess.cookies, "headers": hdr}
            _CSRF_CACHE["ts"] = now
            _CSRF_CACHE["payload"] = ck
            return ck
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < 2:
                time.sleep(4.0 if attempt == 0 else 10.0)
    raise last  # type: ignore[misc]


def _fetch_legulegu_pair(index_name: str):
    """一次 CSRF 同时取 PE 与 PB（把首页请求从 2 次降到 1 次/或 0 次）"""
    import pandas as pd
    import py_mini_racer
    import requests
    from akshare.stock_feature.stock_a_pe_and_pb import hash_code

    # 与 akshare 内部一致的 symbol → indexCode 映射
    symbol_map = {
        "上证50": "000016.SH", "沪深300": "000300.SH", "上证380": "000009.SH",
        "创业板50": "399673.SZ", "中证500": "000905.SH", "上证180": "000010.SH",
        "深证红利": "399324.SZ", "深证100": "399330.SZ", "中证1000": "000852.SH",
        "上证红利": "000015.SH", "中证100": "000903.SH", "中证800": "000906.SH",
    }
    code = symbol_map[index_name]

    js = py_mini_racer.MiniRacer()
    js.eval(hash_code)
    token = js.call("hex", datetime.now().date().isoformat()).lower()

    ck = _legulegu_csrf()

    def _get(kind: str) -> "pd.DataFrame":
        url = f"https://legulegu.com/api/stockdata/index-basic-{kind}"
        last: Optional[Exception] = None
        for attempt in range(3):
            # 节流：乐咕对 API 也限流（实测连打 3 次即 429）
            gap = time.time() - _API_LAST_CALL["ts"]
            if gap < _API_MIN_INTERVAL:
                time.sleep(_API_MIN_INTERVAL - gap)
            _API_LAST_CALL["ts"] = time.time()

            r = requests.get(url, params={"token": token, "indexCode": code},
                             timeout=25, **ck)
            if r.status_code == 429:
                last = RuntimeError("HTTP 429 Too Many Requests")
                # 429 是限流不是封禁：退避 3s / 8s 足够（实测窗口是秒级）
                if attempt < 2:
                    time.sleep(3.0 if attempt == 0 else 8.0)
                    continue
                raise last
            r.raise_for_status()
            j = r.json()
            if "data" not in j:
                # 「接口回错误体」与「cookie 过期」要能区分，上层据此刷新 cookie
                raise KeyError("data")
            return pd.DataFrame(j["data"])
        raise last  # type: ignore[misc]

    try:
        pe_raw = _get("pe")
    except (KeyError, ValueError):
        # cookie 可能过期 → 强制刷一次再试
        _legulegu_csrf(force=True)
        ck = _CSRF_CACHE["payload"]  # type: ignore[assignment]
        pe_raw = _get("pe")
    pb_raw = _get("pb")

    # 与 akshare 的**列名**保持一致（下游 _align_series 按中文列名取数）：
    # akshare 内部把乐咕的英文 key 重命名成中文，我们自己取数时必须补上这一步，
    # 否则 _align_series 找不到「日期/指数/滚动市盈率/市净率」→ 对齐后 0 个月点
    # （2026-11-26 踩到：日志显示「对齐后仅 0 个月点」，数据本身是好的）。
    pe_raw = pe_raw.rename(columns={
        "date": "日期",
        "close": "指数",
        "lyrPe": "静态市盈率",
        "addLyrPe": "等权静态市盈率",
        "middleLyrPe": "静态市盈率中位数",
        "ttmPe": "滚动市盈率",
        "addTtmPe": "等权滚动市盈率",
        "middleTtmPe": "滚动市盈率中位数",
    })
    pb_raw = pb_raw.rename(columns={
        "date": "日期",
        "close": "指数",
        "pb": "市净率",
        "addPb": "等权市净率",
        "middlePb": "市净率中位数",
    })

    # 日期处理与 akshare 一致（转 Asia/Shanghai 的 date 对象）
    for df in (pe_raw, pb_raw):
        df["日期"] = (
            pd.to_datetime(df["日期"], utc=True).dt.tz_convert("Asia/Shanghai").dt.date
        )
    return pe_raw, pb_raw


class MarketTemperatureService:
    """中证800 温度（月频序列日内一次足够，类级缓存 1h）"""

    _cache: Optional[dict] = None
    _ts: float = 0.0
    _TTL = 3600.0
    # 失败冷却（2026-11-21 排查）：原来固定 120s，而乐咕限流窗口远长于此
    # （实测首页 429 后 90s 恢复，但连续请求会再次触发），于是形成
    # 「失败 → 2 分钟后又打 → 又失败」的循环，6 天一次都没成功过，
    # 温度计永远停在旧值。改为按连续失败次数指数退避，上限 1h。
    _FAIL_COOLDOWN = 120.0
    _FAIL_COOLDOWN_MAX = 3600.0
    _fail_until: float = 0.0
    _fail_count: int = 0

    @classmethod
    def reset_cache(cls) -> None:
        cls._cache = None
        cls._ts = 0.0
        cls._fail_until = 0.0
        cls._fail_count = 0

    @classmethod
    def _mark_failure(cls, now: float) -> None:
        """记一次失败并指数退避（2026-11-21 根因修复）

        固定 120s 冷却会让「失败 → 2 分钟后又打 → 又失败」无限循环：
        乐咕的限流窗口比 120s 长，于是永远打不进去、缓存永远不更新。
        改为按连续失败次数退避：2min → 4min → 8min … 上限 1h。
        """
        cls._fail_count += 1
        cooldown = min(
            cls._FAIL_COOLDOWN * (2 ** (cls._fail_count - 1)),
            cls._FAIL_COOLDOWN_MAX,
        )
        cls._fail_until = now + cooldown
        logger.warning(
            f"温度计连续失败 {cls._fail_count} 次，冷却 {cooldown / 60:.0f} 分钟"
        )

    @classmethod
    async def get_temperature(cls, force: bool = False) -> Optional[dict]:
        now = time.time()
        if not force and cls._cache is not None and now - cls._ts < cls._TTL:
            return cls._cache
        if not force and now < cls._fail_until:
            return cls._cache

        def _fetch_with_retry():
            """乐咕取数带退避重试（2026-11-21 根因修复）

            根因：`ak.stock_index_pe_lg` 内部用 `get_cookie_csrf` 先抓乐咕首页
            拿 CSRF token。首页在限流窗口内返回 429/504，`soup.find(...)` 得到
            None 后直接 `.attrs` → AttributeError；或 API 返回无 data 的错误体
            → KeyError 'data'。akshare 自身**没有重试**，一次抖动就算整体失败。

            限流是短时的（实测 90s 后恢复），所以退避重试 3 次能覆盖绝大多数抖动，
            避免把一个瞬时 429 放大成「6 天拿不到新数据」。
            """
            import akshare as ak

            last_exc: Optional[Exception] = None
            for attempt in range(3):
                try:
                    pe_df, pb_df = _fetch_legulegu_pair(TEMPERATURE_INDEX)
                    bond_df = None
                    try:
                        # 格雷厄姆分母（中 10Y 国债日频，2014 起）；失败只降级格雷厄姆块
                        bond_df = ak.bond_zh_us_rate(start_date=GRAHAM_BOND_START)
                    except Exception as e:
                        logger.warning(f"国债收益率取数失败（格雷厄姆块降级）: {e}")
                    if attempt:
                        logger.info(f"乐咕取数第 {attempt + 1} 次尝试成功")
                    return pe_df, pb_df, bond_df
                except Exception as e:  # noqa: BLE001 - 上游各种形状都要兜住
                    last_exc = e
                    if attempt < 2:
                        # 退避：15s / 45s —— 实测乐咕限流窗口在秒级到十几秒，
                        # 8 秒间隔即可连续成功，所以不必等 20/60 那么久
                        delay = 15.0 if attempt == 0 else 45.0
                        logger.warning(
                            f"乐咕取数失败（第 {attempt + 1}/3 次）: "
                            f"{type(e).__name__}: {str(e)[:100]}；{delay:.0f}s 后重试"
                        )
                        time.sleep(delay)
            raise last_exc  # type: ignore[misc]

        from backend.utils.concurrency import run_with_timeout

        try:
            # 超时放宽到 300s：内部最多重试 3 次、退避共 80s，再留足请求时间
            pe_df, pb_df, bond_df = await run_with_timeout(_fetch_with_retry, timeout=300.0)
        except Exception as e:
            # 静默失败曾让首页温度计整块消失却无任何线索（2026-10-27 排查花了很久：
            # 接口 0.01s 返回 null，日志/错误页都查不到原因）。这里落一条错误日志，
            # 把异常类型与消息带到 /system/error-logs，便于定位是依赖缺失还是网络。
            cls._mark_failure(now)
            detail = f"{type(e).__name__}: {str(e)[:160]}"
            logger.warning(f"市场温度计取数失败: {detail}")
            try:
                from backend.services.error_log_service import log_source_failure
                log_source_failure(
                    module="temperature.leigu",
                    message="中证800 温度计取数失败（乐咕 PE/PB）",
                    category="dependency" if "racer" in detail.lower() or "import" in detail.lower()
                    else "network",
                    severity="warning",
                    detail=detail,
                )
            except Exception:
                pass
            return cls._cache

        series = _align_series(pe_df, pb_df)
        if series is None:
            # 接口通了但对不齐/全空：与失败同等对待，需要冷却而不是下个请求再打一遍
            cls._mark_failure(now)
            try:
                from backend.services.error_log_service import log_source_failure
                log_source_failure(
                    module="temperature.leigu",
                    message="中证800 温度计数据对不齐（PE/PB 月点无交集或样本不足）",
                    category="data_quality", severity="warning",
                    detail=f"pe_rows={len(pe_df) if pe_df is not None else 0}, "
                           f"pb_rows={len(pb_df) if pb_df is not None else 0}",
                )
            except Exception:
                pass
            return cls._cache

        payload = _build_payload(series, now)

        # ── 格雷厄姆块（bond 取数失败时整体降级，主温度不受影响）──
        try:
            bond_me = bond_month_end_map(bond_df) if bond_df is not None else {}
            graham_block = build_graham_block(series, bond_me)
            payload["graham"] = graham_block
            if graham_block.get("available"):
                _graham_cross_into(payload, graham_block)
        except Exception as e:
            logger.warning(f"格雷厄姆块构建失败（跳过）: {type(e).__name__}: {e}")

        cls._cache = payload
        cls._ts = now
        cls._fail_until = 0.0
        cls._fail_count = 0
        return payload


def bond_month_end_map(bond_df) -> dict[str, float]:
    """国债日频 → {YYYY-MM: 该月最后一个有效收益率%}（格雷厄姆月度分母）"""
    out: dict[str, float] = {}
    try:
        for _, r in bond_df.iterrows():
            d = str(r.get("日期") or "")[:10]
            v = r.get("中国国债收益率10年")
            if not d or v is None or str(v) == "nan":
                continue
            ym = d[:7]
            fv = float(v)
            if fv > 0:
                out[ym] = fv  # 接口按日期升序返回，末次覆盖即月末
    except Exception as e:
        logger.warning(f"国债序列列名不识别: {e}")
        return {}
    return out


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


def build_graham_block(series: dict, bond_me: dict[str, float]) -> dict:
    """格雷厄姆块：月频序列 → 当前值/10 年分位/交叉状态

    全部输入来自已对齐的 PE 月频序列与国债月末映射；国债缺月 → 该点 None
    （分位跳过）。纯函数，可单测。
    """
    dates, pe = series["dates"], series["pe"]
    graham = graham_monthly_series(pe, bond_me, dates)
    i = len(dates) - 1

    valid = [(k, g) for k, g in enumerate(graham) if g is not None]
    if len(valid) < MIN_WINDOW_MONTHS:
        return {
            "available": False,
            "note": f"格雷厄姆序列不足（{len(valid)}/{MIN_WINDOW_MONTHS} 个有效月点）",
        }

    current = graham[i]
    g_pct = percentile_in_window([g if g is not None else 0 for g in graham], i,
                                 ROLLING_WINDOW_MONTHS)
    # percentile_in_window 对 None 敏感——手写 point-in-time 分位更直接：
    window_vals = [g for g in graham[max(0, i - ROLLING_WINDOW_MONTHS + 1):i + 1]
                   if g is not None]
    if len(window_vals) >= MIN_WINDOW_MONTHS // 2 and current is not None:
        g_pct = sum(1 for v in window_vals if v <= current) / len(window_vals) * 100.0
    else:
        g_pct = None

    temp_pct = (series.get("pe_percentile_last"))
    # 温度分位从 payload 主块传入更干净——这里用 temps 的末值口径：
    # 交叉状态由调用方在 _build_payload 尾部注入（温度分位已知），此处只算格雷厄姆侧
    vals = [g for _, g in valid]
    graham_min, graham_max = min(vals), max(vals)
    graham_median = sorted(vals)[len(vals) // 2]

    return {
        "available": True,
        "current": None if current is None else round(current, 2),
        "percentile_10y": None if g_pct is None else round(g_pct, 1),
        "series_min": round(graham_min, 2),
        "series_max": round(graham_max, 2),
        "series_median": round(graham_median, 2),
        "series_points": len(valid),
        "bond_start": min(bond_me) if bond_me else None,
    }


def _staleness_fields(data_date, fetched_at: float) -> dict:
    """数据末点 vs 计算时刻的滞后信息（供前端提示「数据陈旧」）

    交易日历不做精确判断（节假日、停牌都算正常），只按自然日给出粗略滞后：
    > 7 天基本可确定上游停摆，而不是"今天没开市"。
    """
    try:
        from datetime import date, datetime

        if isinstance(data_date, str):
            d = datetime.strptime(data_date[:10], "%Y-%m-%d").date()
        elif isinstance(data_date, date):
            d = data_date
        else:
            return {"data_lag_days": None, "stale": False}
        lag = (datetime.fromtimestamp(fetched_at).date() - d).days
        return {"data_lag_days": lag, "stale": lag > 7}
    except Exception:  # noqa: BLE001 - 纯展示字段，算不出就不给
        return {"data_lag_days": None, "stale": False}


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
        "updated": format_beijing(fetched_at),
        # 数据陈旧度（2026-11-21 排查新增）：date 是**数据末点**，updated 是
        # **计算时刻**。两者相差过久说明上游一直失败、只是在返回旧缓存——
        # 原实现对此完全静默（温度计显示 11-13 而当天 11-21 也毫无提示），
        # 用户只能自己发现。这里显式给出滞后天数与失败次数，前端可据此提示。
        **_staleness_fields(dates[i], fetched_at),
    }

    # ── 格雷厄姆块 + 双指标交叉状态（graham 块由 get_temperature 注入后再算交叉）──
    return payload


def _graham_cross_into(payload: dict, graham_block: dict) -> None:
    """交叉状态需要温度分位（payload 已算出）与格雷厄姆分位——在两块齐备后调用"""
    g = graham_block
    temp_pct = payload.get("temperature")  # 温度本身即 (PE+PB)/2 分位
    cross = _cross_status(temp_pct, g.get("percentile_10y"))
    payload["graham"]["cross"] = cross
    payload["graham"]["temp_percentile"] = temp_pct
