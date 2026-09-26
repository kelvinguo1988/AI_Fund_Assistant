"""② fund_quarterly 写入链路 + 仓位感知估值

背景：第零层质量过滤（清盘否决 / 规模冲击 / 仓位漂移 / 机构认可度）全部读
`fund_quarterly`，但这张表长期零写入 → 四项检查一直按中性处理。数据其实早已
在「刷新数据」时抓到（pingzhongdata），只是没落库。

覆盖：报告期对齐与单位换算（亿元→元）、生效日期（严禁未披露数据提前生效）、
upsert 不清空历史数值、质量过滤据此真实触发。
"""

import json
from datetime import date

import pytest
from sqlalchemy import select

from backend.models.fund import Fund
from backend.models.fund_quarterly import FundQuarterly
from backend.services.fund_quarterly_service import (
    build_quarterly_rows,
    report_effective_date,
    sync_quarterly_from_extended,
    sync_quarterly_rows,
)


# 结构取自 2026-09-26 实测的 161725 pingzhongdata（数值原样保留）
EXTENDED_161725 = {
    "asset_allocation": {
        "series": [
            {"name": "股票占净比", "data": [94.54, 94.44, 94.62, 94.79]},
            {"name": "债券占净比", "data": [0.0, 0.0, 0.0, 0.0]},
            {"name": "现金占净比", "data": [6.15, 6.02, 5.73, 6.15]},
            {"name": "净资产", "data": [473.3, 431.9203, 402.2078, 313.0236]},
        ],
        "categories": ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"],
    },
    "fluctuation_scale": {
        "categories": ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"],
        "series": [
            {"y": 295.72, "mom": "-11.68%"},
            {"y": 323.14, "mom": "9.27%"},
            {"y": 284.95, "mom": "-11.82%"},
            {"y": 258.37, "mom": "-9.33%"},
            {"y": 197.40, "mom": "-23.60%"},
        ],
    },
    "holder_structure": {
        "series": [
            {"name": "机构持有比例", "data": [2.09, 2.07, 0.97, 0.78]},
            {"name": "个人持有比例", "data": [97.91, 97.93, 99.03, 99.22]},
            {"name": "内部持有比例", "data": [0.0069, 0.0075, 0.0097, 0.0117]},
        ],
        "categories": ["2024-12-31", "2025-06-30", "2025-12-31", "2026-06-30"],
    },
    "buy_sedemption": {
        "series": [
            {"name": "期间申购", "data": [92.34, 54.83, 77.65, 54.83]},
            {"name": "期间赎回", "data": [89.82, 65.37, 74.93, 71.12]},
            {"name": "总份额", "data": [411.96, 401.42, 404.14, 387.85]},
        ],
        "categories": ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"],
    },
    "grand_total": None,
}


class TestEffectiveDate:
    @pytest.mark.parametrize("rpt,eff", [
        ("2026-03-31", "2026-05-01"),   # 一季报 +2 月，05-01 周五
        ("2025-06-30", "2025-09-01"),   # 半年报 8 月底才披露 → +3 月
        ("2025-09-30", "2025-11-03"),   # 三季报 +2 月；11-01 周六 → 顺延 11-03
        ("2025-12-31", "2026-03-02"),   # 年报次年 3 月底披露；03-01 周日 → 03-02
    ])
    def test_rule(self, rpt, eff):
        assert report_effective_date(rpt) == eff

    def test_bad_input_passthrough(self):
        assert report_effective_date("bad") == "bad"


