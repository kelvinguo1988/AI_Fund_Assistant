"""第五轮审查 #1/#2/#3 的口径回归防线 —— 资产类别决定费率档、基准口径与市场因子

三处修复共用 `backend/utils/fund_asset_class.py` 这一份真相，本文件按消费方分节锁口径：

A. 判据本身（池内真实名称/官方类型/标签快照）
B. 赎回费阶梯的分档解析与渲染（#1）
C. 调仓工单的持有期/惩罚档（#1：场内 3 天不再是"观望"）
D. 命中率的基准口径回落 abs（#2：不可比样本不进超额分母）
E. 市场三因子与估值分位阈值调节的 gate（#3：不表态而不是换尺子）
F. 复盘信号同向率的超额分母（#2）
G. analysis_service 的接线（打标 + 开关注入）

口径纪律：`asset_class` 为空串、`benchmark_kind` 为 NULL/空 = 调用方**没判过** = 保持
改造前行为。每节都有一条反向用例守这条回滚语义 —— 那是本次改造最容易悄悄漂移的地方。

测试红线：零上游请求。分类判据只读名称/类型/标签文本，`funds` 表在测试库自建。
"""

import asyncio
import json
import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.ai.holding_fee import (
    DEFAULT_FEE_LADDER,
    DEFAULT_FEE_LADDERS,
    PROFILE_LABELS,
    ZERO_FEE_NOTES,
    ladder_for_profile,
    ladder_text,
    ladders_text,
    parse_fee_ladders,
)
from backend.data_sources.base import FundData
from backend.database import _build_factor_seeds
from backend.engines.factor_engine import (
    calculate_market_fund_flow,
    calculate_market_sentiment,
    calculate_market_valuation,
)
from backend.engines.quality_filter import (
    PARAM_META,
    QUALITY_CONFIG,
    compute_dynamic_thresholds,
)
from backend.services import advice_learning_service as advice_mod
from backend.services.market_regime_service import MarketRegimeSnapshot
from backend.utils.fund_asset_class import (
    ASSET_A_SHARE_EQUITY,
    ASSET_BOND,
    ASSET_COMMODITY,
    ASSET_MONEY_MARKET,
    ASSET_OVERSEAS_EQUITY,
    BENCHMARK_ABS,
    BENCHMARK_HS300_EXCESS,
    a_share_market_proxy,
    benchmark_kind,
    classify_asset,
    fee_profile,
    hs300_comparable,
    share_class,
)
from backend.utils.timezone import beijing_today

REPO = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _dates(count: int):
    """以今天为末点的 count 个连续日期（升序，仅作净值日序号用）"""
    return [(beijing_today() - timedelta(days=count - 1 - i)).isoformat()
            for i in range(count)]


# ═══════════════════════════════════════════════════════════════════
# A. 判据快照（行取自开发库 status=active 的真实基金，2026-10-17 dump）
# ═══════════════════════════════════════════════════════════════════

# code, name, fund_type_official, tags, fund_type, asset, fee_profile
POOL_SNAPSHOT = [
    ("510300", "沪深300ETF", "指数型-股票", "指数型-股票,指数,宽基指数", "etf",
     ASSET_A_SHARE_EQUITY, "exchange_traded"),
    ("159915", "创业板ETF", "指数型-股票", "指数型-股票,指数,宽基指数", "etf",
     ASSET_A_SHARE_EQUITY, "exchange_traded"),
    ("513100", "纳指ETF", "指数型-海外股票", "指数型-海外股票,QDII跨境", "etf",
     ASSET_OVERSEAS_EQUITY, "exchange_traded"),
    ("518880", "黄金ETF", "指数型-其他", "指数型-其他,固收+/偏债", "etf",
     ASSET_COMMODITY, "exchange_traded"),
    ("009033", "建信上海金ETF联接A", "指数型-其他", "指数型-其他,固收+/偏债,银行", "otc",
     ASSET_COMMODITY, "equity"),
    ("162719", "广发道琼斯石油指数人民币A", "指数型-海外股票", "指数型-海外股票,美股QDII", "otc",
     ASSET_COMMODITY, "equity"),
    ("013149", "鹏华双债加利债券C", "债券型-混合二级", "债券型-混合二级,债券,固收+/偏债", "otc",
     ASSET_BOND, "bond_c"),
    ("027013", "广发港股通医疗创新精选混合C", "混合型-偏股",
     "混合型-偏股,偏股,医药,科技,QDII跨境", "otc",
     ASSET_OVERSEAS_EQUITY, "qdii"),
    ("968049", "摩根亚洲股息美元派息", None, "红利/高股息,QDII跨境", "otc",
     ASSET_OVERSEAS_EQUITY, "qdii"),
    ("016702", "银华海外数字经济量化选股混合发起式(QDII)", "QDII-混合偏股",
     "QDII-混合偏股,科技", "otc", ASSET_OVERSEAS_EQUITY, "qdii"),
    ("011452", "华泰柏瑞质量成长C", "混合型-偏股", "混合型-偏股,混合,偏股,含港股", "otc",
     ASSET_A_SHARE_EQUITY, "equity_c"),
    ("004011", "华泰柏瑞易利灵活配置混合C", "混合型-灵活", "混合型-灵活,混合,固收+/偏债", "otc",
     ASSET_A_SHARE_EQUITY, "equity_c"),
    ("025209", "永赢先锋半导体智选混合", "混合型-偏股", "混合型-偏股,科技", "otc",
     ASSET_A_SHARE_EQUITY, "equity"),
    ("000979", "景顺长城沪港深精选股票A", "股票型", "股票型,含港股", "otc",
     ASSET_A_SHARE_EQUITY, "equity"),
    ("018495", "融通产业趋势臻选股票C", "股票型", "股票型", "otc",
     ASSET_A_SHARE_EQUITY, "equity_c"),
]

POOL_PARAMS = [pytest.param(r, id=r[0]) for r in POOL_SNAPSHOT]


