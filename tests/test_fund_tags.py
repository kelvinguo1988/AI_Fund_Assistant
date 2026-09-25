"""双层标签服务回归测试 — 用 8 只真实基金 F10 数据锁定解析规则"""

import sys, os
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.fund_tag_service import (
    parse_primary_tags,
    parse_exposure_tags,
    build_double_tags,
)

# 2026-08-30 实测抓取的真实 F10 数据（锁定口径防回归）
REAL_F10 = {
    "001194": {"type": "混合型-灵活",
               "bench": "1年期银行定期存款利率(税后)+3%(单利年化)",
               "name": "景顺长城稳健回报混合A"},
    "004011": {"type": "混合型-灵活",
               "bench": "中债-综合全价(总值)指数收益率*85%+中证A500指数收益率*15%",
               "name": "华泰柏瑞易利灵活配置混合C"},
    "004815": {"type": "混合型-灵活",
               "bench": "中证沪港深高股息精选指数收益率×80%+中债综合指数收益率×20%",
               "name": "中欧红利优享混合C"},
    "016874": {"type": "混合型-偏股",
               "bench": "沪深300指数收益率*60%+人民币计价的恒生指数收益率*20%+中债-新综合财富(总值)指数收益率*20%",
               "name": "广发远见智选混合C"},
    "023299": {"type": "指数型-股票",
               "bench": "中证A500指数收益率*95%+同期银行活期存款利率(税后)*5%",
               "name": "汇添富中证A500指数增强C"},
    "024203": {"type": "混合型-偏股",
               "bench": "中证智能制造主题指数收益率*70%+恒生指数收益率(按估值汇率折算)*10%+中债-综合指数(全价)收益率*20%",
               "name": "永赢制造升级智选混合发起C"},
    "008715": {"type": "混合型-灵活",
               "bench": "沪深300指数收益率*50%+恒生指数收益率*30%+中债-综合全价(1-3年)指数收益率*20%",
               "name": "景顺长城价值驱动一年持有混合"},
    "018123": {"type": "混合型-偏股",
               "bench": "中证数字经济主题指数收益率*60%+中债综合指数(1-3年)*20%+恒生指数*20%",
               "name": "永赢数字经济智选混合发起C"},
}


class TestPrimaryTags:
    @pytest.mark.parametrize("code,expect", [
        ("001194", ["固收+/偏债", "偏债稳健"]),          # 定存+3% 固收+
        ("004011", ["固收+/偏债"]),                       # 中债85%（不得被宽基 A500 抢走）
        ("004815", ["红利/高股息"]),                      # 高股息基准
        ("016874", ["含港股"]),                           # 恒生 20% → 含港股
        ("023299", ["宽基指数", "指数增强"]),             # A500×95%（不得误判固收）
        ("024203", ["高端制造"]),                         # 智能制造指数基准
        ("008715", ["含港股", "价值"]),                   # 恒生30% + 名称"价值"
        ("018123", ["数字经济/科技"]),                    # 数字经济指数基准
    ])
    def test_real_f10_data(self, code, expect):
        d = REAL_F10[code]
        tags, pos = parse_primary_tags(d["type"], d["bench"], d["name"])
        for e in expect:
            assert e in tags, f"{code}: {e} not in {tags}"
        # 官方类型始终是首个标签
        assert tags[0] == d["type"]

    def test_mutual_fund_fallback(self):
        """互认基金（F10 无档案）→ 名称解析 + 互认标记"""
        tags, pos = parse_primary_tags("互认基金", None, "摩根亚洲股息美元派息")
        assert "QDII跨境" in tags
        assert "红利/高股息" in tags

    def test_no_data(self):
        tags, pos = parse_primary_tags(None, None, "某某混合")
        assert "混合" not in tags or tags  # 不崩溃即可


