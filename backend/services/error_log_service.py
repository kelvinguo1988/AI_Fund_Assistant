"""错误日志存储 — 系统功能报错 / 数据源限流记录

设计（2026-08-31）：
- 独立 sqlite3 连接（check_same_thread=False + threading.Lock），同步读写，
  线程池埋点（eastmoney_patch/adapter 线程）与 async 上下文均可安全调用
- **节流去重**：(module, category, message 指纹) 60 秒内只记 1 条——
  限流类错误在批量任务里会连续触发，不节流会刷爆表且无增量信息
- 表容量上限 2000 条，插入时裁剪旧行
- 分类 category：
    rate_limit  数据源被限流/反爬拦截/熔断（**最关键**，用于排查触发模块）
    timeout     超时
    network     连接失败
    data        数据缺失/解析失败
    other       其他
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("data") / "fund_quant.db"
MAX_ROWS = 2000
RETENTION_DAYS = 30   # 保留期：超过 30 天的日志在下次写入时清理
THROTTLE_WINDOW = 60.0

_CATEGORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS error_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    module TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    severity TEXT NOT NULL DEFAULT 'error',
    message TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_error_logs_ts ON error_logs (ts);
CREATE INDEX IF NOT EXISTS ix_error_logs_category ON error_logs (category);
"""


class ErrorLogStore:
    """线程安全的错误日志存储（sqlite3 直连，独立于 ORM）"""

    _instance: Optional["ErrorLogStore"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
        return cls._instance

    def _init(self):
        self._write_lock = threading.Lock()
        self._throttle: dict[tuple, float] = {}
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(DB_PATH), check_same_thread=False, timeout=15
        )
        self._conn.executescript(_CATEGORY_SCHEMA)
        self._conn.commit()

    def log(
        self,
        module: str,
        message: str,
        category: str = "other",
        severity: str = "error",
        detail: str = "",
    ) -> bool:
        """记录一条错误日志（带节流）

        Returns:
            True=已写入, False=被节流跳过
        """
        now = time.time()
        fp = (module, category, message[:80])
        with self._write_lock:
            last = self._throttle.get(fp, 0.0)
            if now - last < THROTTLE_WINDOW:
                return False
            self._throttle[fp] = now
            try:
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                self._conn.execute(
                    "INSERT INTO error_logs (ts, module, category, severity, message, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, module[:80], category, severity, message[:500], detail[:2000]),
                )
                # 容量裁剪（最多 2000 条）+ 时间保留期（超过 30 天清理）
                self._conn.execute(
                    "DELETE FROM error_logs WHERE id NOT IN "
                    "(SELECT id FROM error_logs ORDER BY id DESC LIMIT ?)",
                    (MAX_ROWS,),
                )
                cutoff = time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(now - RETENTION_DAYS * 86400),
                )
                self._conn.execute("DELETE FROM error_logs WHERE ts < ?", (cutoff,))
                self._conn.commit()
                return True
            except Exception as e:
                logger.warning(f"错误日志写入失败: {e}")
                return False

    def query(
        self, limit: int = 100, category: Optional[str] = None,
        since_ts: Optional[str] = None,
    ) -> list[dict]:
        """查询（时间倒序）"""
        sql = "SELECT id, ts, module, category, severity, message, detail FROM error_logs"
        conds, params = [], []
        if category:
            conds.append("category = ?")
            params.append(category)
        if since_ts:
            conds.append("ts > ?")
            params.append(since_ts)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(min(limit, 1000))
        with self._write_lock:
            rows = self._conn.execute(sql, params).fetchall()
        keys = ["id", "ts", "module", "category", "severity", "message", "detail"]
        return [dict(zip(keys, r)) for r in rows]

    def count(self, since_ts: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) FROM error_logs"
        params: list = []
        if since_ts:
            sql += " WHERE ts > ?"
            params.append(since_ts)
        with self._write_lock:
            return self._conn.execute(sql, params).fetchone()[0]

    def clear(self) -> int:
        with self._write_lock:
            n = self._conn.execute("SELECT COUNT(*) FROM error_logs").fetchone()[0]
            self._conn.execute("DELETE FROM error_logs")
            self._conn.commit()
        return n

    def download_text(self, category: Optional[str] = None) -> str:
        """导出全部匹配日志为文本（下载用，时间正序便于阅读）"""
        sql = ("SELECT ts, severity, category, module, message, detail FROM error_logs")
        params: list = []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        sql += " ORDER BY id ASC"
        with self._write_lock:
            rows = self._conn.execute(sql, params).fetchall()
        lines = [
            f"# 错误日志导出 {time.strftime('%Y-%m-%d %H:%M:%S')} 共 {len(rows)} 条"
            + (f"（分类={category}）" if category else "")
        ]
        for ts, sev, cat, module, msg, detail in rows:
            lines.append(f"\n[{ts}] [{sev}] [{cat}] {module}\n  {msg}")
            if detail:
                lines.append(f"  详情: {detail}")
        return "\n".join(lines)


def log_source_failure(module: str, message: str, category: str = "rate_limit",
                       detail: str = "", severity: str = "error") -> None:
    """数据源失败埋点便捷函数（自动节流；自身异常绝不抛出）"""
    try:
        ErrorLogStore().log(module, message, category=category,
                            severity=severity, detail=detail)
    except Exception:
        pass


def classify_source_error(exc_text: str) -> str:
    """按异常文本归类错误（限流优先）"""
    t = exc_text.lower()
    if any(k in t for k in (
        "remotedisconnected", "connection aborted", "connection reset",
        "proxyerror", "429", "too many requests", "rate limit", "限流", "封",
    )):
        return "rate_limit"
    if "timeout" in t or "timed out" in t:
        return "timeout"
    if "connection" in t or "ssl" in t or "dns" in t:
        return "network"
    return "other"
