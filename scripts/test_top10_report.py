#!/usr/bin/env python3
"""实测报告 top10_change 项：中欧数字经济混合发起C(018994) + 广发远见智选混合C(016874)

流程：建库→种子(含新报告项)→建基金→refresh_holdings 拉季报持仓→
FundRealtimeService.get_top10_changes→report_engine.generate_markdown
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.patch.eastmoney_patch import apply_patch
apply_patch()

import requests as _requests
_orig_merge = _requests.Session.merge_environment_settings
def _no_proxy_merge(self, url, proxies, stream, verify, cert):
    s = _orig_merge(self, url, proxies, stream, verify, cert)
    s["proxies"] = {}
    return s
_requests.Session.merge_environment_settings = _no_proxy_merge

from sqlalchemy import select
from backend.database import init_db, async_session_factory
from backend.models.fund import Fund
from backend.services.fund_holding_service import refresh_holdings
from backend.services.fund_realtime_service import FundRealtimeService
from backend.engines.report_engine import ReportEngine
from backend.schemas.fund import FundCreate
from backend.services.fund_service import FundService

TEST_FUNDS = [("018994", "中欧数字经济混合发起C"), ("016874", "广发远见智选混合C")]


async def main():
    await init_db()
    async with async_session_factory() as db:
        for code, name in TEST_FUNDS:
            exist = (await db.execute(select(Fund).where(Fund.code == code))).scalars().first()
            if exist is None:
                await FundService(db).create_fund(FundCreate(code=code, name=name))
                print(f"已创建基金 {code} {name}")
        await db.commit()

        for code, name in TEST_FUNDS:
            fund = (await db.execute(select(Fund).where(Fund.code == code))).scalars().first()
            print(f"\n=== {name}({code}) 拉取季报持仓 ===")
            try:
                holdings = await refresh_holdings(db, fund.id, code)
                await db.commit()
                print(f"持仓 {len(holdings)} 条已入库")
            except Exception as e:
                print(f"持仓拉取失败: {e}")
                continue

            rt = FundRealtimeService(db)
            changes = await rt.get_top10_changes(fund.id)
            print("前十大持仓涨跌:")
            for h in changes:
                pct = f"{h['pct']:+.2f}%" if h["pct"] is not None else "—"
                print(f"  {h['stock_name']}({h['stock_code']}) 占比{h['ratio']}% → {pct}")

            # 模拟报告渲染（signal 构造最小占位）
            from backend.engines.scoring_engine import SignalResult
            signal = SignalResult(
                weighted_score=3.2, raw_score=3.0,
                signal_direction="buy", signal_strength="moderate_buy",
                operation_advice="测试建议", equity_ratio=0.7,
            )
            md = ReportEngine().generate_markdown(
                fund_code=code, fund_name=name, analysis_date="2026-08-29",
                signal=signal, factor_scores=[], enabled_items=["top10_change"],
                top10_changes=changes,
            )
            print("\n--- 报告片段 ---")
            print(md)


if __name__ == "__main__":
    asyncio.run(main())
