"""潜力 ETF 扫描三态可观测性回归（2026-10-17 根因修复）

用户看到的"扫描出来 0 条数据"实际有三种完全不同的原因，此前返回体同形：
1. `system_config.etf_hints_enabled = "false"` → 端点首行短路（生产/开发库都是这种）；
2. 东财全市场快照被熔断/拒绝 → 只有 error 文案，且写死"约 10 分钟"与真实冷却无关；
3. 快照到手但全是腾讯降级行（不含量比/换手等场内字段）→ 整批被跳过。

补丁域名那条另见 test_url_safety.TestEastMoneyPush2DelayHost。

本文件零真实上游请求：`_get_etf_spot` 全部 monkeypatch。
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from backend.models.system_config import SystemConfig  # noqa: E402
from backend.routers.fund import etf_scan  # noqa: E402
from backend.services import fund_realtime_service as _frs  # noqa: E402
from backend.services.etf_signal_service import scan_potential  # noqa: E402
from backend.services.etf_spot_tencent import (  # noqa: E402
    parse_tencent_etf_batch, to_tencent_etf_code,
)


@pytest.fixture(autouse=True)
def _isolate_breakers():
    """熔断状态是类属性，跑完必须还原，否则会让同进程其他用例误判源不可用"""
    saved = dict(_frs.FundRealtimeService._source_fail_until)
    yield
    _frs.FundRealtimeService._source_fail_until.clear()
    _frs.FundRealtimeService._source_fail_until.update(saved)


def _forbid_upstream(monkeypatch):
    """把快照入口换成"调用即失败"，用来锁定短路行为"""
    async def _boom(*a, **kw):
        raise AssertionError("开关关闭时不得发起全市场快照请求")
    monkeypatch.setattr(_frs.FundRealtimeService, "_get_etf_spot", _boom)


class TestClosedFlagState:
    async def test_disabled_returns_reason_and_no_upstream(self, db_session, monkeypatch):
        db_session.add(SystemConfig(config_key="etf_hints_enabled", config_value="false"))
        await db_session.commit()
        _forbid_upstream(monkeypatch)

        d = (await etf_scan(db_session)).data
        assert d["enabled"] is False
        assert "功能开关" in d["reason"], "关闭态必须告诉用户去哪里开"
        assert (d["scanned"], d["snapshot_rows"], d["skipped_no_fields"]) == (0, 0, 0)
        assert d["movers"] == [] and d["unusual"] == []
        assert "error" not in d, "关闭态不是故障态，不得同时报 error"


class TestSourceUnavailableState:
    async def test_error_names_breaking_source_and_remaining_seconds(
        self, db_session, monkeypatch
    ):
        async def _none(self, codes=None):
            return None
        monkeypatch.setattr(_frs.FundRealtimeService, "_get_etf_spot", _none)
        _frs.FundRealtimeService._source_fail_until[_frs.EM_SOURCE] = time.time() + 300

        d = (await etf_scan(db_session)).data
        assert d["enabled"] is True and d["scanned"] == 0
        assert _frs.EM_SOURCE in d["error"]
        assert "剩余" in d["error"], "不能再写死「约 10 分钟」"
        assert "10 分钟后自动恢复" not in d["error"]

    async def test_tencent_fallback_success_returns_source_and_full_scan(
        self, db_session, monkeypatch
    ):
        """2026-10-19 根因修复：东财挂 → 腾讯全链路 → 快照可扫描且标注 source"""
        spot = {
            "510300": {
                "name": "沪深300ETF", "price": 4.24, "pct": 0.09,
                "turnover_rate": 4.15, "volume_ratio": 0.75,
                "main_inflow_pct": 29.75, "amount": 5.1371e9,
                "iopv": None, "premium_discount": None,
                "source": _frs.TX_ALL_SOURCE,
            },
        }

        async def _tx_full(self):
            return spot
        monkeypatch.setattr(
            _frs.FundRealtimeService, "_get_etf_spot_tencent_full", _tx_full,
        )

        d = (await etf_scan(db_session)).data
        assert d["source"] == _frs.TX_ALL_SOURCE
        assert d["scanned"] == 1, "腾讯行含量价字段，必须参与扫描"

    async def test_no_snapshot_error_mentions_tencent_fallback(
        self, db_session, monkeypatch
    ):
        """双链路都失败时的空态：文案必须承认腾讯链路存在（2026-10-19 已接入）"""
        async def _none(self, codes=None):
            return None
        monkeypatch.setattr(_frs.FundRealtimeService, "_get_etf_spot", _none)

        d = (await etf_scan(db_session)).data
        assert "腾讯全链路" in d["error"] or "自动切换" in d["error"], (
            "空态文案仍在宣称『腾讯降级链路不含量价字段，不做替代』"
            "——已过时，会误导用户认为功能无救"
        )


class TestDegradedSnapshotState:
    async def test_tencent_rows_without_fields_are_counted_not_hidden(
        self, db_session, monkeypatch
    ):
        """快照收到 2 行但都不含量价字段 → scanned=0 且必须能自证原因"""
        spot = {
            "510300": {"name": "沪深300ETF", "price": 4.0, "pct": 1.0},
            "510500": {"name": "中证500ETF", "price": 6.0, "pct": -0.5},
        }

        async def _degraded(self, codes=None):
            return spot
        monkeypatch.setattr(_frs.FundRealtimeService, "_get_etf_spot", _degraded)

        d = (await etf_scan(db_session)).data
        assert (d["scanned"], d["snapshot_rows"], d["skipped_no_fields"]) == (0, 2, 2)
        assert "error" not in d, "拿到快照就不是数据源不可用，不能报 error"


class TestScanCounters:
    def test_counters_match_row_split(self):
        full = {"name": "A", "pct": 3.0, "volume_ratio": 2.0,
                "main_inflow_pct": 12.0, "turnover_rate": 1.0, "amount": 2e8}
        lean = {"name": "B", "pct": 1.0, "price": 1.0}
        r = scan_potential({"510001": full, "510002": lean, "510003": lean}, set())
        assert (r["scanned"], r["snapshot_rows"], r["skipped_no_fields"]) == (1, 3, 2)

    def test_empty_map_counters(self):
        r = scan_potential({}, set())
        assert (r["scanned"], r["snapshot_rows"], r["skipped_no_fields"]) == (0, 0, 0)


class TestTencentEtfParser:
    """腾讯全链路解析器（2026-10-19 替代源，字段语义实测校准）"""

    # 真实抓包 payload（2026-10-19 q=sh510300），88 字段全量；
    # 关键下标（实测）: [6]外盘12105648 [7]内盘6554321 [32]涨跌0.09
    # [37]成交额万513710 [38]换手4.24 [49]量比0.75
    RAW = (
        'v_sh510300="1~沪深300ETF华泰柏瑞~510300~4.240~4.236~4.224~'
        '12105648~6554321~5551327~4.240~15993~4.239~2564~4.238~2333~'
        '4.237~2217~4.236~6292~4.241~1315~4.242~5248~4.243~369~4.244~5235~'
        '4.245~5900~~20261019161439~0.004~0.09~4.267~4.220~'
        '4.240/12105648/5137102820~12105648~513710~4.24~~~4.267~4.220~1.11~'
        '1210.65~1210.65~0.00~4.660~3.812~0.75~11332~4.244~~~~~~'
        '513710.2820~308.8840~7285~   A~ETF~-8.42~-2.75~~~~5.095~4.187~'
        '-4.01~-7.40~-7.61~28552987700~28552987700~23.87~-10.77~'
        '28552987700~-0.16~4.2469~-5.80~-0.14~4.2473~CNY~0~___D__F__N~'
        '4.235~7027~";'
        'v_pv_none_match="1";'
    )
    # 关键下标（实测 2026-10-19）: [6]外盘12105648 [7]内盘6554321
    # [32]涨跌0.09 [37]成交额万513710 [38]换手4.24 [49]量比0.75

    def test_batch_parse_fields(self):
        out = parse_tencent_etf_batch(self.RAW)
        assert "510300" in out and "518880" not in out, (
            "v_pv_none_match 必须被跳过"
        )
        row = out["510300"]
        assert row["price"] == 4.24
        assert row["pct"] == 0.09
        # f49 才是与东财同口径的量比（f43 是盘口动态量比，勿用）
        assert row["volume_ratio"] == 0.75
        assert row["turnover_rate"] == 4.24
        assert row["amount"] == 513710 * 1e4
        # 主买占比 = (外盘−内盘)/(外盘+内盘)
        expect = (12105648 - 6554321) / (12105648 + 6554321) * 100
        assert abs(row["main_inflow_pct"] - expect) < 1e-6
        assert row["name"] == "沪深300ETF华泰柏瑞"
        assert row["time"] == "20261019161439"

    def test_code_mapping(self):
        assert to_tencent_etf_code("510300") == "sh510300"   # 沪 ETF 51x
        assert to_tencent_etf_code("588000") == "sh588000"   # 沪 58x 科创
        assert to_tencent_etf_code("159915") == "sz159915"   # 深 15x
        assert to_tencent_etf_code("511010") == "sh511010"   # 债券 ETF

    def test_scan_potential_accepts_tencent_row(self):
        """腾讯行(主买口径)必须能进入原有扫描规则（量价齐升榜）"""
        out = parse_tencent_etf_batch(self.RAW)
        row = out["510300"]
        row["pct"] = 3.0
        row["volume_ratio"] = 2.0   # 人为抬到 movers 阈值以上验证规则路径
        r = scan_potential({"510300": {**row, "source": "tencent_etf_full"}}, set())
        assert r["scanned"] == 1
        assert len(r["movers"]) == 1, "pct>2 + 量比>1.5 + 主买占比>0 应进量价齐升榜"
        assert r["movers"][0]["code"] == "510300"


class TestDegradedPriorityByCallShape:
    """东财不可用时的分流语义（2026-10-19 审查发现的性能回归护栏）

    历史坑：两处失败分支原本"先全链路再按需"，东财熔断时仪表盘为了 3~10 只
    ETF 也要付一次全市场 34 批扫描（实测 5.2s vs 按需 0.2s）。这里把调用形态
    与优先级的关系钉死，防止将来又被"简化"回去。
    """

    @staticmethod
    def _svc():
        return _frs.FundRealtimeService.__new__(_frs.FundRealtimeService)

    async def test_codes_given_prefers_on_demand_over_full_scan(self, monkeypatch):
        """仪表盘估值（codes 给定）必须走单批按需，不许触发全市场扫描"""
        calls = []

        async def _on_demand(self, codes, now):
            calls.append("on_demand")
            return {"510300": {"name": "沪深300ETF", "price": 4.24, "pct": 0.09}}

        async def _full(self):
            calls.append("full")
            raise AssertionError("codes 给定时不该付全市场扫描的代价")

        monkeypatch.setattr(
            _frs.FundRealtimeService, "_get_etf_spot_tencent_on_demand", _on_demand,
        )
        monkeypatch.setattr(
            _frs.FundRealtimeService, "_get_etf_spot_tencent_full", _full,
        )
        svc = self._svc()
        svc._source_available = lambda src: True  # type: ignore[method-assign]

        out = await svc._get_etf_spot_degraded(["510300"], 0.0)
        assert out and "510300" in out
        assert calls == ["on_demand"]

    async def test_codes_none_goes_full_scan(self, monkeypatch):
        """榜单扫描（codes=None）必须走全链路——按需拿不到量价字段"""
        calls = []

        async def _on_demand(self, codes, now):
            calls.append("on_demand")
            return {}

        async def _full(self):
            calls.append("full")
            return {"510300": {"name": "沪深300ETF", "volume_ratio": 0.75}}

        monkeypatch.setattr(
            _frs.FundRealtimeService, "_get_etf_spot_tencent_on_demand", _on_demand,
        )
        monkeypatch.setattr(
            _frs.FundRealtimeService, "_get_etf_spot_tencent_full", _full,
        )
        svc = self._svc()
        svc._source_available = lambda src: True  # type: ignore[method-assign]

        out = await svc._get_etf_spot_degraded(None, 0.0)
        assert out and "510300" in out
        assert "full" in calls, "codes=None 必须触发全链路"
        assert "on_demand" not in calls, "codes=None 不该先走按需（补不出量价）"

    async def test_on_demand_failure_falls_back_to_full_scan(self, monkeypatch):
        """按需失败 → 仍要退全链路（宁可慢也要有数，不能直接空）"""
        async def _on_demand(self, codes, now):
            return None

        async def _full(self):
            return {"510300": {"name": "沪深300ETF", "volume_ratio": 0.75}}

        monkeypatch.setattr(
            _frs.FundRealtimeService, "_get_etf_spot_tencent_on_demand", _on_demand,
        )
        monkeypatch.setattr(
            _frs.FundRealtimeService, "_get_etf_spot_tencent_full", _full,
        )
        svc = self._svc()
        svc._source_available = lambda src: True  # type: ignore[method-assign]

        out = await svc._get_etf_spot_degraded(["510300"], 0.0)
        assert out and "510300" in out

    async def test_both_links_down_returns_none(self, monkeypatch):
        """两条链路都不可用 → 诚实返回 None，不拿半张快照冒充全市场"""
        async def _on_demand(self, codes, now):
            return None

        async def _full(self):
            return None

        monkeypatch.setattr(
            _frs.FundRealtimeService, "_get_etf_spot_tencent_on_demand", _on_demand,
        )
        monkeypatch.setattr(
            _frs.FundRealtimeService, "_get_etf_spot_tencent_full", _full,
        )
        svc = self._svc()
        svc._source_available = lambda src: True  # type: ignore[method-assign]

        assert await svc._get_etf_spot_degraded(None, 0.0) is None
        assert await svc._get_etf_spot_degraded(["510300"], 0.0) is None