class TestAssetClassifierSnapshot:
    @pytest.mark.parametrize("row", POOL_PARAMS)
    def test_classify_asset(self, row):
        assert classify_asset(row[1], row[2], row[3], row[0]) == row[5]

    @pytest.mark.parametrize("row", POOL_PARAMS)
    def test_fee_profile(self, row):
        assert fee_profile(row[1], row[2], row[3], row[0], row[4]) == row[6]

    def test_tags_do_not_decide_overseas(self):
        """标签是"持仓暴露"：A 股主动基金写"含港股/QDII跨境"很常见（改造前实测 6 只
        混合型-偏股因此被判成海外基金，同时改掉费率档并退出 A 股因子）"""
        assert classify_asset("某成长混合C", "混合型-偏股",
                              "混合型-偏股,含港股,QDII跨境,港股通") == ASSET_A_SHARE_EQUITY

    def test_commodity_checked_before_bond(self):
        """`指数型-其他` 的黄金 ETF 联接不能被当成 A 股股权类；
        `债券型-混合二级` 必须走债基阶梯而不是商品档"""
        assert classify_asset("建信上海金ETF联接A", "指数型-其他") == ASSET_COMMODITY
        assert classify_asset("某债券型混合二级A", "债券型-混合二级") == ASSET_BOND

    def test_money_market_first(self):
        assert classify_asset("华夏现金增利货币A", "货币型") == ASSET_MONEY_MARKET

    def test_96_prefix_is_mutual_recognition(self):
        """96 开头 = 港澳互认基金，官方类型常缺失，只靠代码段判定"""
        assert classify_asset("摩根亚洲股息美元派息", None, None, "968049") == ASSET_OVERSEAS_EQUITY

    def test_unrecognized_falls_back_to_a_share_equity(self):
        """识别不出来 = 保持改造前行为（不因为文本没写"海外"就悄悄关因子）"""
        ac = classify_asset("某某新基金", None)
        assert ac == ASSET_A_SHARE_EQUITY
        assert benchmark_kind(ac) == BENCHMARK_HS300_EXCESS
        assert a_share_market_proxy(ac) is True

    def test_empty_asset_class_keeps_old_calibers(self):
        assert benchmark_kind("") == BENCHMARK_HS300_EXCESS
        assert a_share_market_proxy("") is True

    def test_only_a_share_equity_is_hs300_comparable(self):
        for ac in (ASSET_OVERSEAS_EQUITY, ASSET_BOND, ASSET_COMMODITY, ASSET_MONEY_MARKET):
            assert hs300_comparable(ac) is False
            assert benchmark_kind(ac) == BENCHMARK_ABS
            assert a_share_market_proxy(ac) is False
        assert hs300_comparable(ASSET_A_SHARE_EQUITY) is True
        assert benchmark_kind(ASSET_A_SHARE_EQUITY) == BENCHMARK_HS300_EXCESS
        assert a_share_market_proxy(ASSET_A_SHARE_EQUITY) is True

    def test_share_class_suffix(self):
        assert share_class("融通产业趋势臻选股票C") == "C"
        assert share_class("景顺长城稳健回报混合A") == "A"
        assert share_class("华泰柏瑞质量成长（C）") == "C"
        assert share_class("华泰柏瑞质量成长C类") == "C"
        assert share_class("永赢先锋半导体智选混合") == ""
        assert share_class(None) == ""


# ═══════════════════════════════════════════════════════════════════
# B. 阶梯配置：分档解析（#1）
# ═══════════════════════════════════════════════════════════════════

class TestFeeLadderConfig:
    def test_profile_keys_have_one_truth(self):
        """档位名散在三处（分类器 / 默认阶梯 / 中文标签）—— 少一个键就是"该档静默回落
        权益档"的错算，配置字典也必须覆盖同一批键"""
        assert set(DEFAULT_FEE_LADDERS) == set(PROFILE_LABELS)
        assert set(DEFAULT_FEE_LADDERS) == {
            "equity", "equity_c", "bond", "bond_c", "qdii", "exchange_traded", "money_market"}
        assert set(QUALITY_CONFIG["redemption_fee_ladders"]) == set(DEFAULT_FEE_LADDERS)
        assert parse_fee_ladders(QUALITY_CONFIG["redemption_fee_ladders"], None) == \
            parse_fee_ladders(DEFAULT_FEE_LADDERS, None)

    def test_legacy_single_ladder_shared_by_all_profiles(self):
        """回滚路径：旧键写成单条列表时全档共用，等价改造前的单一阶梯"""
        legacy = [[7, 1.5], [365, 0.5], [None, 0.25]]
        out = parse_fee_ladders(None, legacy)
        assert len(out) == 7
        assert all(v == [(7, 1.5), (365, 0.5), (None, 0.25)] for v in out.values())

    def test_new_key_written_as_list_also_shares(self):
        out = parse_fee_ladders([[7, 1.5], [None, 0.0]], None)
        assert all(v == [(7, 1.5), (None, 0.0)] for v in out.values())

    def test_partial_override_keeps_other_profiles_own_defaults(self):
        """只覆盖债基时，权益档不能跟着"共用"跑 —— 那会把 0.25% 的底费率写成 0.1%"""
        out = parse_fee_ladders({"bond": [[7, 1.5], [None, 0.0]]}, None)
        assert out["bond"] == [(7, 1.5), (None, 0.0)]
        assert out["equity"] == [(b, p) for b, p in DEFAULT_FEE_LADDERS["equity"]]
        assert out["exchange_traded"] == [(None, 0.0)]

    def test_unknown_profile_and_dirty_rows_dropped(self):
        out = parse_fee_ladders({"equityX": [[7, 1.5]], "bond": [["x", 1.5], [7, 0.3]]}, None)
        assert "equityX" not in out
        assert out["bond"] == [(7, 0.3)]
        # 整份配置全脏 → 回落默认：费率可以算保守，不能因脏配置表现成免赎回费
        assert parse_fee_ladders({"equity": "乱码"}, None)["equity"] == \
            [(b, p) for b, p in DEFAULT_FEE_LADDERS["equity"]]

    def test_ladder_for_profile_unknown_falls_back_to_equity(self):
        ladders = parse_fee_ladders({"bond": [[7, 1.5], [None, 0.1]]}, None)
        assert ladder_for_profile(ladders, "never_heard_of_it") == ladders["equity"]
        assert ladder_for_profile({}, "qdii") == [(b, p) for b, p in DEFAULT_FEE_LADDER]

    def test_zero_fee_ladder_text_not_garbled(self):
        """`[(None, 0)]` 走"全持有期 0%"，不能渲染成 "≥None天" """
        assert ladder_text([(None, 0.0)]) == "全持有期 0%"
        assert ladder_text(parse_fee_ladders(None, None)["equity"]) == (
            "<7天 1.5% / <365天 0.5% / ≥365天 0.25%")
        text = ladders_text(parse_fee_ladders(None, None))
        assert "None" not in text
        assert "场内份额、货币基金：全持有期 0%" in text
        assert "债券C类、权益C类：<7天 1.5% / <30天 0.5% / ≥30天 0%" in text
        assert "QDII/海外、权益A类：<7天 1.5% / <365天 0.5% / ≥365天 0.25%" in text

    def test_ladders_text_merges_equal_profiles_only(self):
        """全档共用（回滚态）时并成一行点名 7 档，而不是重复 7 遍同一串"""
        text = ladders_text(parse_fee_ladders([[7, 1.5], [None, 0.25]], None))
        assert text.count("<7天 1.5% / ≥7天 0.25%") == 1
        for label in PROFILE_LABELS.values():
            assert label in text


