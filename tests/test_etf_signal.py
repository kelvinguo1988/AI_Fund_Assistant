"""场内 ETF 信号服务测试 — 提示规则 + 潜力扫描过滤"""

import sys, os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.etf_signal_service import (
    evaluate_etf_hints, scan_potential,
)


def _spot(**kw):
    base = {"name": "测试ETF", "price": 1.0, "pct": 1.0, "iopv": 1.0,
            "turnover_rate": 2.0, "volume_ratio": 1.0, "main_inflow_pct": 0.0,
            "amount": 5e8}
    base.update(kw)
    return base


class TestEvaluateHints:
    def test_premium_warning(self):
        """溢价 > 1% → 追高警示"""
        hints = evaluate_etf_hints(_spot(price=1.05, iopv=1.0))  # 溢价 5%
        assert any(h["type"] == "premium" and h["level"] == "warning" for h in hints)

    def test_discount_positive(self):
        """折价 < -0.5% → 低成本买入提示"""
        hints = evaluate_etf_hints(_spot(price=0.99, iopv=1.0))  # 折价 1%
        assert any(h["type"] == "discount" and h["level"] == "positive" for h in hints)

    def test_liquidity_block(self):
        """成交额 < 1000 万 → 流动性警示"""
        hints = evaluate_etf_hints(_spot(amount=5e6))
        assert any(h["type"] == "liquidity" for h in hints)

    def test_outflow_warning(self):
        """量比 > 2 且主力净流出 > 10% → 放量流出"""
        hints = evaluate_etf_hints(_spot(volume_ratio=3.0, main_inflow_pct=-15.0))
        assert any(h["type"] == "outflow" for h in hints)

    def test_inflow_confirm(self):
        """量比 > 1.5 且主力净流入 > 10% → 放量流入确认"""
        hints = evaluate_etf_hints(_spot(volume_ratio=2.0, main_inflow_pct=15.0))
        assert any(h["type"] == "inflow" and h["level"] == "positive" for h in hints)

    def test_tencent_degraded_no_crash(self):
        """腾讯降级（场内字段全 None）→ 无提示不崩溃"""
        hints = evaluate_etf_hints(_spot(volume_ratio=None, main_inflow_pct=None,
                                         turnover_rate=None, amount=None, iopv=None))
        assert hints == []

    def test_normal_etf_no_hints(self):
        """正常行情 → 无提示"""
        hints = evaluate_etf_hints(_spot())
        assert hints == []


class TestScanPotential:
    def test_filters_and_pool_marking(self):
        spot_map = {
            # 量价齐升：涨 3% + 量比 2 + 主力流入
            "510001": _spot(name="A", pct=3.0, volume_ratio=2.0, main_inflow_pct=12.0, amount=2e8),
            # 资金流入：主力 25% 高成交
            "510002": _spot(name="B", pct=0.5, volume_ratio=1.0, main_inflow_pct=25.0, amount=3e8),
            # 异动：换手 8%
            "510003": _spot(name="C", pct=-1.0, volume_ratio=1.0, main_inflow_pct=-5.0,
                            turnover_rate=8.0, amount=5e7),
            # 成交额过小 → 全部榜单排除
            "510004": _spot(name="D", pct=5.0, volume_ratio=4.0, main_inflow_pct=30.0,
                            amount=5e6),
            # 腾讯降级行（无场内字段）→ 排除
            "510005": _spot(name="E", volume_ratio=None, main_inflow_pct=None,
                            turnover_rate=None, amount=None),
        }
        result = scan_potential(spot_map, pool_codes={"510001"})
        assert result["scanned"] == 4  # 510005（腾讯降级无字段）被排除
        # 量价齐升
        assert [x["code"] for x in result["movers"]] == ["510001"]
        assert result["movers"][0]["in_pool"] is True
        # 资金流入 Top（510002 主力 25% 最高）
        assert result["inflow"][0]["code"] == "510002"
        # 异动含 510003（换手 8% > 5%）
        assert any(x["code"] == "510003" for x in result["unusual"])

    def test_empty_map(self):
        result = scan_potential({}, set())
        assert result == {"scanned": 0, "movers": [], "inflow": [], "unusual": []}