class TestExposureTags:
    def test_light_module_concentration(self):
        """001194 真实 Q2 持仓 → 光模块/算力集中暴露"""
        holdings = [
            {"stock_name": "新易盛", "ratio": 9.36},
            {"stock_name": "中际旭创", "ratio": 8.99},
            {"stock_name": "源杰科技", "ratio": 8.21},
            {"stock_name": "生益科技", "ratio": 7.71},
            {"stock_name": "寒武纪", "ratio": 7.7},
            {"stock_name": "华虹宏力", "ratio": 6.11},
            {"stock_name": "阳光电源", "ratio": 3.26},
        ]
        out = parse_exposure_tags(holdings)
        assert "光模块/CPO×3" in out
        assert "半导体/算力芯片×2" in out
        assert "新能源" in out

    def test_empty(self):
        assert parse_exposure_tags([]) is None
        assert parse_exposure_tags([{"stock_name": "x", "ratio": None}]) is None


# ── 暴露聚合改造（2026-09-20 设计①②：去噪/并集/别名/联动）────────────


class TestExposureV2:
    def test_noise_concepts_dropped(self):
        """业绩事件/持股类/指数编制组合概念不参与赛道聚合"""
        cm = {"300308": ["2026中报预增", "国家大基金持股", "中国AI 50", "同花顺出海50"]}
        out = parse_exposure_tags(
            [{"stock_code": "300308", "stock_name": "某某无关股", "ratio": 8.0}],
            concept_map=cm,
        )
        assert out is None  # 唯一命中是噪声 → 无有效桶

    def test_alias_merge(self):
        """THS 概念别名归一：CPO概念 与光模块关键词入同一桶"""
        holds = [
            {"stock_code": "1", "stock_name": "中际旭创", "ratio": 10.0},
            {"stock_code": "2", "stock_name": "某新易盛无关", "ratio": 9.0},
        ]
        out = parse_exposure_tags(holds, concept_map={"2": ["CPO概念"]})
        assert "光模块/CPO×2 19.0%" in out

    def test_union_multi_bucket(self):
        """并集模型：一票同时计入 THS 概念桶与关键词桶"""
        holds = [{"stock_code": "300308", "stock_name": "中际旭创", "ratio": 8.05}]
        out = parse_exposure_tags(
            holds, concept_map={"300308": ["液冷服务器", "F5G概念"]},
        )
        assert "光模块/CPO×1 8.1%" in out
        assert "AI服务器×1 8.1%" in out
        assert "(覆盖100%)" in out

    def test_min_bucket_and_top4(self):
        """<2% 桶丢弃；最多输出前 4 桶"""
        holds = [{"stock_name": f"无关{i}", "ratio": 1.0} for i in range(6)]
        holds[0]["stock_name"] = "中际旭创"  # 8%? ratio 1.0 → 1% <2 被弃
        assert parse_exposure_tags(holds) is None

    def test_theme_linkage_concentrated(self, monkeypatch):
        """前两桶合计≥50% → 主标签补持仓推定主题（018957 场景）"""
        import backend.services.fund_tag_service as tm
        monkeypatch.setattr(tm, "fetch_xq_basic", lambda code: None)
        holds = [
            {"stock_name": "中际旭创", "ratio": 9.9},
            {"stock_name": "新易盛", "ratio": 9.5},
            {"stock_name": "天孚通信", "ratio": 9.2},
            {"stock_name": "源杰科技", "ratio": 8.8},
            {"stock_name": "东山精密", "ratio": 8.5},
            {"stock_name": "深南电路", "ratio": 8.1},
            {"stock_name": "生益科技", "ratio": 7.9},
            {"stock_name": "沪电股份", "ratio": 7.6},
            {"stock_name": "某某无关", "ratio": 5.0},
        ]
        result = tm.build_double_tags(
            "018957", "中航机遇领航混合发起C", "otc", holds,
            f10={"official_type": "混合型-偏股",
                 "benchmark": "沪深300指数收益率*70%+中债综合指数收益率*25%+中证港股通综合指数收益率*5%"},
        )
        assert "光模块/CPO" in (result["tags"] or "")
        assert result["position_tag"] == "含港股" or result["position_tag"] == "光模块/CPO"

    def test_theme_linkage_not_fired_when_diffuse(self, monkeypatch):
        """分散持仓不触发联动"""
        import backend.services.fund_tag_service as tm
        monkeypatch.setattr(tm, "fetch_xq_basic", lambda code: None)
        holds = [
            {"stock_name": "工商银行", "ratio": 6.0},
            {"stock_name": "贵州茅台", "ratio": 5.5},
            {"stock_name": "宁德时代", "ratio": 5.0},
            {"stock_name": "招商银行", "ratio": 4.8},
            {"stock_name": "美的集团", "ratio": 4.5},
        ]
        result = tm.build_double_tags(
            "000001", "某某分散混合", "otc", holds,
            f10={"official_type": "混合型-偏股",
                 "benchmark": "沪深300指数收益率*70%+中债综合指数收益率*30%"},
        )
        assert "光模块/CPO" not in (result["tags"] or "")


