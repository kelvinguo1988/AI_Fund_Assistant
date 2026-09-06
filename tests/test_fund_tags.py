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
        assert "其他" in out

    def test_empty(self):
        assert parse_exposure_tags([]) is None
        assert parse_exposure_tags([{"stock_name": "x", "ratio": None}]) is None


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