# ═══════════════════════════════════════════════════════════════════
# C. 调仓工单的持有期/惩罚档（#1 的实际后果）
# ═══════════════════════════════════════════════════════════════════

def _fund(code, name, official=None, tags=None, ftype="otc"):
    return SimpleNamespace(code=code, name=name, fund_type_official=official,
                           tags=tags, fund_type=ftype)


def _pos(first_buy):
    return SimpleNamespace(first_buy_date=first_buy)


class TestFeeGateByProfile:
    @pytest.fixture
    def policy(self):
        from backend.ai.rebalance import RebalanceService
        return RebalanceService._fee_policy({})

    def test_policy_shape(self, policy):
        assert policy["enabled"] is True
        assert policy["penalize_pct"] == 1.0
        assert len(policy["ladders"]) == 7
        assert policy["ladder_text"]

    def test_exchange_traded_3_days_is_not_downgraded(self, policy):
        """#1 的核心后果：场内 ETF 卖出只有佣金，持有 3 天不该落 1.5% 惩罚档"""
        from backend.ai.rebalance import RebalanceService
        gate = RebalanceService._fee_gate(
            policy, _pos(date(2026, 10, 14)), "2026-10-17",
            _fund("510300", "沪深300ETF", "指数型-股票", ftype="etf"))
        assert gate["fee_profile"] == "exchange_traded"
        assert gate["holding_days"] == 3
        assert gate["estimated_fee_pct"] == 0.0
        assert gate["punitive"] is False and gate["wait_days"] == 0
        assert "场内份额" in gate["notes"][0] and "未计入" in gate["notes"][0]

    def test_bond_c_full_month_is_free(self, policy):
        from backend.ai.rebalance import RebalanceService
        gate = RebalanceService._fee_gate(
            policy, _pos(date(2026, 9, 1)), "2026-10-17",
            _fund("013149", "鹏华双债加利债券C", "债券型-混合二级"))
        assert gate["fee_profile"] == "bond_c"
        assert gate["estimated_fee_pct"] == 0.0 and gate["punitive"] is False

    def test_bond_a_still_charges_after_month(self, policy):
        assert "bond" not in ZERO_FEE_NOTES      # 债基满 30 天仍收 0.1%，不是免费档
        from backend.ai.rebalance import RebalanceService
        gate = RebalanceService._fee_gate(
            policy, _pos(date(2026, 9, 1)), "2026-10-17",
            _fund("000003", "某债券型纯债A", "债券型-纯债"))
        assert gate["fee_profile"] == "bond"
        assert gate["estimated_fee_pct"] == 0.1
        assert gate["notes"] == ["持有 46 天，适用赎回费 0.1%"]

    def test_equity_c_over_30_days_not_punitive(self, policy):
        from backend.ai.rebalance import RebalanceService
        gate = RebalanceService._fee_gate(
            policy, _pos(date(2026, 7, 9)), "2026-10-17",
            _fund("004011", "华泰柏瑞易利灵活配置混合C", "混合型-灵活"))
        assert gate["fee_profile"] == "equity_c"
        assert gate["holding_days"] == 100
        assert gate["estimated_fee_pct"] == 0.0 and gate["punitive"] is False
        assert gate["notes"] == ["持有 100 天，适用赎回费 0%"]

    def test_equity_a_short_hold_still_punitive(self, policy):
        """反向保护：权益 A 类 3 天仍是 1.5% 惩罚档 —— 改造只摘掉误降级，不放开真短期费"""
        from backend.ai.rebalance import RebalanceService
        gate = RebalanceService._fee_gate(
            policy, _pos(date(2026, 10, 14)), "2026-10-17",
            _fund("025209", "永赢先锋半导体智选混合", "混合型-偏股"))
        assert gate["fee_profile"] == "equity"
        assert gate["estimated_fee_pct"] == 1.5
        assert gate["punitive"] is True and gate["wait_days"] == 4

    def test_missing_fund_uses_equity_ladder(self, policy):
        """fund=None（等权近似/查不到基金）→ 回落最保守的权益档，不猜档位"""
        from backend.ai.rebalance import RebalanceService
        gate = RebalanceService._fee_gate(policy, _pos(date(2026, 10, 14)), "2026-10-17", None)
        assert gate["fee_profile"] is None
        assert gate["estimated_fee_pct"] == 1.5 and gate["punitive"] is True

    def test_unknown_holding_days_never_punitive(self, policy):
        from backend.ai.rebalance import RebalanceService
        gate = RebalanceService._fee_gate(policy, _pos(None), "2026-10-17",
                                         _fund("510300", "沪深300ETF", ftype="etf"))
        assert gate["holding_days"] is None and gate["estimated_fee_pct"] is None
        assert gate["punitive"] is False and "持有期未知" in gate["notes"][0]

    def test_rollback_switch_clears_the_whole_section(self):
        from backend.ai.rebalance import RebalanceService
        off = RebalanceService._fee_policy({"redemption_fee_enabled": 0})
        assert off["enabled"] is False
        gate = RebalanceService._fee_gate(off, _pos(date(2026, 10, 14)), "2026-10-17",
                                         _fund("025209", "永赢先锋半导体智选混合"))
        assert gate == {"holding_days": None, "estimated_fee_pct": None, "punitive": False,
                        "wait_days": 0, "notes": [], "fee_profile": None}

    def test_legacy_ladder_config_flattens_all_profiles(self):
        """DB 只写旧键时场内 ETF 也按单条阶梯算 —— 配置层回滚，工单必须照办"""
        from backend.ai.rebalance import RebalanceService
        policy = RebalanceService._fee_policy(
            {"redemption_fee_ladder": [[7, 1.5], [365, 0.5], [None, 0.25]]})
        gate = RebalanceService._fee_gate(policy, _pos(date(2026, 10, 14)), "2026-10-17",
                                         _fund("510300", "沪深300ETF", ftype="etf"))
        assert gate["estimated_fee_pct"] == 1.5 and gate["punitive"] is True

    def test_ladder_override_via_json_config(self):
        """`redemption_fee_ladders` 走 quality_filter_config JSON 覆盖（配置页不渲染 list）"""
        from backend.ai.rebalance import RebalanceService
        policy = RebalanceService._fee_policy({"redemption_fee_ladders": {
            "exchange_traded": [[None, 0.3]]}})
        assert policy["ladders"]["exchange_traded"] == [[None, 0.3]]
        gate = RebalanceService._fee_gate(policy, _pos(date(2026, 10, 14)), "2026-10-17",
                                         _fund("510300", "沪深300ETF", ftype="etf"))
        assert gate["estimated_fee_pct"] == 0.3 and gate["punitive"] is False