@pytest.mark.asyncio
async def test_build_double_tags_mutual(db_session, monkeypatch):
    """互认基金端到端：F10 档案缺失 → 名称解析兜底"""
    # 模拟 F10 无档案（968049 真实场景），禁止内部真实联网
    import backend.services.fund_tag_service as tag_mod
    monkeypatch.setattr(tag_mod, "fetch_f10_profile", lambda code: None)
    result = build_double_tags(
        code="968049", name="摩根亚洲股息美元派息", fund_type="otc",
        holdings=None, f10=None,
    )
    assert result["is_mutual_fund"] is True
    assert "互认基金" in (result["tags"] or "")
    assert result["fund_type_official"] == "互认基金"


@pytest.mark.asyncio
async def test_find_dirty_tag_fund_ids(db_session):
    """启动自愈扫描：仅命中 active 且基准/暴露/标签缺失的基金"""
    from backend.models.fund import Fund
    from backend.services.fund_service import find_dirty_tag_fund_ids

    ok = Fund(code="510300", name="完好的", fund_type="etf", status="active",
              tags="宽基", benchmark_text="沪深300*95%", exposure_tags="x")
    dirty = Fund(code="017103", name="缺暴露", fund_type="otc", status="active",
                 tags="混合", benchmark_text="数字经济*80%")
    disabled = Fund(code="000002", name="停用且无标签", fund_type="otc", status="disabled")
    db_session.add_all([ok, dirty, disabled])
    await db_session.commit()

    ids = await find_dirty_tag_fund_ids(db_session)
    assert ids == [dirty.id]


@pytest.mark.asyncio
async def test_recompute_double_tags_end_to_end(db_session, monkeypatch):
    """重算入口：F10 档案 + 库内持仓 → tags/benchmark/exposure 全部落库"""
    import backend.services.fund_tag_service as tm
    monkeypatch.setattr(tm, "fetch_f10_profile", lambda code: {
        "official_type": "混合型-偏股",
        "benchmark": "中证数字经济主题指数收益率*80%+人民币活期存款利率(税后)*20%",
    })
    monkeypatch.setattr(tm, "fetch_xq_basic", lambda code: None)

    from backend.models.fund import Fund
    from backend.models.fund_holding import FundHolding
    from backend.services.fund_service import recompute_double_tags

    f = Fund(code="017103", name="大摩数字经济混合C", fund_type="otc", status="active")
    db_session.add(f)
    await db_session.commit()
    for c, n, r in [("300308", "中际旭创", 8.05), ("300502", "新易盛", 7.99)]:
        db_session.add(FundHolding(
            fund_id=f.id, stock_code=c, stock_name=n, ratio=r,
            quarter_label="2026年2季度股票投资明细", report_date="",
        ))
    await db_session.commit()

    fund = await recompute_double_tags(db_session, f.id)
    assert fund is not None
    assert "数字经济/科技" in (fund.tags or "")
    assert fund.benchmark_text and "数字经济" in fund.benchmark_text
    assert fund.exposure_tags and "光模块/CPO×2 16.0%" in fund.exposure_tags


