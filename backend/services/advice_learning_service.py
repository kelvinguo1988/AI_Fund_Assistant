"""调仓建议自进化闭环 — 建议落库 → 30 交易日回填 → 双口径命中率 → 阈值校准

借鉴 fundadvisor learning_engine（2026-09-24 用户确认做成系统原生 Skill）：
- advice_log: 调仓引擎产出的工单（卖出/买入候选）落库
- 评估窗口（Q7-B）：建议日 → 其后第 EVAL_HORIZON_TRADING_DAYS 个**净值日**。
  旧实现按"建议日 → 最新净值日"取首末点，同一批样本窗口长度从 30 到 90 天不等，
  窗口未走完的样本直接跳过、留待下一轮，基准取同一区间。
- 命中判定（Q7-A/C 双口径并存，都落库）：
    hit_abs    绝对涨跌：sell 跌=命中、buy 涨=命中 —— 上涨市里几乎恒真，量的是 beta
    hit_excess 相对沪深300 同区间超额涨跌：sell 跑输=命中、buy 跑赢=命中 —— 量的是选基
  `hit` 列按 system_config.advice_hit_mode 落库（默认 excess）；把该键改回 abs
  即整条链回到旧口径，无需改代码。
- **基准只对 A 股股权类可比**（2026-10-17 审查 #2）：`advice_log.benchmark_kind`
  记录该只基金的基准口径（`hs300_excess` / `abs`，由 `utils/fund_asset_class` 判定）。
  海外/QDII、债券、商品基金的 `hit_excess` **不落值（NULL = 未判定）**、`hit` 一律取
  绝对口径 —— 拿沪深300 量一只纳指基金，量到的是"美股是否跑赢 A 股"，债基在牛市里
  "卖出必命中"，两类都会顺着 `calibrate()` 把止盈/止损线推向市场方向而不是选基能力。
- 校准：按近窗口**当前口径**命中率微调止盈/止损建议阈值（保守上下限约束）

存储复用 error_logs 模式的独立 sqlite3 直连（线程安全 + 节流不适用，
advice_log 一条一记录）。
"""

import logging
import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from backend.utils.fund_asset_class import BENCHMARK_ABS, BENCHMARK_HS300_EXCESS
from backend.utils.timezone import now_beijing

logger = logging.getLogger(__name__)

EVAL_HORIZON_TRADING_DAYS = 30
# SQL 粗筛用的自然日下限：30 交易日 ≈ 42 自然日，再留节假日余量。
# 未成熟的样本根本不入选，就不会每一轮都把它们的净值序列重新拉一遍（少打上游接口）。
EVAL_HORIZON_NATURAL_DAYS = 46
# 校准只看近窗口样本：全量累计下旧样本永不退出，命中率一旦好转阈值也回不去
CALIBRATION_WINDOW_DAYS = 90
# 5 条样本的命中率标准差约 0.22，不足以判断阈值好坏
CALIBRATION_MIN_SAMPLES = 30
CALIBRATION_STEP = 5.0
LOW_HIT_RATE = 0.5
HIGH_HIT_RATE = 0.6
PARAM_BOUNDS = {
    "profit_take_pct": (15.0, 50.0),
    "stop_loss_pct": (-30.0, -10.0),
}
DEFAULT_PARAMS = {"profit_take_pct": 30.0, "stop_loss_pct": -20.0}

