"""调仓建议自进化闭环 — 建议落库 → 30 天回填 → 命中率 → 阈值校准

借鉴 fundadvisor learning_engine（2026-09-24 用户确认做成系统原生 Skill）：
- advice_log: 调仓引擎产出的工单（卖出/买入候选）落库
- 评估窗口 30 天：回填窗口内基金实际涨跌与基准差
- 命中判定：sell 类建议在窗口跌 = 命中；buy 类涨 = 命中
- 校准：按命中统计微调止盈/止损建议阈值（保守上下限约束）

存储复用 error_logs 模式的独立 sqlite3 直连（线程安全 + 节流不适用，
advice_log 一条一记录）。
"""

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

EVAL_HORIZON_DAYS = 30
PARAM_BOUNDS = {
    "profit_take_pct": (15.0, 50.0),
    "stop_loss_pct": (-30.0, -10.0),
}
DEFAULT_PARAMS = {"profit_take_pct": 30.0, "stop_loss_pct": -20.0}

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
    hit INTEGER
);
CREATE TABLE IF NOT EXISTS advice_calibration (
    key TEXT PRIMARY KEY,
    value REAL,
    updated_at TEXT
);
"""

SELL_ACTIONS = {"sell", "moderate_sell", "heavy_sell"}
BUY_ACTIONS = {"buy", "moderate_buy", "heavy_buy"}


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
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=15)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def log_advice(self, fund_code: str, action: str,
                   reasons: str = "", score: Optional[float] = None) -> int:
        with self._w:
            cur = self._conn.execute(
                "INSERT INTO advice_log (ts, fund_code, action, reasons, score) "
                "VALUES (?, ?, ?, ?, ?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 fund_code, action, reasons[:500], score),
            )
            self._conn.commit()
            return cur.lastrowid

    def pending_evaluations(self, horizon_days: int = EVAL_HORIZON_DAYS) -> list[dict]:
        """到评估期但尚未回填的建议"""
        cutoff = (datetime.now() - timedelta(days=horizon_days)
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

    def record_outcome(self, advice_id: int, action: str, fund_change: float,
                       bench_change: float) -> None:
        """按建议方向判定命中：sell 类跌=命中，buy 类涨=命中"""
        if action in SELL_ACTIONS:
            hit = 1 if fund_change < 0 else 0
        elif action in BUY_ACTIONS:
            hit = 1 if fund_change > 0 else 0
        else:
            hit = 0
        with self._w:
            self._conn.execute(
                "INSERT OR REPLACE INTO advice_outcomes "
                "(advice_id, eval_date, fund_change_pct, benchmark_change_pct, hit) "
                "VALUES (?, ?, ?, ?, ?)",
                (advice_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 round(fund_change, 3), round(bench_change, 3), hit),
            )
            self._conn.commit()

    def stats(self) -> dict:
        with self._w:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM advice_outcomes").fetchone()[0]
            rows = self._conn.execute(
                "SELECT a.action, o.hit, o.fund_change_pct FROM advice_outcomes o "
                "JOIN advice_log a ON a.id = o.advice_id").fetchall()
            cal = {r[0]: r[1] for r in self._conn.execute(
                "SELECT key, value FROM advice_calibration")}
        sell = [r for r in rows if r[0] in SELL_ACTIONS]
        buy = [r for r in rows if r[0] in BUY_ACTIONS]
        return {
            "evaluated": total,
            "sell_total": len(sell),
            "sell_hits": sum(1 for r in sell if r[1] == 1),
            "buy_total": len(buy),
            "buy_hits": sum(1 for r in buy if r[1] == 1),
            "params": cal or DEFAULT_PARAMS,
        }

    def calibrate(self) -> dict:
        """命中率低于 50% → 收紧阈值（保守上下限内）"""
        st = self.stats()
        out = {"adjusted": False, "changes": []}
        with self._w:
            for key, bound in PARAM_BOUNDS.items():
                cur = self._conn.execute(
                    "SELECT value FROM advice_calibration WHERE key = ?",
                    (key,)).fetchone()
                cur_val = cur[0] if cur else DEFAULT_PARAMS[key]
                # sell 命中差 → 止损线向 -10 收紧；buy 命中差 → 止盈线向 15 收紧
                new_val = cur_val
                if key == "stop_loss_pct" and st["sell_total"] >= 5:
                    hit = st["sell_hits"] / st["sell_total"]
                    if hit < 0.5:
                        new_val = min(cur_val + 5.0, bound[1])  # 向 -10 靠近
                if key == "profit_take_pct" and st["buy_total"] >= 5:
                    hit = st["buy_hits"] / st["buy_total"]
                    if hit < 0.5:
                        new_val = max(cur_val - 5.0, bound[0])
                if new_val != cur_val:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO advice_calibration "
                        "(key, value, updated_at) VALUES (?, ?, ?)",
                        (key, new_val, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    out["changes"].append({"key": key, "from": cur_val, "to": new_val})
                    out["adjusted"] = True
            self._conn.commit()
        return out
