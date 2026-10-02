"""单只基金无记录不得降级整源（2026-10-01 审查 P1）

背景：DataSourceManager 把 get_fund_data 抛出的任何异常都记成"源坏了"，
于是基金池里一只已清盘的代码就能让 AKShare 降级 5 分钟 —— 之后所有基金
改打备源、仪表盘显示数据源故障、告警推送报"断供"，而真正的原因只是这一
个代码没有记录。修法是引入 NoDataError 把"代码级答案"从"源健康信号"里
摘出来。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from backend.data_sources.base import FundData, NoDataError  # noqa: E402
from backend.data_sources.data_source_manager import DataSourceManager, _SourceStatus  # noqa: E402


class _FakeAdapter:
    """按代码返回数据 / 抛 NoDataError / 抛真实故障"""

    def __init__(self, name, mapping=None, raises=None):
        self.name = name
        self._mapping = mapping or {}
        self._raises = raises or {}
        self.requested: list[str] = []

    @property
    def available(self):
        return True

    async def probe(self):
        return True

    async def get_fund_data(self, code, period=250, fund_type=None):
        self.requested.append(code)
        exc = self._raises.get(code)
        if exc is not None:
            raise exc
        return self._mapping[code]

    async def get_market_indices(self):
        from backend.data_sources.base import MarketIndices
        return MarketIndices()

    async def get_bond_yield(self):
        return None


def _manager(*adapters) -> DataSourceManager:
    mgr = DataSourceManager.__new__(DataSourceManager)
    mgr._sources = [
        _SourceStatus(a.name, a, idx) for idx, a in enumerate(adapters)
    ]
    return mgr


def _nav(code):
    hist = [1.0 + i * 0.01 for i in range(40)]
    return FundData(code=code, name=f"F{code}", date="2026-09-25",
                    close=hist[-1], close_history=hist)


class TestManagerTreatsNoDataAsCodeLevel:
    @pytest.mark.asyncio
    async def test_no_data_error_keeps_source_active(self):
        """死代码不降级：源状态保持 active，异常原样上抛交调用方跳过"""
        primary = _FakeAdapter("AKShare", raises={"968049": NoDataError("无记录 code=968049")})
        backup = _FakeAdapter("JoinQuant", mapping={"968049": _nav("968049")})
        mgr = _manager(primary, backup)

        with pytest.raises(NoDataError):
            await mgr.get_fund_data("968049")

        assert primary.requested == ["968049"]
        status = {s["name"]: s for s in mgr.source_status}
        assert status["AKShare"]["active"] is True
        assert status["AKShare"]["consecutive_failures"] == 0

    @pytest.mark.asyncio
    async def test_no_data_error_does_not_poll_backup(self):
        """不轮询下一级：两家源覆盖同一基金全集，为死代码多打一个源只是上游压力"""
        primary = _FakeAdapter("AKShare", raises={"968049": NoDataError("无记录")})
        backup = _FakeAdapter("JoinQuant", mapping={"968049": _nav("968049")})
        mgr = _manager(primary, backup)

        with pytest.raises(NoDataError):
            await mgr.get_fund_data("968049")

        assert backup.requested == []

    @pytest.mark.asyncio
    async def test_healthy_funds_unaffected_after_dead_code(self):
        """死代码之后的正常基金仍然走主源（回归旧行为：整池被降级打备源）"""
        primary = _FakeAdapter(
            "AKShare",
            mapping={"004011": _nav("004011")},
            raises={"968049": NoDataError("无记录")},
        )
        backup = _FakeAdapter("JoinQuant", mapping={"004011": _nav("004011")})
        mgr = _manager(primary, backup)

        with pytest.raises(NoDataError):
            await mgr.get_fund_data("968049")
        fd = await mgr.get_fund_data("004011")

        assert fd.code == "004011"
        assert primary.requested == ["968049", "004011"]
        assert backup.requested == []

    @pytest.mark.asyncio
    async def test_real_failure_still_degrades(self):
        """请求级失败必须照旧降级：NoDataError 不能变成新的静默吞异常口子"""
        primary = _FakeAdapter("AKShare", raises={"004011": RuntimeError("上游超时")})
        backup = _FakeAdapter("JoinQuant", mapping={"004011": _nav("004011")})
        mgr = _manager(primary, backup)

        fd = await mgr.get_fund_data("004011")

        assert fd.code == "004011"
        status = {s["name"]: s for s in mgr.source_status}
        assert status["AKShare"]["active"] is False
        assert status["AKShare"]["consecutive_failures"] == 1


class TestAdapterRaisesNoData:
    @pytest.mark.asyncio
    async def test_etf_empty_history_raises_no_data(self, monkeypatch):
        from backend.data_sources.akshare_adapter import AKShareAdapter

        adapter = AKShareAdapter()

        async def _none(*args, **kwargs):
            return pd.DataFrame()
        monkeypatch.setattr(adapter, "_call", _none)

        with pytest.raises(NoDataError):
            await adapter._get_etf_data("510399", 60)

    @pytest.mark.asyncio
    async def test_etf_no_data_still_falls_back_to_otc(self, monkeypatch):
        """主接口说没有时仍要试一次备接口：fund_type 标错的代码靠这条活路救回"""
        from backend.data_sources.akshare_adapter import AKShareAdapter

        adapter = AKShareAdapter()
        hit = {"etf": 0, "otc": 0}

        async def _etf(code, period):
            hit["etf"] += 1
            raise NoDataError("ETF 行情数据为空")

        async def _otc(code, period):
            hit["otc"] += 1
            return _nav(code)

        async def _noop(*args, **kwargs):
            return None

        monkeypatch.setattr(adapter, "_get_etf_data", _etf)
        monkeypatch.setattr(adapter, "_get_otc_fund_data", _otc)
        # 主备之外的补数字段要联网，本用例只验证路由
        monkeypatch.setattr(adapter, "get_bond_yield", _noop)
        monkeypatch.setattr(adapter, "_fill_benchmark_data", _noop)
        monkeypatch.setattr(adapter, "_fill_fund_size", _noop)

        fd = await adapter.get_fund_data("510300", period=60, fund_type="etf")

        assert fd.close_history
        assert hit == {"etf": 1, "otc": 1}