def _make_total_fail(monkeypatch):
    import backend.services.fund_tag_service as tm
    from backend.services.fund_tag_service import F10FetchError

    def _fail(code):
        raise F10FetchError("F10 抓取失败(test)")
    monkeypatch.setattr(tm, "fetch_f10_profile", _fail)
    monkeypatch.setattr(tm, "fetch_xq_basic", lambda code: None)


@pytest.mark.asyncio
async def test_recompute_total_fail_keeps_old_when_fields_complete(db_session, monkeypatch):
    """双源失败 + 字段齐全：保守保留旧标签（原保护语义不变）"""
    _make_total_fail(monkeypatch)
    from backend.models.fund import Fund
    from backend.services.fund_service import recompute_double_tags

    f = Fund(code="017103", name="大摩数字经济混合C", fund_type="otc", status="active",
             tags="旧标签", benchmark_text="旧基准", fund_type_official="混合型")
    db_session.add(f)
    await db_session.commit()

    fund = await recompute_double_tags(db_session, f.id, allow_fallback_on_fail=True)
    assert fund.tags == "旧标签" and fund.benchmark_text == "旧基准"


@pytest.mark.asyncio
async def test_recompute_total_fail_writes_fallback_for_dirty(db_session, monkeypatch):
    """双源失败 + 基准缺失（自愈目标）：写名称解析兜底，坏标签不再永久残留"""
    _make_total_fail(monkeypatch)
    from backend.models.fund import Fund
    from backend.services.fund_service import recompute_double_tags

    f = Fund(code="017103", name="大摩数字经济混合C", fund_type="otc",
             status="active", tags="CPO,HALO,光模块")
    db_session.add(f)
    await db_session.commit()

    fund = await recompute_double_tags(db_session, f.id, allow_fallback_on_fail=True)
    assert fund.tags and fund.tags != "CPO,HALO,光模块"
    assert "数字经济" in fund.tags


@pytest_asyncio.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from backend.database import Base
    import backend.models

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


# ── 指数估值近似映射（模块 C' 增强，2026-08-31 用户确认）──────────────

class TestBenchmarkApprox:
    def test_direct_match(self):
        from backend.services.index_valuation_service import IndexValuationService
        IndexValuationService._cache = [{
            "index": "沪深300", "pe": 12.8, "percentile_1y": 55.0,
            "zone": "合理", "advice": "合理区间", "updated": "2026-08-31",
        }]
        try:
            hint = IndexValuationService.match_fund_hint(
                "天弘沪深300ETF联接C", "沪深300指数收益率*95%+活期*5%",
            )
            assert hint is not None and "跟踪指数" in hint["message"]
        finally:
            IndexValuationService._cache = None

    def test_benchmark_approximate(self):
        """主动基金基准近似映射：取占比最高的支持指数，标注近似"""
        from backend.services.index_valuation_service import IndexValuationService
        IndexValuationService._cache = [{
            "index": "沪深300", "pe": 12.8, "percentile_1y": 55.0,
            "zone": "合理", "advice": "合理区间", "updated": "2026-08-31",
        }]
        try:
            hint = IndexValuationService.match_fund_hint(
                "广发远见智选混合C",
                "沪深300指数收益率*60%+人民币计价的恒生指数收益率*20%+中债-新综合财富(总值)指数收益率*20%",
            )
            assert hint is not None
            assert hint.get("approximate") is True
            assert "沪深300" in hint["message"] and "60%" in hint["message"]
        finally:
            IndexValuationService._cache = None

    def test_fixed_income_excluded(self):
        """固收+ 基金权益占比低，高低估不适用"""
        from backend.services.index_valuation_service import IndexValuationService
        hint = IndexValuationService.match_fund_hint(
            "华泰柏瑞易利灵活配置混合C",
            "中债-综合全价(总值)指数收益率*85%+中证A500指数收益率*15%",
            is_fixed_income=True,
        )
        assert hint is None

    def test_low_ratio_no_approx(self):
        """支持指数占比 <20% 不近似（无意义）"""
        from backend.services.index_valuation_service import IndexValuationService
        IndexValuationService._cache = [{
            "index": "沪深300", "pe": 12.8, "percentile_1y": 55.0,
            "zone": "合理", "advice": "x", "updated": "d",
        }]
        try:
            hint = IndexValuationService.match_fund_hint(
                "x", "沪深300指数收益率*10%+其他*90%",
            )
            assert hint is None
        finally:
            IndexValuationService._cache = None