class TestBuildRows:
    def test_report_dates_are_union_and_sorted(self):
        rows = build_quarterly_rows(EXTENDED_161725)
        assert [r["report_date"] for r in rows] == [
            "2024-12-31", "2025-06-30", "2025-09-30", "2025-12-31",
            "2026-03-31", "2026-06-30",
        ]

    def test_units_converted(self):
        by_date = {r["report_date"]: r for r in build_quarterly_rows(EXTENDED_161725)}
        q1 = by_date["2026-03-31"]
        # 亿元 → 元（quality_filter 阈值是 5e7 / 1e8 元量级）
        assert q1["fund_size"] == pytest.approx(258.37 * 1e8)
        assert q1["stock_position_ratio"] == pytest.approx(94.62)
        # 半年度报告期没有资产分配数据，只有规模与持有人结构
        assert q1["institution_holding_ratio"] is None
        assert by_date["2025-12-31"]["institution_holding_ratio"] == pytest.approx(0.97)
        assert by_date["2025-12-31"]["stock_position_ratio"] == pytest.approx(94.44)

    def test_insider_shares_from_ratio_times_total_shares(self):
        by_date = {r["report_date"]: r for r in build_quarterly_rows(EXTENDED_161725)}
        # 0.0117% × 387.85 亿份
        assert by_date["2026-06-30"]["insider_holding_shares"] == pytest.approx(
            0.0117 / 100 * 387.85 * 1e8, rel=1e-6
        )
        # 内部人比例有、总份额没有（半年度日期不在申赎 categories 里）→ 不猜
        assert by_date["2024-12-31"]["insider_holding_shares"] is None

    def test_size_falls_back_to_asset_allocation(self):
        """规模变动只有 5 期，另一来源可补上缺的报告期"""
        ext = {"asset_allocation": EXTENDED_161725["asset_allocation"]}
        rows = build_quarterly_rows(ext)
        assert rows[0]["fund_size"] == pytest.approx(473.3 * 1e8)

    def test_empty_extended_yields_nothing(self):
        assert build_quarterly_rows({}) == []
        assert build_quarterly_rows({"asset_allocation": None, "holder_structure": None}) == []


class TestUpsert:
    @pytest.mark.asyncio
    async def test_sync_writes_and_updates_without_dupes(self, db_session):
        fund = Fund(code="161725", name="测试基金", fund_type="otc", status="active")
        db_session.add(fund)
        await db_session.flush()

        funds, rows = await sync_quarterly_from_extended(
            db_session, {"161725": EXTENDED_161725}, {"161725": fund.id}
        )
        assert (funds, rows) == (1, 6)

        # 再来一次（模拟下一次刷新）：不新增行，数值刷新
        updated = dict(EXTENDED_161725)
        updated["fluctuation_scale"] = {
            "categories": ["2026-09-30"],
            "series": [{"y": 180.0, "mom": "-8.8%"}],
        }
        await sync_quarterly_from_extended(db_session, {"161725": updated}, {"161725": fund.id})

        stored = list((await db_session.execute(
            select(FundQuarterly).order_by(FundQuarterly.report_date)
        )).scalars().all())
        assert len(stored) == 7
        assert [r.report_date for r in stored][-1] == "2026-09-30"
        assert stored[-1].fund_size == pytest.approx(180.0 * 1e8)
        assert stored[-1].effective_date == "2026-11-02"  # 三季报 +2 月，11-01 周日顺延

    @pytest.mark.asyncio
    async def test_null_from_new_source_keeps_history(self, db_session):
        """总份额某期缺失时不把已落库的份额清空"""
        fund = Fund(code="004011", name="测试基金", fund_type="otc", status="active")
        db_session.add(fund)
        await db_session.flush()
        await sync_quarterly_rows(db_session, fund.id, [{
            "report_date": "2026-03-31", "effective_date": "2026-05-04",
            "fund_size": 1e9, "stock_position_ratio": 90.0,
            "institution_holding_ratio": 5.0, "insider_holding_shares": 12345.0,
        }])
        await sync_quarterly_rows(db_session, fund.id, [{
            "report_date": "2026-03-31", "effective_date": "2026-05-04",
            "fund_size": None, "stock_position_ratio": None,
            "institution_holding_ratio": 6.0, "insider_holding_shares": None,
        }])
        row = (await db_session.execute(select(FundQuarterly))).scalars().one()
        assert row.fund_size == pytest.approx(1e9)
        assert row.stock_position_ratio == pytest.approx(90.0)
        assert row.institution_holding_ratio == pytest.approx(6.0)
        assert row.insider_holding_shares == pytest.approx(12345.0)

    @pytest.mark.asyncio
    async def test_code_outside_pool_skipped(self, db_session):
        funds, rows = await sync_quarterly_from_extended(
            db_session, {"968049": EXTENDED_161725}, {}
        )
        assert (funds, rows) == (0, 0)


