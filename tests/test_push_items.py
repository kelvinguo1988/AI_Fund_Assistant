"""推送报告项白名单回归测试 — top10_change 必须进入基金维度推送"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.push_service import FUND_ITEMS, MARKET_ITEMS


def test_top10_change_in_fund_items():
    """2026-08-29 修复：top10_change 不在白名单导致推送报告永远缺前十大持仓涨跌"""
    assert "top10_change" in FUND_ITEMS


def test_no_overlap_between_item_groups():
    """市场项与基金项集合不应重叠（重叠项会被双重处理）"""
    assert not (FUND_ITEMS & MARKET_ITEMS), FUND_ITEMS & MARKET_ITEMS


def test_all_report_config_keys_covered():
    """DB 种子的全部 item_key 都应归入某个白名单，否则推送静默丢弃该报告项"""
    import sqlite3
    from backend.config import settings
    from pathlib import Path

    db_path = Path(settings.DATABASE_DIR) / settings.DATABASE_NAME
    if not db_path.exists():
        return  # 无库环境跳过
    conn = sqlite3.connect(str(db_path))
    keys = {r[0] for r in conn.execute("SELECT item_key FROM report_config")}
    conn.close()
    uncovered = keys - (FUND_ITEMS | MARKET_ITEMS)
    assert not uncovered, f"以下报告项未归入推送白名单，推送时会被静默丢弃: {uncovered}"
