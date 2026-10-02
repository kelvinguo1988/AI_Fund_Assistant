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
    score REAL
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

SELL_ACTIONS = {"sell", "moderate_sell", "heavy_sell"}
BUY_ACTIONS = {"buy", "moderate_buy", "heavy_buy"}


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
        for ddl in _OUTCOME_NEW_COLUMNS:
            try:
                self._conn.execute(f"ALTER TABLE advice_outcomes ADD COLUMN {ddl}")
            except sqlite3.OperationalError:
                pass  # 列已存在（新库由 _SCHEMA 直接建齐）
        self._conn.commit()

    def log_advice(self, fund_code: str, action: str,
                   reasons: str = "", score: Optional[float] = None,
                   dedupe_same_day: bool = True) -> Optional[int]:
        """工单落库；同一基金同一方向一天只记一条

        调仓页/Agent 任务/每日简报都会重算同一份四清单，不去重则 advice_log 会被
        同一天的重复样本灌满 —— 命中率会被同一笔"建议"反复计票。
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
            cur = self._conn.execute(
                "INSERT INTO advice_log (ts, fund_code, action, reasons, score) "
                "VALUES (?, ?, ?, ?, ?)",
                (now_beijing().strftime("%Y-%m-%d %H:%M:%S"),
                 fund_code, action, reasons[:500], score),
            )
            self._conn.commit()
            return cur.lastrowid

    def pending_evaluations(self, horizon_days: int = EVAL_HORIZON_NATURAL_DAYS) -> list[dict]:
        """到评估期但尚未回填的建议（自然日粗筛，严格窗口由 build_eval_window 判定）"""
        cutoff = (now_beijing() - timedelta(days=horizon_days)
                  ).strftime("%Y-%m-%d %H:%M:%S")
        with self._w:
            rows = self._conn.execute(
                "SELECT a.id, a.fund_code, a.action, a.ts FROM advice_log a "
                "LEFT JOIN advice_outcomes o ON o.advice_id = a.id "
                "WHERE a.ts <= ? AND o.advice_id IS NULL "
                "ORDER BY a.id", (cutoff,)
            ).fetchall()
        return [
            {"advice_id": r[0], "fund_code": r[1], "action": r[2], "ts": r[3]}
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

    def record_outcome(self, advice_id: int, action: str, fund_change: float,
                       bench_change: float, eval_date: Optional[str] = None) -> dict:
        """双口径同时落库（Q7-C）：hit_abs / hit_excess，hit 列按当前口径

        eval_date 传评估窗口**结束日**（净值序列的最后一点），而不是回填动作发生的
        时间 —— 否则周末补跑会让一批样本的评估日期挤在同一天，看不出窗口。
        """
        mode = self.get_hit_mode()
        hit_abs = judge_hit(action, fund_change, bench_change, "abs")
        hit_excess = judge_hit(action, fund_change, bench_change, "excess")
        hit = hit_abs if mode == "abs" else hit_excess
        with self._w:
            self._conn.execute(
                "INSERT OR REPLACE INTO advice_outcomes "
                "(advice_id, eval_date, fund_change_pct, benchmark_change_pct, "
                " hit, hit_abs, hit_excess) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (advice_id, (eval_date or now_beijing().strftime("%Y-%m-%d %H:%M:%S")),
                 round(fund_change, 3), round(bench_change, 3),
                 hit, hit_abs, hit_excess),
            )
            self._conn.commit()
        return {"hit": hit, "hit_abs": hit_abs, "hit_excess": hit_excess, "mode": mode}

    def stats(self, window_days: Optional[int] = None) -> dict:
        """命中率统计（双口径并存）

        Args:
            window_days: 只统计该天数内发出的建议；None = 全量累计（UI 口径）
        """
        cutoff = (now_beijing() - timedelta(days=window_days)
                  ).strftime("%Y-%m-%d %H:%M:%S") if window_days else None
        with self._w:
            if cutoff:
                rows = self._conn.execute(
                    "SELECT a.action, o.hit, o.fund_change_pct, o.hit_abs, o.hit_excess "
                    "FROM advice_outcomes o JOIN advice_log a ON a.id = o.advice_id "
                    "WHERE a.ts >= ?", (cutoff,)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT a.action, o.hit, o.fund_change_pct, o.hit_abs, o.hit_excess "
                    "FROM advice_outcomes o JOIN advice_log a ON a.id = o.advice_id").fetchall()
            total = len(rows)
            cal = {r[0]: r[1] for r in self._conn.execute(
                "SELECT key, value FROM advice_calibration")}
        sell = [r for r in rows if r[0] in SELL_ACTIONS]
        buy = [r for r in rows if r[0] in BUY_ACTIONS]

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
    """
    import asyncio
    import random

    from backend.utils.timezone import beijing_today

    store = store or AdviceLearningStore()
    pending = store.pending_evaluations()
    if not pending:
        return {"evaluated": 0, "skipped_window_incomplete": 0,
                "benchmark_missing": 0, "pending": 0, "calibration": store.calibrate()}

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

    evaluated = immature = bench_missing = 0
    for item in pending:
        win = build_eval_window(series_by_code.get(item["fund_code"]) or [], item["ts"][:10])
        if win is None:
            immature += 1
            continue
        bench_chg = window_change_pct(bench_series, win["base_date"], win["end_date"])
        if bench_chg is None:
            bench_missing += 1
            bench_chg = 0.0
        store.record_outcome(
            item["advice_id"], item["action"],
            win["change_pct"], bench_chg, eval_date=win["end_date"],
        )
        evaluated += 1

    out = {"evaluated": evaluated, "skipped_window_incomplete": immature,
           "benchmark_missing": bench_missing, "pending": len(pending),
           "funds_fetched": len(series_by_code), "calibration": store.calibrate()}
    logger.info(
        f"建议回填：判定 {evaluated}/{len(pending)} 条，"
        f"窗口未走完 {immature} 条，基准缺失 {bench_missing} 条"
    )
    return out