class TestQualityChecksEngaged:
    """落库后第零层检查真的会触发（此前恒为中性）"""

    def _rows(self, positions, sizes):
        return [
            {
                "report_date": f"2025-{m:02d}-31",
                "effective_date": eff,
                "fund_size": s,
                "stock_position_ratio": p,
                "institution_holding_ratio": None,
                "insider_holding_shares": None,
            }
            for (m, eff), p, s in zip(
                [(3, "2025-05-01"), (6, "2025-09-01"), (9, "2025-11-03"), (12, "2026-03-02")],
                positions, sizes
            )
        ]

    def test_drift_detected(self):
        from backend.engines.quality_filter import check_allocation_drift
        rows = self._rows([94.0, 93.0, 50.0, 92.0], [1e9] * 4)
        is_drift, high_purity = check_allocation_drift(rows, today=date(2026, 9, 26))
        assert is_drift is True
        assert high_purity is False

    def test_high_purity_detected(self):
        from backend.engines.quality_filter import check_allocation_drift
        rows = self._rows([94.0, 94.2, 94.1, 94.3], [1e9] * 4)
        assert check_allocation_drift(rows, today=date(2026, 9, 26)) == (False, True)

    def test_size_shock_detected(self):
        from backend.engines.quality_filter import check_size_shock
        rows = self._rows([94.0] * 4, [1e8, 1e8, 1.2e8, 3e8])
        assert check_size_shock(rows, today=date(2026, 9, 26)) is True

    def test_future_report_not_used(self):
        """effective_date 未到的报告期不得参与判定（严禁未来数据泄露）"""
        from backend.engines.quality_filter import check_size_shock
        rows = self._rows([94.0] * 4, [1e8, 1e8, 1.2e8, 3e8])
        assert check_size_shock(rows, today=date(2025, 8, 1)) is False


class TestPositionLoaderForEstimate:
    """仓位感知估值的取数：只认已生效的最新一期股票仓位"""

    @pytest.mark.asyncio
    async def test_picks_latest_effective_only(self, db_session):
        from backend.services.fund_realtime_service import FundRealtimeService

        fund = Fund(code="161725", name="测试基金", fund_type="otc", status="active")
        other = Fund(code="004011", name="无季度数据", fund_type="otc", status="active")
        db_session.add_all([fund, other])
        await db_session.flush()
        await sync_quarterly_rows(db_session, fund.id, [
            {"report_date": "2025-09-30", "effective_date": "2025-11-03",
             "fund_size": 3e10, "stock_position_ratio": 94.62,
             "institution_holding_ratio": None, "insider_holding_shares": None},
            {"report_date": "2025-12-31", "effective_date": "2026-03-02",
             "fund_size": 3e10, "stock_position_ratio": 94.79,
             "institution_holding_ratio": None, "insider_holding_shares": None},
            # 未来才生效（2099）：估值不得引用未披露仓位
            {"report_date": "2099-03-31", "effective_date": "2099-05-04",
             "fund_size": 3e10, "stock_position_ratio": 10.0,
             "institution_holding_ratio": None, "insider_holding_shares": None},
        ])

        svc = FundRealtimeService(db_session)
        positions = await svc._latest_stock_positions([fund.id, other.id])
        assert list(positions) == [fund.id]
        assert positions[fund.id] == pytest.approx(94.79)

    @pytest.mark.asyncio
    async def test_empty_ids_no_query(self, db_session):
        from backend.services.fund_realtime_service import FundRealtimeService
        assert await FundRealtimeService(db_session)._latest_stock_positions([]) == {}