class TestRebalanceHoldingsCarriesProfile:
    def test_holdings_rows_expose_profile_and_label(self):
        """档位中文名由后端下发（前端再抄一份映射 = 第二份真相）"""
        src = _src("backend/ai/rebalance.py")
        assert '"fee_profile": (fp := _fee_profile_of(fund_by_id[fid]))' in src
        assert '"fee_profile_label": PROFILE_LABELS.get(fp, "")' in src
        page = _src("frontend/src/pages/PositionsPage.tsx")
        assert "fee_profile_label" in page
        assert "exchange_traded" not in page      # 前端不自己判档位

    def test_summary_md_declares_profiles_and_commission(self):
        from backend.ai.rebalance import RebalanceReport
        rpt = RebalanceReport(as_of="2026-10-17", fee_policy={
            "enabled": True, "penalize_pct": 1.0,
            "ladder_text": "场内份额、货币基金：全持有期 0%"})
        md = rpt.summary_md()
        assert "赎回费阶梯（按份额档位）：" in md
        assert "场内份额、货币基金：全持有期 0%" in md
        assert "只有券商佣金，未计入" in md


# ═══════════════════════════════════════════════════════════════════
# D. 命中率的基准口径回落（#2）
# ═══════════════════════════════════════════════════════════════════

def _cols(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _store(tmp_path, name="q7_kind.db", with_funds=True):
    """AdviceLearningStore 直连测试库（object.__new__ 绕开单例，不碰真实 advice_log）

    `_isolate_advice_store` 建的库没有 `funds` 表，所以这里自建：口径判定要的是同库
    真实文本，而不是再打一次上游。
    """
    store = object.__new__(advice_mod.AdviceLearningStore)
    store._w = threading.Lock()
    store._conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
    store._conn.executescript(advice_mod._SCHEMA)
    store._conn.execute(
        "CREATE TABLE IF NOT EXISTS system_config ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " config_key TEXT NOT NULL UNIQUE, config_value TEXT NOT NULL,"
        " description TEXT, updated_at TEXT)")
    if with_funds:
        store._conn.execute(
            "CREATE TABLE funds (code TEXT PRIMARY KEY, name TEXT,"
            " fund_type_official TEXT, tags TEXT)")
        for row in POOL_SNAPSHOT:
            store._conn.execute(
                "INSERT OR REPLACE INTO funds (code, name, fund_type_official, tags)"
                " VALUES (?, ?, ?, ?)", (row[0], row[1], row[2], row[3]))
    store._conn.commit()
    return store


def _seed(store, code, action, ts, kind):
    """直接落 advice_log（绕开同日去重），把 benchmark_kind 写成要测的那一档"""
    cur = store._conn.execute(
        "INSERT INTO advice_log (ts, fund_code, action, reasons, score, benchmark_kind)"
        " VALUES (?, ?, ?, '', 1.0, ?)", (ts, code, action, kind))
    store._conn.commit()
    return cur.lastrowid


class TestBenchmarkKindResolution:
    def test_resolved_from_funds_table(self, tmp_path):
        store = _store(tmp_path)
        assert store.benchmark_kind_for("013149") == BENCHMARK_ABS
        assert store.benchmark_kind_for("513100") == BENCHMARK_ABS
        assert store.benchmark_kind_for("025209") == BENCHMARK_HS300_EXCESS

    @pytest.mark.parametrize("row", POOL_PARAMS)
    def test_kind_matches_asset(self, tmp_path, row):
        store = _store(tmp_path)
        want = BENCHMARK_ABS if row[5] != ASSET_A_SHARE_EQUITY else BENCHMARK_HS300_EXCESS
        assert store.benchmark_kind_for(row[0]) == want

    def test_unknown_code_or_missing_table_keeps_old_caliber(self, tmp_path):
        """查不到基金（已删除/测试库无 funds 表）按可比处理 —— 不静默把样本摘掉"""
        assert advice_mod.resolve_benchmark_kind(
            _store(tmp_path, "a.db")._conn, "999999") == BENCHMARK_HS300_EXCESS
        assert advice_mod.resolve_benchmark_kind(
            _store(tmp_path, "b.db", with_funds=False)._conn, "013149") == BENCHMARK_HS300_EXCESS

    def test_log_advice_locks_the_kind_at_write_time(self, tmp_path):
        """口径由落库那刻锁死：事后改名不该让历史样本的判定口径漂移"""
        store = _store(tmp_path)
        aid = store.log_advice("013149", "sell", "债基卖出")
        row = store._conn.execute(
            "SELECT benchmark_kind FROM advice_log WHERE id = ?", (aid,)).fetchone()
        assert row[0] == BENCHMARK_ABS
        store._conn.execute("UPDATE funds SET name = '某某成长混合A' WHERE code = '013149'")
        store._conn.commit()
        assert store._kind_of(aid) == BENCHMARK_ABS


class TestLegacyDbMigration:
    """存量库补列：新库由 _SCHEMA 直接建齐，只有**已存在的旧表**才走 ALTER —— 单测若永远
    用干净临时库，这条路径就等于没测（实跑时 `advice_log.benchmark_kind` 缺失让
    `/api/analysis/advice-stats` 直接 500：`no such column: a.benchmark_kind`）。"""

    _LEGACY = (
        "CREATE TABLE advice_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
        " fund_code TEXT NOT NULL, action TEXT NOT NULL, reasons TEXT, score REAL);"
        "CREATE TABLE advice_outcomes (advice_id INTEGER PRIMARY KEY, eval_date TEXT,"
        " fund_change_pct REAL, benchmark_change_pct REAL, hit INTEGER);"
    )

    def _legacy_conn(self, tmp_path, name="legacy.db"):
        conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False)
        conn.executescript(self._LEGACY)
        conn.commit()
        return conn

    def test_both_tables_get_their_own_columns(self, tmp_path):
        conn = self._legacy_conn(tmp_path)
        added = advice_mod.migrate_added_columns(conn)
        assert set(added) == {"advice_outcomes.hit_abs", "advice_outcomes.hit_excess",
                              "advice_log.benchmark_kind"}
        assert "benchmark_kind" in _cols(conn, "advice_log")
        assert {"hit_abs", "hit_excess"} <= _cols(conn, "advice_outcomes")
        # 幂等：第二次不再改动任何东西
        assert advice_mod.migrate_added_columns(conn) == []

    def test_migration_is_per_table_not_all_on_one(self, tmp_path):
        """回归防线：把两张表的列都 ALTER 到同一张表上时，advice_log 永远补不到列"""
        conn = self._legacy_conn(tmp_path, "per_table.db")
        advice_mod.migrate_added_columns(conn)
        for ddl in advice_mod._OUTCOME_NEW_COLUMNS:
            assert ddl.split()[0] not in _cols(conn, "advice_log")
        for table, columns in advice_mod._MIGRATIONS:
            assert {d.split()[0] for d in columns} <= _cols(conn, table), table

    def test_stats_runs_on_migrated_legacy_db(self, tmp_path):
        store = _store(tmp_path, "legacy_stats.db")
        conn = store._conn
        conn.executescript("DROP TABLE advice_log; DROP TABLE advice_outcomes;" + self._LEGACY)
        conn.commit()
        advice_mod.migrate_added_columns(conn)
        aid = conn.execute("INSERT INTO advice_log (ts, fund_code, action, score)"
                           " VALUES ('2026-10-01 15:00:00', '013149', 'sell', 1.0)").lastrowid
        conn.execute("INSERT INTO advice_outcomes (advice_id, eval_date, fund_change_pct,"
                     " benchmark_change_pct, hit, hit_abs, hit_excess)"
                     " VALUES (?, '2026-10-17', 1.0, NULL, 0, 0, NULL)", (aid,))
        conn.commit()
        st = store.stats()
        # 旧行没有 benchmark_kind → 按可比处理（改造前语义），NULL 的 excess 不进该口径分母
        assert st["by_kind"] == {"hs300_comparable": 1, "abs_only": 0}
        assert st["evaluated"] == 1
        assert st["by_mode"]["excess"]["sell"] == {"total": 0, "hits": 0}