# 命中口径开关（Q7 回滚路径）：system_config KV 表，abs = 2026-10 之前的旧口径
HIT_MODE_KEY = "advice_hit_mode"
HIT_MODES = ("abs", "excess")
DEFAULT_HIT_MODE = "excess"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS advice_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    action TEXT NOT NULL,
    reasons TEXT,
    score REAL,
    benchmark_kind TEXT
);
CREATE TABLE IF NOT EXISTS advice_outcomes (
    advice_id INTEGER PRIMARY KEY,
    eval_date TEXT,
    fund_change_pct REAL,
    benchmark_change_pct REAL,
    hit INTEGER,
    hit_abs INTEGER,
    hit_excess INTEGER
);
CREATE TABLE IF NOT EXISTS advice_calibration (
    key TEXT PRIMARY KEY,
    value REAL,
    updated_at TEXT
);
"""

# 存量库补列（CREATE TABLE IF NOT EXISTS 不会给已存在的表加列）。
# 现网 advice_outcomes 为 0 行，NULL 只可能出现在改造前的历史行，统计时按"未判定"排除。
_OUTCOME_NEW_COLUMNS = ("hit_abs INTEGER", "hit_excess INTEGER")
# advice_log.benchmark_kind（审查 #2）：NULL = 改造前的行，按"基准可比"处理
_LOG_NEW_COLUMNS = ("benchmark_kind TEXT",)

# (表, 该表要补的列) —— 必须按表配对：早先版本把两张表的列都往 advice_outcomes 上 ALTER，
# 于是新库（_SCHEMA 直接建齐）一切正常、只有存量库在 `stats()` 里炸 "no such column"。
_MIGRATIONS = (("advice_outcomes", _OUTCOME_NEW_COLUMNS), ("advice_log", _LOG_NEW_COLUMNS))


def migrate_added_columns(conn: sqlite3.Connection) -> list[str]:
    """给存量库补齐新增列，返回本次真正加上的列（已存在则跳过）。"""
    added: list[str] = []
    for table, columns in _MIGRATIONS:
        for ddl in columns:
            name = ddl.split()[0]
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
            except sqlite3.OperationalError:
                if name not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
                    raise          # 列真不在 —— 别把 SQL 错误咽成"已存在"
            else:
                added.append(f"{table}.{name}")
    conn.commit()
    return added


SELL_ACTIONS = {"sell", "moderate_sell", "heavy_sell"}
BUY_ACTIONS = {"buy", "moderate_buy", "heavy_buy"}


def resolve_benchmark_kind(conn: sqlite3.Connection, fund_code: str) -> str:
    """该基金代码应用的基准口径（读同库 `funds` 表，零上游请求）

    代码查不到（临时测试库没有 funds 表、已删除的基金）一律按 `hs300_excess` 返回
    —— 保持改造前口径，而不是静默把样本从超额统计里摘掉。
    """
    from backend.utils.fund_asset_class import benchmark_kind, classify_asset

    try:
        row = conn.execute(
            "SELECT name, fund_type_official, tags, code FROM funds WHERE code = ? LIMIT 1",
            (str(fund_code or ""),)).fetchone()
    except sqlite3.OperationalError:
        return BENCHMARK_HS300_EXCESS
    if row is None:
        return BENCHMARK_HS300_EXCESS
    return benchmark_kind(classify_asset(row[0], row[1], row[2], row[3]))


def judge_hit(action: str, fund_change: float, bench_change: float,
              mode: str = DEFAULT_HIT_MODE) -> int:
    """命中判定的唯一出口：abs=绝对涨跌，excess=相对基准超额（Q7 默认）

    未知 action 记 0（stats 的分桶只统计 sell/buy 两类，不影响命中率）。
    """
    if action in SELL_ACTIONS:
        sign = -1.0
    elif action in BUY_ACTIONS:
        sign = 1.0
    else:
        return 0
    chg = fund_change if mode == "abs" else fund_change - bench_change
    return 1 if sign * chg > 0 else 0


def build_eval_window(nav_series: list[tuple[str, float]], advice_date: str,
                      trading_days: int = EVAL_HORIZON_TRADING_DAYS) -> Optional[dict]:
    """建议日 → 其后第 trading_days 个净值日；窗口未走完返回 None

    起点取建议日当天（含）之前最后一个净值日：场外净值 T 日盘中未公布，
    以已公布的最近净值为基线才是"当时能看到的价格"。
    """
    if not nav_series or not advice_date:
        return None
    series = sorted(nav_series)
    base_idx = None
    for i, (d, _v) in enumerate(series):
        if d[:10] <= advice_date[:10]:
            base_idx = i
        else:
            break
    if base_idx is None:
        # 建议日早于序列起点：基线净值缺失，无法判定涨跌，交回调用方跳过
        return None
    end_idx = base_idx + trading_days
    if end_idx >= len(series):
        return None
    base_date, base_nav = series[base_idx]
    end_date, end_nav = series[end_idx]
    if not base_nav:
        return None
    return {
        "base_date": base_date[:10],
        "end_date": end_date[:10],
        "trading_days": end_idx - base_idx,
        "change_pct": (end_nav / base_nav - 1) * 100,
    }


def window_change_pct(series: list[tuple[str, float]],
                      base_date: str, end_date: str) -> Optional[float]:
    """同区间首末点涨跌（基准与基金对齐同一日期区间；点不足返回 None）"""
    pts = sorted((d, v) for d, v in (series or []) if base_date <= d[:10] <= end_date)
    if len(pts) < 2 or not pts[0][1]:
        return None
    return (pts[-1][1] / pts[0][1] - 1) * 100



class AdviceLearningStore:
    _instance: Optional["AdviceLearningStore"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
        return cls._instance

    def _init(self):
        self._w = threading.Lock()
        try:
            from backend.config import settings
            db_path = Path(settings.DATABASE_DIR) / settings.DATABASE_NAME
        except Exception:
            db_path = Path("data") / "fund_quant.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # busy_timeout 与 async engine 的 connect_args={"timeout": 30} 对齐：
        # 直连只有 15s 时，全量分析写库期间本服务的回填写入会先一步放弃并报
        # database is locked（先超时的总是短的那方）
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
        self._conn.executescript(_SCHEMA)
        migrate_added_columns(self._conn)

    def log_advice(self, fund_code: str, action: str,
                   reasons: str = "", score: Optional[float] = None,
                   dedupe_same_day: bool = True,
                   benchmark_kind: Optional[str] = None) -> Optional[int]:
        """工单落库；同一基金同一方向一天只记一条

        调仓页/Agent 任务/每日简报都会重算同一份四清单，不去重则 advice_log 会被
        同一天的重复样本灌满 —— 命中率会被同一笔"建议"反复计票。

        `benchmark_kind` 缺省时读同库 `funds` 表自行判定（审查 #2）：口径由落库那刻
        锁死，事后改基金名称/类型不会让历史样本的判定口径漂移。
        """
        with self._w:
            if dedupe_same_day:
                today = now_beijing().strftime("%Y-%m-%d")
                exists = self._conn.execute(
                    "SELECT 1 FROM advice_log WHERE fund_code = ? AND action = ? "
                    "AND substr(ts, 1, 10) = ? LIMIT 1",
                    (fund_code, action, today)).fetchone()
                if exists:
                    return None
            kind = benchmark_kind or resolve_benchmark_kind(self._conn, fund_code)
            cur = self._conn.execute(
                "INSERT INTO advice_log (ts, fund_code, action, reasons, score, benchmark_kind) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now_beijing().strftime("%Y-%m-%d %H:%M:%S"),
                 fund_code, action, reasons[:500], score, kind),
            )
            self._conn.commit()
            return cur.lastrowid

    def pending_evaluations(self, horizon_days: int = EVAL_HORIZON_NATURAL_DAYS) -> list[dict]:
        """到评估期但尚未回填的建议（自然日粗筛，严格窗口由 build_eval_window 判定）"""
        cutoff = (now_beijing() - timedelta(days=horizon_days)
                  ).strftime("%Y-%m-%d %H:%M:%S")
        with self._w:
            rows = self._conn.execute(
                "SELECT a.id, a.fund_code, a.action, a.ts, a.benchmark_kind FROM advice_log a "
                "LEFT JOIN advice_outcomes o ON o.advice_id = a.id "
                "WHERE a.ts <= ? AND o.advice_id IS NULL "
                "ORDER BY a.id", (cutoff,)
            ).fetchall()
        return [
            {"advice_id": r[0], "fund_code": r[1], "action": r[2], "ts": r[3],
             "benchmark_kind": r[4]}
            for r in rows
        ]

    def get_hit_mode(self) -> str:
        """当前命中口径（system_config.advice_hit_mode，脏值/缺行一律回落到默认）"""
        try:
            row = self._conn.execute(
                "SELECT config_value FROM system_config WHERE config_key = ?",
                (HIT_MODE_KEY,)).fetchone()
        except sqlite3.OperationalError as e:
            logger.warning(f"读命中口径失败（按 {DEFAULT_HIT_MODE} 处理）: {e}")
            return DEFAULT_HIT_MODE
        value = (row[0] or "").strip().lower() if row else ""
        return value if value in HIT_MODES else DEFAULT_HIT_MODE

    def set_hit_mode(self, mode: str) -> str:
        m = (mode or "").strip().lower()
        if m not in HIT_MODES:
            raise ValueError(f"命中口径只能是 {HIT_MODES}，收到 {mode!r}")
        now = now_beijing().strftime("%Y-%m-%d %H:%M:%S")
        with self._w:
            updated = self._conn.execute(
                "UPDATE system_config SET config_value = ?, updated_at = ? "
                "WHERE config_key = ?", (m, now, HIT_MODE_KEY)).rowcount
            if not updated:
                self._conn.execute(
                    "INSERT INTO system_config (config_key, config_value, description, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (HIT_MODE_KEY, m, "调仓建议命中口径：excess=相对沪深300超额 / abs=绝对涨跌(旧)", now))
            self._conn.commit()
        return m

    def _kind_of(self, advice_id: int) -> str:
        """按 advice_id 反查基准口径；NULL/查不到 = 改造前语义（基准可比）"""
        try:
            row = self._conn.execute(
                "SELECT benchmark_kind FROM advice_log WHERE id = ?", (advice_id,)).fetchone()
        except sqlite3.OperationalError:
            return BENCHMARK_HS300_EXCESS
        return (row[0] if row and row[0] else BENCHMARK_HS300_EXCESS)

    def benchmark_kind_for(self, fund_code: str) -> str:
        """按代码现算基准口径（回填遇到改造前的 NULL 行时用；读同库 funds 表，零上游请求）"""
        return resolve_benchmark_kind(self._conn, fund_code)

    def record_outcome(self, advice_id: int, action: str, fund_change: float,
                       bench_change: Optional[float], eval_date: Optional[str] = None,
                       benchmark_kind: Optional[str] = None) -> dict:
        """双口径落库（Q7-C）：hit_abs / hit_excess，hit 列按当前口径

        eval_date 传评估窗口**结束日**（净值序列的最后一点），而不是回填动作发生的
        时间 —— 否则周末补跑会让一批样本的评估日期挤在同一天，看不出窗口。

        `benchmark_kind == abs`（海外/债券/商品等与沪深300 不可比的标的，审查 #2）时
        `hit_excess` **留 NULL 而不是写一个错值**：那一栏量的是"这只纳指基金跑没跑赢
        沪深300"，把它计进分母等于让市场相关性冒充选基能力，而 `calibrate()` 正是对着
        这个分母挪止损/止盈线的。此类样本的 `hit` 一律取绝对口径。
        """
        mode = self.get_hit_mode()
        kind = benchmark_kind or self._kind_of(advice_id)
        # 缺基准时同样不写错值：那一栏量的是"没比"，退化成绝对口径而不是"与 0% 比"
        comparable = kind != BENCHMARK_ABS and bench_change is not None
        hit_abs = judge_hit(action, fund_change, bench_change, "abs")
        hit_excess = judge_hit(action, fund_change, bench_change, "excess") if comparable else None
        hit = hit_abs if (not comparable or mode == "abs") else hit_excess
        bench_val = None if bench_change is None else round(float(bench_change), 3)
        with self._w:
            self._conn.execute(
                "INSERT OR REPLACE INTO advice_outcomes "
                "(advice_id, eval_date, fund_change_pct, benchmark_change_pct, "
                " hit, hit_abs, hit_excess) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (advice_id, (eval_date or now_beijing().strftime("%Y-%m-%d %H:%M:%S")),
                 round(fund_change, 3), bench_val,
                 hit, hit_abs, hit_excess),
            )
            self._conn.commit()
        return {"hit": hit, "hit_abs": hit_abs, "hit_excess": hit_excess,
                "mode": mode, "benchmark_kind": kind}

    def stats(self, window_days: Optional[int] = None) -> dict:
        """命中率统计（双口径并存）

        Args:
            window_days: 只统计该天数内发出的建议；None = 全量累计（UI 口径）

        超额口径的分母只含"基准可比"的样本（`hit_excess` 为 NULL 的海外/债券/商品
        行不计，见 `record_outcome`），`by_kind` 给出被摘掉多少 —— 让人一眼看出
        这个命中率是拿什么尺子量的。
        """
        cutoff = (now_beijing() - timedelta(days=window_days)
                  ).strftime("%Y-%m-%d %H:%M:%S") if window_days else None
        with self._w:
            if cutoff:
                rows = self._conn.execute(
                    "SELECT a.action, o.hit, o.fund_change_pct, o.hit_abs, o.hit_excess, "
                    "       a.benchmark_kind "
                    "FROM advice_outcomes o JOIN advice_log a ON a.id = o.advice_id "
                    "WHERE a.ts >= ?", (cutoff,)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT a.action, o.hit, o.fund_change_pct, o.hit_abs, o.hit_excess, "
                    "       a.benchmark_kind "
                    "FROM advice_outcomes o JOIN advice_log a ON a.id = o.advice_id").fetchall()
            total = len(rows)
            cal = {r[0]: r[1] for r in self._conn.execute(
                "SELECT key, value FROM advice_calibration")}
        sell = [r for r in rows if r[0] in SELL_ACTIONS]
        buy = [r for r in rows if r[0] in BUY_ACTIONS]
        abs_only = sum(1 for r in rows if r[5] == BENCHMARK_ABS)

        # 改造前写入的历史行 abs/excess 为 NULL，计入 evaluated 但不计入该口径分母
        def _side(items: list, col: int) -> dict:
            scored = [r for r in items if r[col] is not None]
            return {"total": len(scored), "hits": sum(1 for r in scored if r[col] == 1)}

        return {
            "evaluated": total,
            "window_days": window_days,
            "hit_mode": self.get_hit_mode(),
            "sell_total": len(sell),
            "sell_hits": sum(1 for r in sell if r[1] == 1),
            "buy_total": len(buy),
            "buy_hits": sum(1 for r in buy if r[1] == 1),
            "by_mode": {
                "abs": {"sell": _side(sell, 3), "buy": _side(buy, 3)},
                "excess": {"sell": _side(sell, 4), "buy": _side(buy, 4)},
            },
            "by_kind": {
                "hs300_comparable": total - abs_only,
                "abs_only": abs_only,
            },
            "params": cal or DEFAULT_PARAMS,
        }

    def calibrate(self) -> dict:
        """近窗口命中率低于 50% → 收紧阈值；高于 60% → 向默认值回退一档

        2026-09-29 审查 P1：原实现用全量累计样本且门槛只有 5 条 —— 5 条样本的
        命中率标准差约 0.22，等于对着噪声调参；而累计口径下旧样本永不退出，
        阈值只会单向漂到上下限（棘轮效应）。改为近 CALIBRATION_WINDOW_DAYS 天、
        至少 CALIBRATION_MIN_SAMPLES 条，并允许命中率好转时回退。

        2026-10-02 Q7：命中率取**当前口径**（默认 excess 超额），旧口径把上涨市里
        近乎恒真的 buy 绝对命中当成能力，止损/止盈线被单侧推动。
        """
        st = self.stats(window_days=CALIBRATION_WINDOW_DAYS)
        side = st["by_mode"][st["hit_mode"]]
        out = {"adjusted": False, "changes": [], "window_days": CALIBRATION_WINDOW_DAYS,
               "hit_mode": st["hit_mode"], "sample_basis": st["evaluated"]}
        with self._w:
            for key, bound in PARAM_BOUNDS.items():
                cur = self._conn.execute(
                    "SELECT value FROM advice_calibration WHERE key = ?",
                    (key,)).fetchone()
                cur_val = cur[0] if cur else DEFAULT_PARAMS[key]
                # sell 命中差 → 止损线向 -10 收紧；buy 命中差 → 止盈线向 15 收紧
                new_val = cur_val
                if key == "stop_loss_pct" and side["sell"]["total"] >= CALIBRATION_MIN_SAMPLES:
                    hit = side["sell"]["hits"] / side["sell"]["total"]
                    if hit < LOW_HIT_RATE:
                        new_val = min(cur_val + CALIBRATION_STEP, bound[1])
                    elif hit > HIGH_HIT_RATE:
                        new_val = min(cur_val + CALIBRATION_STEP * 0.5, DEFAULT_PARAMS[key])
                if key == "profit_take_pct" and side["buy"]["total"] >= CALIBRATION_MIN_SAMPLES:
                    hit = side["buy"]["hits"] / side["buy"]["total"]
                    if hit < LOW_HIT_RATE:
                        new_val = max(cur_val - CALIBRATION_STEP, bound[0])
                    elif hit > HIGH_HIT_RATE:
                        new_val = max(cur_val - CALIBRATION_STEP * 0.5, DEFAULT_PARAMS[key])
                if new_val != cur_val:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO advice_calibration "
                        "(key, value, updated_at) VALUES (?, ?, ?)",
                        (key, round(new_val, 2),
                         now_beijing().strftime("%Y-%m-%d %H:%M:%S")))
                    out["changes"].append({"key": key, "from": cur_val, "to": round(new_val, 2)})
                    out["adjusted"] = True
            self._conn.commit()
        return out


# 回填逐只取净值的防封间隔（秒）：无间隔的连续请求正是限流的典型触发形态
BACKFILL_SLEEP_RANGE = (10.0, 30.0)


async def run_advice_backfill(store: Optional["AdviceLearningStore"] = None) -> dict:
    """到期建议回填 + 校准（Q7 口径）：手动 POST 与周度调度共用同一份逻辑

    上游预算：无到期样本 → 0 请求；有到期样本才取基准（adapter 类级 1h 缓存，
    命中即零请求），净值按**基金去重**后每只取一次（同一基金多条建议共用一条序列），
    只与只之间随机 sleep。窗口未走完的样本本轮不判定，下周再取一次。

    收益口径（Q11，与复盘共用 `caliber_service`）：fund_change 取分红复权净值
    （分红不再被记成下跌），bench_change 取沪深300 价格指数 + 按区间交易日折算的
    股息 —— 两侧同口径，超额命中才只反映选基能力。

    基准可比性（审查 #2）：每只基金的口径由 `advice_log.benchmark_kind` 决定（缺省时
    读同库 `funds` 表现算，零上游请求）。不可比的样本**根本不取基准**，
    `hit_excess` 留 NULL、`hit` 走绝对口径 —— 于是 `stats()/calibrate()` 的超额分母
    自动只剩 A 股股权类。
    """
    import asyncio
    import random

    from backend.utils.timezone import beijing_today

    store = store or AdviceLearningStore()
    pending = store.pending_evaluations()
    if not pending:
        return {"evaluated": 0, "skipped_window_incomplete": 0,
                "benchmark_missing": 0, "abs_only_evaluated": 0,
                "pending": 0, "calibration": store.calibrate()}

    from backend.data_sources.akshare_adapter import AKShareAdapter
    from backend.services.caliber_service import load_caliber, with_dividend_carry
    from backend.services.review_service import _fetch_nav_series

    # 一轮一个口径：回填逐只做，中途改配置会让同批样本不可比
    policy = await load_caliber()

    adapter = AKShareAdapter()
    bench_series: list[tuple[str, float]] = []
    try:
        bench_series = with_dividend_carry(
            await adapter.get_benchmark_series(), policy["bench_div_yield_pct"])
    except Exception as e:
        logger.warning(f"建议回填取基准失败（本轮超额口径按 0 处理）: {type(e).__name__}: {e}")

    # 每只基金取最早的建议日：一次请求覆盖该基金全部到期样本的窗口
    first_date: dict[str, str] = {}
    for item in pending:
        code, d = item["fund_code"], item["ts"][:10]
        if code not in first_date or d < first_date[code]:
            first_date[code] = d

    series_by_code: dict[str, list[tuple[str, float]]] = {}
    for i, (code, start) in enumerate(sorted(first_date.items())):
        if i:
            await asyncio.sleep(random.uniform(*BACKFILL_SLEEP_RANGE))
        # cutoff 由 _fetch_nav_series 锚定 start 再前推 60 天（取基线净值）；
        # days 只决定场外兜底链路的拉取长度，需覆盖建议日距今的全程
        need = max((beijing_today() - date.fromisoformat(start)).days + 10,
                   EVAL_HORIZON_TRADING_DAYS + 20)
        try:
            series_by_code[code] = await _fetch_nav_series(
                adapter, code, need, start, adjusted=policy["nav_adjusted"])
        except Exception as e:
            logger.warning(f"建议回填取净值失败 {code}: {type(e).__name__}: {e}")
            series_by_code[code] = []

    evaluated = immature = bench_missing = abs_only = 0
    for item in pending:
        win = build_eval_window(series_by_code.get(item["fund_code"]) or [], item["ts"][:10])
        if win is None:
            immature += 1
            continue
        kind = item.get("benchmark_kind") or store.benchmark_kind_for(item["fund_code"])
        # 基准只给"可比"样本取（沪深300 对债基/海外基金没有意义）；可比但缺基准时按 0
        # 会让超额判定退化成绝对涨跌 —— 这类计数单列，不与 abs_only（刻意不比）混为一谈
        if kind != BENCHMARK_ABS:
            bench_chg = window_change_pct(bench_series, win["base_date"], win["end_date"])
            if bench_chg is None:
                bench_missing += 1
                bench_chg = 0.0
        else:
            # 不可比样本不记基准涨跌：落 NULL，免得报表把"没比"显示成"与 0% 比"
            bench_chg = None
            abs_only += 1
        store.record_outcome(
            item["advice_id"], item["action"],
            win["change_pct"], bench_chg, eval_date=win["end_date"],
            benchmark_kind=kind,
        )
        evaluated += 1

    out = {"evaluated": evaluated, "skipped_window_incomplete": immature,
           "benchmark_missing": bench_missing, "pending": len(pending),
           "abs_only_evaluated": abs_only,
           "funds_fetched": len(series_by_code), "calibration": store.calibrate()}
    logger.info(
        f"建议回填：判定 {evaluated}/{len(pending)} 条，"
        f"窗口未走完 {immature} 条，基准缺失 {bench_missing} 条，"
        f"基准不可比按绝对口径 {abs_only} 条"
    )
    return out