# ── 特性开关 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_feature_flags_roundtrip():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from backend.database import Base
    import backend.models

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    from backend.routers.system_config import (
        get_feature_flags, update_feature_flags,
    )
    async with Session() as db:
        # 默认全开
        r = await get_feature_flags(db)
        assert r.data == {"etf_hints_enabled": True, "otc_hints_enabled": True}
        # 关闭场内
        r = await update_feature_flags({"etf_hints_enabled": False}, db)
        assert r.data["etf_hints_enabled"] is False
        assert r.data["otc_hints_enabled"] is True
        # 非法键 → 400
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await update_feature_flags({"bad_key": True}, db)
        # 非 bool → 400
        with pytest.raises(HTTPException):
            await update_feature_flags({"etf_hints_enabled": "false"}, db)
    await engine.dispose()


class TestTypeEquivalenceStructured:
    """结构化类型等价（2026-09-12 用户日志反馈回归）"""

    def test_user_reported_cases_no_warning(self):
        from backend.services.fund_tag_service import _types_equivalent as eq
        assert eq("QDII-混合偏股", "QDII-混合")       # 016702
        assert eq("混合型-灵活", "混合型-灵活配置")   # 005851
        assert eq("指数型-股票", "股票型-标准指数")   # 023639
        assert eq("股票型", "股票型-普通")             # 018495

    def test_real_conflicts_still_detected(self):
        from backend.services.fund_tag_service import _types_equivalent as eq
        assert not eq("股票型", "债券型")
        assert not eq("混合型-偏股", "混合型-灵活")
        assert not eq("指数型-股票", "混合型-偏股")
        assert not eq("QDII-混合偏股", "QDII-混合债券")

    def test_cross_dimension_not_conflict(self):
        """2026-09-24 告警去噪：两源各报一个维度（组织形式 vs 投资范围）不算冲突

        原 "QDII vs 混合型-偏股 视为冲突" 的口径一并修正：QDII 是投资资质，
        与混合偏股可同真，跨维度比较不产生真信号，只产生假告警。
        """
        from backend.services.fund_tag_service import _types_equivalent as eq
        assert eq("指数型-海外股票", "QDII-股票")       # 162719 等，8 条假告警
        assert eq("指数型-其他", "商品型-非QDII")        # 4 条假告警
        assert eq("债券型-混合二级", "债券型-普通债券")  # 雪球粗桶，4 条假告警
        assert eq("QDII", "混合型-偏股")


# ── 组合 X 光（借鉴 fundadvisor，2026-09-24）─────────────────────────