class TestOutcomeAbsFallback:
    def test_bull_market_sell_on_bond_fund_no_longer_always_misses(self, tmp_path):
        """牛市里"卖出债基"在超额口径必判未命中（涨得比沪深300 少也算卖对）→
        这类样本改走绝对口径，`hit` 不再被市场方向绑死，超额列留 NULL"""
        store = _store(tmp_path)
        aid = store.log_advice("013149", "sell", "债基止盈")
        out = store.record_outcome(aid, "sell", 1.0, 20.0)     # 涨 1% 但跑输基准
        assert out["benchmark_kind"] == BENCHMARK_ABS
        assert out["hit_excess"] is None
        assert out["hit_abs"] == 0 and out["hit"] == 0

        aid2 = store.log_advice("518880", "sell", "黄金止盈")
        out2 = store.record_outcome(aid2, "sell", -1.0, 20.0)  # 真跌 → 绝对口径就记命中
        assert out2["hit"] == 1 and out2["hit_excess"] is None

    def test_benchmark_change_null_for_abs_only(self, tmp_path):
        """落 NULL 而不是 0.0：报表不能把"没比"显示成"与 0% 比" """
        store = _store(tmp_path)
        aid = store.log_advice("513100", "buy", "纳指买入")
        store.record_outcome(aid, "buy", 3.0, None)
        assert store._conn.execute(
            "SELECT benchmark_change_pct, hit_excess FROM advice_outcomes"
            " WHERE advice_id = ?", (aid,)).fetchone() == (None, None)

    def test_missing_benchmark_never_writes_a_wrong_excess(self, tmp_path):
        """可比但缺基准（bench=None）也不硬造超额判定：hit 退回绝对口径"""
        store = _store(tmp_path)
        aid = store.log_advice("025209", "buy", "A股买入")
        out = store.record_outcome(aid, "buy", 2.0, None)
        assert out["benchmark_kind"] == BENCHMARK_HS300_EXCESS
        assert out["hit_excess"] is None and out["hit"] == out["hit_abs"] == 1

    def test_comparable_rows_keep_both_columns(self, tmp_path):
        """反向保护：A 股股权类仍是双列并存，hit 跟 system_config 口径走"""
        store = _store(tmp_path)
        aid = store.log_advice("025209", "buy", "A股买入")
        out = store.record_outcome(aid, "buy", 1.0, 5.0)
        assert out == {"hit": 0, "hit_abs": 1, "hit_excess": 0, "mode": "excess",
                       "benchmark_kind": BENCHMARK_HS300_EXCESS}
        store.set_hit_mode("abs")
        out2 = store.record_outcome(aid, "buy", 1.0, 5.0)
        assert out2["hit"] == 1 and out2["hit_excess"] == 0

    def test_stats_separates_kinds_and_excess_denominator(self, tmp_path):
        store = _store(tmp_path)
        for code in ("025209", "000979"):            # 2 条可比：abs 中 / excess 不中
            aid = store.log_advice(code, "buy", "b")
            store.record_outcome(aid, "buy", 1.0, 5.0)
        for code in ("513100", "013149", "518880"):  # 3 条不可比
            aid = store.log_advice(code, "buy", "b")
            store.record_outcome(aid, "buy", 1.0, 5.0)
        st = store.stats()
        assert st["evaluated"] == 5
        assert st["by_kind"] == {"hs300_comparable": 2, "abs_only": 3}
        assert st["by_mode"]["abs"]["buy"] == {"total": 5, "hits": 5}
        assert st["by_mode"]["excess"]["buy"] == {"total": 2, "hits": 0}

    def test_calibrate_ignores_abs_only_samples(self, tmp_path):
        """#2 的真实危害：calibrate 对着"美股有没有跑赢 A 股"挪止损线。
        同样 30 条全不中的样本，不可比时不动阈值、可比时收紧。"""
        ts = (beijing_today() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        store = _store(tmp_path)
        for i in range(advice_mod.CALIBRATION_MIN_SAMPLES):
            aid = _seed(store, f"5131{i:02d}", "sell", ts, BENCHMARK_ABS)
            store.record_outcome(aid, "sell", 1.0, 0.5)
        assert store.calibrate()["adjusted"] is False

        store2 = _store(tmp_path, "cmp.db")
        for i in range(advice_mod.CALIBRATION_MIN_SAMPLES):
            aid = _seed(store2, f"0252{i:02d}", "sell", ts, BENCHMARK_HS300_EXCESS)
            store2.record_outcome(aid, "sell", 1.0, 0.5)
        out = store2.calibrate()
        assert out["adjusted"] is True
        assert {c["key"] for c in out["changes"]} == {"stop_loss_pct"}

    def test_legacy_null_kind_rows_stay_comparable(self, tmp_path):
        """改造前的行 benchmark_kind 为 NULL → 按可比处理，历史统计语义不变"""
        store = _store(tmp_path)
        aid = _seed(store, "013149", "sell", "2026-09-01 10:00:00", None)
        assert store._kind_of(aid) == BENCHMARK_HS300_EXCESS
        assert store.record_outcome(aid, "sell", -1.0, 0.5)["hit_excess"] == 1
        assert store.stats()["by_kind"] == {"hs300_comparable": 1, "abs_only": 0}


@pytest.fixture
def no_backfill_sleep(monkeypatch):
    async def _fake(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _fake)


@pytest.fixture
def stubbed_backfill(monkeypatch):
    """stub 基准/净值/口径三个入口：本用例测"不可比样本不取基准"，零真实请求"""
    state = {"bench_calls": 0, "nav_calls": [],
             "policy": {"nav_adjusted": True, "bench_div_yield_pct": 0.0}}

    class _Adapter:
        async def get_benchmark_series(self, symbol="sh000300", period=600):
            state["bench_calls"] += 1
            return [(d, 4000.0 + i * 40.0) for i, d in enumerate(_dates(120))]

    async def _policy(_db=None):
        return dict(state["policy"])

    async def _fetch(_adapter, code, days, start_date=None, *, adjusted=True):
        state["nav_calls"].append(code)
        return [(d, 1.0 + i * 0.002) for i, d in enumerate(_dates(120))]

    monkeypatch.setattr("backend.data_sources.akshare_adapter.AKShareAdapter", _Adapter)
    monkeypatch.setattr("backend.services.caliber_service.load_caliber", _policy)
    monkeypatch.setattr("backend.services.review_service._fetch_nav_series", _fetch)
    return state


class TestBackfillBenchmarkByKind:
    async def test_abs_only_sample_records_no_benchmark(
            self, tmp_path, stubbed_backfill, no_backfill_sleep):
        store = _store(tmp_path)
        old = (beijing_today() - timedelta(days=100)).strftime("%Y-%m-%d") + " 09:30:00"
        abs_id = _seed(store, "513100", "sell", old, BENCHMARK_ABS)
        cmp_id = _seed(store, "025209", "sell", old, BENCHMARK_HS300_EXCESS)

        out = await advice_mod.run_advice_backfill(store)
        assert out["evaluated"] == 2 and out["abs_only_evaluated"] == 1
        rows = {r[0]: r[1:] for r in store._conn.execute(
            "SELECT advice_id, benchmark_change_pct, hit_excess, hit_abs"
            " FROM advice_outcomes")}
        assert rows[abs_id] == (None, None, 0)          # 基准上涨，sell 绝对口径未命中
        assert rows[cmp_id][0] is not None and rows[cmp_id][1] is not None
        # 基准序列仍一轮取一次：改造没有新增上游请求
        assert stubbed_backfill["bench_calls"] == 1

    async def test_kind_missing_on_legacy_row_resolved_from_funds(
            self, tmp_path, stubbed_backfill, no_backfill_sleep):
        """改造前的行（kind NULL）回填时按同库 funds 表现算，不写错超额"""
        store = _store(tmp_path)
        old = (beijing_today() - timedelta(days=100)).strftime("%Y-%m-%d") + " 09:30:00"
        aid = _seed(store, "013149", "buy", old, None)
        out = await advice_mod.run_advice_backfill(store)
        assert out["evaluated"] == 1 and out["abs_only_evaluated"] == 1
        assert store._conn.execute(
            "SELECT benchmark_change_pct FROM advice_outcomes WHERE advice_id = ?",
            (aid,)).fetchone()[0] is None

    def test_pending_evaluations_carries_kind(self, tmp_path):
        store = _store(tmp_path)
        store.log_advice("013149", "sell", "债基")
        assert store.pending_evaluations(horizon_days=0)[0]["benchmark_kind"] == BENCHMARK_ABS


# ═══════════════════════════════════════════════════════════════════
# E. 市场三因子 + 估值分位阈值调节的 gate（#3）
# ═══════════════════════════════════════════════════════════════════

def _fd(asset_class=""):
    return FundData(code="000001", name="测试", date="2026-10-17", close=1.5,
                    close_history=[], asset_class=asset_class)


def _snap(pct=0.9, adv=0.7, margin=0.05):
    return MarketRegimeSnapshot(valuation_percentile=pct, adv_decline_ratio=adv,
                                margin_change_pct_7d=margin)


PARAMS_ON = {"_regime_snapshot": _snap(), "_market_factors_a_share_only": True}
MARKET_FNS = [calculate_market_valuation, calculate_market_sentiment,
              calculate_market_fund_flow]
OUT_OF_SCOPE = (ASSET_BOND, ASSET_OVERSEAS_EQUITY, ASSET_COMMODITY, ASSET_MONEY_MARKET)


class TestMarketFactorGate:
    def test_out_of_scope_says_nothing_not_neutral_plus(self):
        """债基/纳指/黄金基金：记 0 分 + data_valid=False（不表态），
        而不是让 A 股情绪给它加减分 —— 合计权重 1.8/8.3 ≈ 22%，足以翻转边缘信号"""
        for fn, code, name in ((calculate_market_valuation, "market_valuation", "大盘估值分位"),
                               (calculate_market_sentiment, "market_sentiment", "市场情绪"),
                               (calculate_market_fund_flow, "market_fund_flow", "资金面")):
            for ac in OUT_OF_SCOPE:
                r = fn(_fd(ac), PARAMS_ON)
                assert (r.factor_code, r.factor_name) == (code, name)
                assert r.score == 0.0 and r.raw_value == 0.0
                assert r.data_valid is False, f"{code} × {ac}"

    @pytest.mark.parametrize("fn", MARKET_FNS)
    def test_a_share_still_scored(self, fn):
        r = fn(_fd(ASSET_A_SHARE_EQUITY), PARAMS_ON)
        assert r.data_valid is True and r.score != 0.0

    @pytest.mark.parametrize("fn", MARKET_FNS)
    def test_untagged_fund_keeps_old_behavior(self, fn):
        """asset_class 为空 = 没判过 → 照旧全池通用"""
        assert fn(_fd(""), PARAMS_ON).data_valid is True

    @pytest.mark.parametrize("fn", MARKET_FNS)
    def test_switch_off_restores_old_behavior(self, fn):
        """回滚：market_factors_a_share_only=0 → 债基也按 A 股市场读数打分"""
        r = fn(_fd(ASSET_BOND), {**PARAMS_ON, "_market_factors_a_share_only": False})
        assert r.data_valid is True and r.score != 0.0

    def test_caller_without_switch_keeps_old_behavior(self):
        """旧调用方/单测不传 _market_factors_a_share_only → 不因缺键改变行为：
        债基仍然读得到 A 股估值分位（改造前那样打分），而不是被静默判成不表态"""
        from backend.engines.factor_engine import set_current_regime
        set_current_regime(_snap(0.9))
        try:
            assert calculate_market_valuation(_fd(ASSET_BOND), {}).data_valid is True
            assert calculate_market_valuation(_fd(ASSET_BOND)).score == -1.0
        finally:
            set_current_regime(None)

    def test_missing_snapshot_still_neutral_and_invalid(self):
        """快照缺失时（改造前就是中性 0 分）data_valid 仍为 False，覆盖率口径不变"""
        r = calculate_market_valuation(_fd(ASSET_A_SHARE_EQUITY),
                                       {"_regime_snapshot": None,
                                        "_market_factors_a_share_only": True})
        assert (r.score, r.data_valid) == (0.0, False)


class TestThresholdRegimeGate:
    cfg = dict(QUALITY_CONFIG)

    def _buy(self, asset_class="", cfg=None, pct=0.95):
        buy, _ = compute_dynamic_thresholds(
            False, False, cfg or self.cfg, regime_snapshot=_snap(pct),
            asset_class=asset_class)
        return buy

    def _base(self, cfg=None):
        return compute_dynamic_thresholds(
            False, False, cfg or self.cfg, regime_snapshot=_snap(None))[0]

    def test_a_share_gets_the_high_valuation_increment(self):
        assert self._buy(ASSET_A_SHARE_EQUITY) == pytest.approx(
            self._base() + self.cfg["extreme_high_valuation_buy_increment"])

    def test_non_a_share_threshold_untouched(self):
        """沪深300 在历史高位不是"一只债基该更谨慎买入"的理由 ——
        因子侧不表态了，阈值侧也不能再给它加（不允许只改一半）"""
        for ac in OUT_OF_SCOPE:
            assert self._buy(ac) == pytest.approx(self._base()), ac

    def test_empty_asset_class_and_switch_off_both_keep_adjustment(self):
        bumped = self._base() + self.cfg["extreme_high_valuation_buy_increment"]
        assert self._buy("") == pytest.approx(bumped)               # 没判过 = 改造前
        assert self._buy(ASSET_BOND, dict(self.cfg, market_factors_a_share_only=0)) \
            == pytest.approx(bumped)                                 # 回滚键

    def test_size_shock_and_drift_still_apply(self):
        """仓位冲击/风格漂移的加档与资产类别无关 —— 它们量的是这只基金自己"""
        cfg = dict(self.cfg, extreme_high_valuation_pct=0.9)
        bond_shock = compute_dynamic_thresholds(
            True, False, cfg, regime_snapshot=_snap(0.95), asset_class=ASSET_BOND)[0]
        assert bond_shock == pytest.approx(
            compute_dynamic_thresholds(False, False, cfg, regime_snapshot=_snap(None))[0]
            + cfg["size_shock_buy_increment"])

    def test_config_defaults_and_metadata(self):
        assert QUALITY_CONFIG["market_factors_a_share_only"] == 1
        assert "market_factors_a_share_only" in PARAM_META
        assert "redemption_fee_ladders" in PARAM_META
        assert "redemption_fee_ladder" in PARAM_META


# ═══════════════════════════════════════════════════════════════════
# F. 复盘信号的超额分母（#2）
# ═══════════════════════════════════════════════════════════════════

def _item(code, growth, signal, kind=None):
    from backend.schemas.analysis import FundReviewItem
    return FundReviewItem(fund_code=code, fund_name=code, growth_pct=growth,
                          signal_start=signal, benchmark_kind=kind)


class TestReviewExcessDenominator:
    def test_abs_only_items_stay_in_absolute_rate(self):
        from backend.services.review_service import ReviewService
        items = [
            _item("025209", 5.0, "buy"),                 # 可比：两口径都进
            _item("513100", 5.0, "buy", BENCHMARK_ABS),  # 不可比：只进绝对
            _item("013149", -3.0, "sell", BENCHMARK_ABS),
        ]
        stats = ReviewService._signal_hit_stats(items, 8.0)
        assert (stats["buy_total"], stats["sell_total"]) == (2, 1)
        assert stats["hit_rate"] == 100.0               # 绝对口径全命中（beta）
        ex = stats["excess"]
        assert (ex["buy_total"], ex["sell_total"]) == (1, 0)
        assert ex["abs_only_skipped"] == 2
        assert ex["hit_rate"] == 0.0                    # 涨 5% 但跑输基准 8%

    def test_no_benchmark_means_no_excess_block(self):
        from backend.services.review_service import ReviewService
        stats = ReviewService._signal_hit_stats(
            [_item("013149", 5.0, "buy", BENCHMARK_ABS)], None)
        assert "excess" not in stats

    def test_none_kind_is_treated_as_comparable(self):
        from backend.services.review_service import ReviewService
        stats = ReviewService._signal_hit_stats([_item("013149", 5.0, "buy", None)], 8.0)
        assert stats["excess"]["buy_total"] == 1
        assert stats["excess"]["abs_only_skipped"] == 0

    def test_hold_and_missing_growth_skipped_as_before(self):
        from backend.services.review_service import ReviewService
        stats = ReviewService._signal_hit_stats(
            [_item("A", None, "buy", BENCHMARK_ABS), _item("B", 2.0, "hold", BENCHMARK_ABS)],
            8.0)
        assert stats["hit_rate"] is None
        assert stats["excess"]["abs_only_skipped"] == 0

    def test_report_discloses_caliber_and_skips(self):
        from backend.schemas.analysis import ReviewReport
        from backend.services.review_service import ReviewService, _bench_cell
        report = ReviewReport(
            start_date="2026-09-01", end_date="2026-10-01", fund_count=2,
            portfolio_growth_pct=1.0, benchmark_growth_pct=8.0, excess_pct=-7.0,
            items=[_item("025209", 5.0, "buy", BENCHMARK_HS300_EXCESS),
                   _item("013149", 5.0, "buy", BENCHMARK_ABS)],
            signal_stats={"buy_total": 2, "buy_hits": 2, "sell_total": 0, "sell_hits": 0,
                          "hit_rate": 100.0,
                          "excess": {"buy_total": 1, "buy_hits": 0, "sell_total": 0,
                                     "sell_hits": 0, "hit_rate": 0.0,
                                     "benchmark_growth_pct": 8.0, "abs_only_skipped": 1}},
            caliber={"lines": ["> 口径一", "> 口径二", "> 口径三"],
                     "nav_adjusted": True, "bench_div_yield_pct": 2.7})
        md = ReviewService._build_summary_md(report)
        assert "> 口径一" in md and "> 仅供参考" in md
        assert "另有 1 只与沪深300 不可比" in md
        assert "基准口径：超额一律对沪深300" in md
        assert "仅绝对涨跌" in md and "沪深300超额" in md
        assert _bench_cell(None) == "—"
        assert _bench_cell(BENCHMARK_ABS) == "仅绝对涨跌"

    def test_review_items_tag_kind_from_fund_text(self):
        src = _src("backend/services/review_service.py")
        assert "benchmark_kind=benchmark_kind(classify_asset(" in src


# ═══════════════════════════════════════════════════════════════════
# G. analysis_service 接线（打标 + 开关注入）
# ═══════════════════════════════════════════════════════════════════

class TestAnalysisServiceWiring:
    def test_inject_regime_params_carries_the_switch(self):
        from backend.services.analysis_service import _inject_regime_params
        snap = _snap(0.5)
        assert _inject_regime_params(json.dumps({"window": 60}), snap, True) == {
            "window": 60, "_regime_snapshot": snap, "_market_factors_a_share_only": True}
        assert _inject_regime_params(None, None, False)["_market_factors_a_share_only"] is False
        assert _inject_regime_params("乱码", None, True)["_market_factors_a_share_only"] is True

    def test_tag_asset_class_uses_fund_text(self):
        from backend.services.analysis_service import _tag_asset_class
        fd = _tag_asset_class(
            _fund("013149", "鹏华双债加利债券C", "债券型-混合二级", "债券"),
            FundData(code="013149"))
        assert fd.asset_class == ASSET_BOND

    def test_both_fetch_paths_tag_after_fetch(self):
        """两条取数路径都必须打标，且打在 calculate_all 之前 ——
        漏一条就是那只基金读着 A 股情绪打分"""
        src = _src("backend/services/analysis_service.py")
        assert src.count("_tag_asset_class(fund, fd)") == 2
        assert src.count("fd = await self.data_source.get_fund_data(") == 2
        assert "_tag_asset_class(fund, fd)" in src.split("def _fetch_one")[1][:600]
        assert "_tag_asset_class(fund, fd)" in src.split("def _fetch_and_score")[1][:600]

    def test_switch_read_from_merged_quality_config_and_injected(self):
        src = _src("backend/services/analysis_service.py")
        assert 'merged_qf_config.get("market_factors_a_share_only", 1)' in src
        assert 'if f.get("code", "").startswith("market_") else f' in src

    def test_gate_does_not_change_factor_weights(self):
        """#3 用 data_valid=False 而不是跳过因子：total_weight 读的是因子配置里的
        weight，与 data_valid 无关 → 动态阈值的折算尺子不会因为这只基金退出市场择时而漂移"""
        src = _src("backend/engines/quality_filter.py")
        assert 'total_weight=sum(float(f.get("weight", 1.0) or 0.0) for f in active_factors)' in src
        # 且市场因子 normalization=none：0 分不会被截面标准化重算成非 0，"不表态"才成立
        seeds = {s["code"]: s for s in _build_factor_seeds()}
        for code in ("market_valuation", "market_sentiment", "market_fund_flow"):
            assert seeds[code]["normalization"] == "none", code

    def test_no_upstream_requests_added(self):
        """三处判定全走库内文本（新增上游请求与防封禁预算直接冲突）"""
        for rel in ("backend/utils/fund_asset_class.py", "backend/ai/holding_fee.py"):
            src = _src(rel)
            for forbidden in ("requests", "httpx", "akshare", "run_with_timeout"):
                assert forbidden not in src, f"{rel} 引入了 {forbidden}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
