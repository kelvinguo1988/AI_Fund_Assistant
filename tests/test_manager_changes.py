"""基金经理在任/离任口径测试（2026-09-26）

背景：记录按 (fund_id, manager_name) 唯一且只增不改，旧口径把插入顺序的最后
一个名字当现任，于是同一次刷新写入的共同在任经理全被报成"经理变更"
（实测 004011：郑青为"现任"，同日同批写入的董辰/闫泽君成"前任"）。
改为 last_seen_at 批次快照：最近一轮刷新确认在任的全部经理 = 现任。
"""

from datetime import datetime, timedelta

from sqlalchemy import select

from backend.models.fund import Fund
from backend.models.fund_manager_record import FundManagerRecord
from backend.services import fund_manager_service as fms


OLD = datetime(2026, 6, 14, 13, 9, 49)


async def _add(db, fund_id, name, seen, **kw):
    db.add(FundManagerRecord(
        fund_id=fund_id,
        manager_name=name,
        company=kw.get("company", "某基金"),
        tenure_days=kw.get("tenure_days", 1000),
        asset_scale=kw.get("asset_scale", 10.0),
        best_return=kw.get("best_return", 50.0),
        managed_codes=kw.get("managed_codes", "004011"),
        created_at=kw.get("created_at", seen),
        last_seen_at=seen,
    ))
    await db.commit()


class TestComputeManagerChanges:
    async def test_co_managers_same_batch_are_all_current(self, db_session):
        """同一批写入的多位经理 = 共同在任，不得报变更"""
        fund = Fund(code="004011", name="测试基金", fund_type="otc", status="active")
        db_session.add(fund)
        await db_session.commit()
        for n in ("董辰", "闫泽君", "郑青"):
            await _add(db_session, fund.id, n, OLD)

        res = await fms.compute_manager_changes(db_session, fund.id)
        assert {m["manager_name"] for m in res["current"]} == {"董辰", "闫泽君", "郑青"}
        assert res["history"] == []
        assert res["changed"] is False

    async def test_departed_manager_goes_to_history(self, db_session):
        """离任者 last_seen_at 停在旧批次 → 进前任并报变更"""
        fund = Fund(code="011452", name="测试基金2", fund_type="otc", status="active")
        db_session.add(fund)
        await db_session.commit()
        await _add(db_session, fund.id, "韩浩", OLD)
        await _add(db_session, fund.id, "王森", OLD + timedelta(days=70))

        res = await fms.compute_manager_changes(db_session, fund.id)
        assert [m["manager_name"] for m in res["current"]] == ["王森"]
        assert [m["manager_name"] for m in res["history"]] == ["韩浩"]
        assert res["changed"] is True

    async def test_legacy_rows_fall_back_to_created_at(self, db_session):
        """存量行 last_seen_at 为 NULL（迁移未回填）时按 created_at 分组"""
        fund = Fund(code="017874", name="测试基金3", fund_type="otc", status="active")
        db_session.add(fund)
        await db_session.commit()
        await _add(db_session, fund.id, "马芳", OLD, created_at=OLD)
        await _add(db_session, fund.id, "姚加红", OLD, created_at=OLD)
        for r in (await db_session.execute(select(FundManagerRecord))).scalars():
            r.last_seen_at = None
        await db_session.commit()

        res = await fms.compute_manager_changes(db_session, fund.id)
        assert len(res["current"]) == 2
        assert res["changed"] is False

    async def test_no_records_returns_none(self, db_session):
        assert await fms.compute_manager_changes(db_session, 999) is None


class TestRefreshManagers:
    async def test_refresh_bumps_seen_and_inserts_new(self, db_session, monkeypatch):
        """续见者刷新统计并推进 last_seen；新经理插行；未见者保持旧批次"""
        fund = Fund(code="004011", name="测试基金", fund_type="otc", status="active")
        db_session.add(fund)
        await db_session.commit()

        async def _fake_all():
            return [
                {"姓名": "郑青", "所属公司": "华泰柏瑞基金", "现任基金代码": "004011,110022",
                 "累计从业时间": "5200", "现任基金资产总规模": "1200.5", "现任基金最佳回报": "88.8"},
                {"姓名": "新人甲", "所属公司": "华泰柏瑞基金", "现任基金代码": "004011",
                 "累计从业时间": "300", "现任基金资产总规模": "20", "现任基金最佳回报": "12"},
                {"姓名": "别家经理", "所属公司": "其他基金", "现任基金代码": "999999",
                 "累计从业时间": "1", "现任基金资产总规模": "1", "现任基金最佳回报": "1"},
            ]
        monkeypatch.setattr(fms, "_get_all_managers", _fake_all)

        await fms.refresh_managers(db_session, fund.id, "004011")
        rows = {r.manager_name: r for r in (
            await db_session.execute(
                select(FundManagerRecord).where(FundManagerRecord.fund_id == fund.id)
            )
        ).scalars()}
        assert set(rows) == {"郑青", "新人甲"}
        assert rows["郑青"].tenure_days == 5200 and rows["郑青"].asset_scale == 1200.5
        first_seen = rows["郑青"].last_seen_at

        # 第二轮：郑青续任（统计继续更新），新人甲离任后不再被看到
        async def _fake_all2():
            return [
                {"姓名": "郑青", "所属公司": "华泰柏瑞基金", "现任基金代码": "004011",
                 "累计从业时间": "5300", "现任基金资产总规模": "1300", "现任基金最佳回报": "90"},
            ]
        monkeypatch.setattr(fms, "_get_all_managers", _fake_all2)
        await fms.refresh_managers(db_session, fund.id, "004011")
        rows = {r.manager_name: r for r in (
            await db_session.execute(
                select(FundManagerRecord).where(FundManagerRecord.fund_id == fund.id)
            )
        ).scalars()}
        assert rows["郑青"].tenure_days == 5300
        assert rows["郑青"].last_seen_at >= first_seen
        assert rows["新人甲"].last_seen_at == first_seen  # 未被看到 = 不推进

        # 人为拉开批次（测试内两轮刷新只差毫秒，真实相差以小时/天计）
        rows["新人甲"].last_seen_at = first_seen - timedelta(days=30)
        await db_session.commit()
        res = await fms.compute_manager_changes(db_session, fund.id)
        assert [m["manager_name"] for m in res["current"]] == ["郑青"]
        assert [m["manager_name"] for m in res["history"]] == ["新人甲"]
        assert res["changed"] is True

    async def test_no_manager_source_data_is_noop(self, db_session, monkeypatch):
        """全量经理源失败（返回空）时不清空既有记录"""
        fund = Fund(code="005851", name="测试基金", fund_type="otc", status="active")
        db_session.add(fund)
        await db_session.commit()
        await _add(db_session, fund.id, "沈犁", OLD)

        async def _empty():
            return []
        monkeypatch.setattr(fms, "_get_all_managers", _empty)
        assert await fms.refresh_managers(db_session, fund.id, "005851") == []
        res = await fms.compute_manager_changes(db_session, fund.id)
        assert [m["manager_name"] for m in res["current"]] == ["沈犁"]