class TestXrayScore:
    def _fund(self, code, name, manager="", company="", holdings=None):
        return {"code": code, "name": name, "manager": manager,
                "company": company, "holdings": holdings or []}

    def test_concentrated_portfolio_low_score(self):
        """两只基金重仓完全相同 → 高重叠扣分 + 警告"""
        from backend.services.portfolio_xray_service import xray_score
        holds = [("600001", "甲", 8.0), ("600002", "乙", 7.0), ("600003", "丙", 6.0),
                 ("600004", "丁", 5.0), ("600005", "戊", 5.0), ("600006", "己", 5.0)]
        funds = [
            self._fund("A", "基金A", "经理1", "公司1", holds),
            self._fund("B", "基金B", "经理1", "公司1", holds),  # 同经理同持仓
        ]
        r = xray_score(funds)
        assert r["score"] < 70, f"高度重叠应低分，实际 {r['score']}"
        assert any("重叠" in w for w in r["warnings"])
        assert any("同一经理" in w for w in r["warnings"])
        assert r["overlaps"][0]["common"] == 6

    def test_diversified_portfolio(self):
        """持仓完全不同 → 无重叠警告；两基金各 50% 同为单一经理 →
        触发 MINOR 经理集中警告（35% 阈值，设计内行为）"""
        from backend.services.portfolio_xray_service import xray_score
        funds = [
            self._fund("A", "A", "m1", "c1",
                       [(f"60000{i}", f"股{i}", 3.0) for i in range(10)]),
            self._fund("B", "B", "m2", "c2",
                       [(f"00010{i}", f"股{i}", 3.0) for i in range(10)]),
        ]
        r = xray_score(funds)
        assert r["overlaps"] == [] or all(o["common"] == 0 for o in r["overlaps"])
        assert r["deductions"]["overlap"] == 0.0
        assert r["deductions"]["hhi"] == 0.0  # 20 只不同股，HHI 极低
        assert any("同一经理" in w or "集中于经理" in w for w in r["warnings"])

    def test_jaccard_math(self):
        from backend.services.portfolio_xray_service import xray_score
        funds = [
            self._fund("A", "A", holdings=[("1", "a", 5.0), ("2", "b", 5.0),
                                           ("3", "c", 5.0), ("4", "d", 5.0)]),
            self._fund("B", "B", holdings=[("3", "c", 5.0), ("4", "d", 5.0),
                                           ("5", "e", 5.0), ("6", "f", 5.0)]),
        ]
        r = xray_score(funds)
        # 交集 2 / 并集 6 = 0.333
        assert r["overlaps"][0]["jaccard"] == 0.333

    def test_single_fund_penalty(self):
        """单基金且单股 → HHI 满罚 35 + 单基金 20 = 45 分"""
        from backend.services.portfolio_xray_service import xray_score
        r = xray_score([self._fund("A", "A", holdings=[("1", "a", 5.0)])])
        assert r["deductions"]["single_fund"] == 20.0
        assert r["deductions"]["hhi"] == 35.0
        assert r["score"] == 45.0


# ── 建议自进化存储（2026-09-24）──────────────────────────────────────

class TestAdviceLearning:
    def test_log_and_stats(self, tmp_path, monkeypatch):
        from backend.services import advice_learning_service as mod
        dbfile = tmp_path / "al.db"
        monkeypatch.setattr(mod, "AdviceLearningStore", mod.AdviceLearningStore)
        import importlib
        # 注入临时路径：直接构造实例并替换连接
        store = mod.AdviceLearningStore.__new__(mod.AdviceLearningStore)
        import sqlite3, threading
        store._w = threading.Lock()
        store._conn = sqlite3.connect(str(dbfile), check_same_thread=False)
        store._conn.executescript(mod._SCHEMA)
        store._conn.commit()

        store.log_advice("016874", "sell", "测试卖出", 2.0)
        store.record_outcome(1, "sell", -3.5, 0.5)  # sell 跌 → hit=1
        st = store.stats()
        assert st["sell_total"] == 1 and st["sell_hits"] == 1

    def test_pending_evaluations(self, tmp_path):
        from backend.services import advice_learning_service as mod
        import sqlite3, threading
        from datetime import datetime, timedelta
        store = mod.AdviceLearningStore.__new__(mod.AdviceLearningStore)
        store._w = threading.Lock()
        store._conn = sqlite3.connect(str(tmp_path / "al2.db"), check_same_thread=False)
        store._conn.executescript(mod._SCHEMA)
        # 40 天前的建议（到评估期）
        old_ts = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
        store._conn.execute(
            "INSERT INTO advice_log (ts, fund_code, action) VALUES (?, 'x', 'sell')",
            (old_ts,))
        # 昨天的建议（未到期）
        y_ts = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        store._conn.execute(
            "INSERT INTO advice_log (ts, fund_code, action) VALUES (?, 'y', 'buy')",
            (y_ts,))
        store._conn.commit()
        pend = store.pending_evaluations()
        assert len(pend) == 1 and pend[0]["fund_code"] == "x"
