from __future__ import annotations
"""推送编排服务 — 遍历渠道→格式化→发送"""

import json
import logging
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.push_channel import PushChannel
from backend.models.report_config import ReportConfig
from backend.push.feishu import FeishuPush
from backend.schemas.analysis import AnalysisResultOut

logger = logging.getLogger(__name__)

# 市场概况相关项（非基金维度）
MARKET_ITEMS = {
    "signal_summary", "top_buy_sell", "adv_decline", "turnover",
    "market_flow", "hsgt_flow",
    "sector_flow_day", "sector_flow_week", "sector_flow_month",
    # fund_realtime_top10 2026-08-31 加入：全景报告尾部基金实时涨跌 TOP10
    "fund_realtime_top10",
}
# 基金维度项（top10_change 2026-08-29 / fund_daily_change 2026-08-31 加入：
# 推送报告含前十大持仓当日涨跌 + 基金本身当日实时涨跌）
FUND_ITEMS = {
    "factor_detail", "weighted_score", "operation_advice",
    "signal_strength", "risk_warning", "top10_change", "fund_daily_change",
}

# 推送取数优先复用实时估值缓存（调度已预热/分析轮已取过快照），
# 仅当缓存覆盖率低于该比例时才真正强刷一次行情源。
# 背景：一次调度内"分析轮 + 预热轮 + 推送轮"会连打三轮行情源，东财反爬
# 触发 600s 熔断后，最后一轮（推送）拿不到数据 → 实时估值整块消失。
# 缓存优先可以让推送对熔断免疫（吃前两轮留下的快照）。
REALTIME_CACHE_MIN_COVERAGE = 0.5


class PushService:
    """推送编排服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _fetch_realtime_map(self) -> dict[str, dict]:
        """取全池基金当日实时涨跌 —— 缓存优先，覆盖率不足才强刷

        一次调度会依次经过「分析轮 → 预热轮 → 推送轮」，若每轮都 force=True
        强刷，东财反爬触发 600s 熔断后，轮到推送时已无源可用 → 实时估值整块
        消失且无任何报错。故此处先读缓存（吃预热/分析轮写入的 _estimate_cache），
        仅在覆盖率低于 REALTIME_CACHE_MIN_COVERAGE 时才真正强刷一次，
        让推送对熔断免疫。
        """
        from backend.models.fund import Fund as _Fund
        from backend.services.fund_realtime_service import FundRealtimeService

        fund_rows = list(
            (await self.db.execute(
                select(_Fund).where(_Fund.status == "active")
            )).scalars().all()
        )
        if not fund_rows:
            return {}

        rt_svc = FundRealtimeService(self.db)
        realtime_map = await rt_svc.get_realtime(fund_rows, force=False)
        # 覆盖率过低 → 缓存已过期或上一轮全败，才真正强刷一次
        if len(realtime_map) < len(fund_rows) * REALTIME_CACHE_MIN_COVERAGE:
            realtime_map = await rt_svc.get_realtime(fund_rows, force=True)

        logger.info(f"推送取基金当日涨跌: {len(realtime_map)}/{len(fund_rows)} 只")
        if not realtime_map:
            # 静默失败会让推送"成功但实时估值凭空消失"，必须留痕
            logger.warning(
                f"推送取基金当日涨跌为空（{len(fund_rows)} 只全部无数据），"
                f"报告中的实时涨跌项将整块缺失。"
                f"常见原因：行情源熔断/反爬、持仓数据缺失、"
                f"或 ETF 行情与持仓自算双双取不到"
            )
        return realtime_map

    async def push_analysis_results(
        self,
        results: list[AnalysisResultOut],
        channel_id: int | None = None,
    ) -> dict[str, bool]:
        """推送分析结果

        Args:
            results: 分析结果列表
            channel_id: 指定推送渠道 ID，None 表示推送到所有启用渠道

        Returns:
            渠道名→是否成功 的映射
        """
        if not results:
            logger.info("无分析结果，跳过推送")
            return {}

        # 获取推送渠道
        stmt = select(PushChannel).where(PushChannel.enabled == True)
        if channel_id:
            stmt = stmt.where(PushChannel.id == channel_id)
        result = await self.db.execute(stmt)
        channels = result.scalars().all()

        if not channels:
            logger.warning("无启用的推送渠道")
            return {}

        # 获取启用的报告配置项
        report_result = await self.db.execute(
            select(ReportConfig).order_by(ReportConfig.sort_order)
        )
        all_configs = report_result.scalars().all()
        enabled_items = [c.item_key for c in all_configs if c.enabled]

        enabled_market = [i for i in enabled_items if i in MARKET_ITEMS]
        enabled_fund = [i for i in enabled_items if i in FUND_ITEMS]

        # 生成报告文本
        from backend.engines.report_engine import report_engine
        from backend.engines.scoring_engine import SignalResult

        # 获取市场概况数据（先清缓存，确保推送使用最新行情）
        market_summary_md = None
        if enabled_market:
            try:
                from backend.services.market_service import MarketService
                MarketService.clear_cache()

                # 市场环境摘要（估值分位/情绪/资金面）——置于全景报告头部
                market_regime_md = None
                try:
                    from backend.services.market_regime_service import MarketRegimeService
                    snap = await MarketRegimeService().get_snapshot()
                    regime_lines = []
                    if snap.valuation_percentile is not None:
                        regime_lines.append(
                            f"- 大盘估值：沪深300 PE 分位 **{snap.valuation_percentile:.0%}**"
                            f"（当前 PE {snap.valuation_current_pe}，{snap.valuation_date}）"
                        )
                    if snap.adv_decline_ratio is not None:
                        regime_lines.append(
                            f"- 市场情绪：涨跌家数比 {snap.adv_decline_ratio:+.2f}"
                            f"（涨 {snap.up_count} / 跌 {snap.down_count}）"
                        )
                    if snap.margin_change_pct_7d is not None:
                        regime_lines.append(
                            f"- 资金面：两融余额 7 日变化 **{snap.margin_change_pct_7d:+.2%}**"
                            f"（{snap.margin_date}）"
                        )
                    if regime_lines:
                        market_regime_md = "**市场环境**\n" + "\n".join(regime_lines)
                except Exception as re_:
                    logger.warning(f"市场环境摘要生成失败: {re_}")
                    market_regime_md = None
                svc = MarketService()
                from backend.schemas.market import MarketSummaryOut, SignalSummary

                today_str = date.today().isoformat()
                market_flow = await svc.get_market_capital_flow()
                sector_flow_raw = await svc.get_sector_flow_rankings()
                hsgt_flow = await svc.get_hsgt_flow()
                adv_decline = await svc.get_market_adv_decline()
                turnover = await svc.get_market_turnover()

                # 数据新鲜度校验日志
                stale_fields = []
                if market_flow is None:
                    stale_fields.append("market_capital_flow")
                if hsgt_flow is None:
                    stale_fields.append("hsgt_flow")
                if adv_decline is None:
                    stale_fields.append("adv_decline")
                if turnover is None:
                    stale_fields.append("turnover")
                if stale_fields:
                    logger.warning(f"推送前行情数据缺失: {', '.join(stale_fields)}，推送内容可能不完整")
                else:
                    logger.info("推送前行情数据校验通过（5 项均正常获取）")

                # 统计信号方向计数 & 构建 TOP10 买卖信号
                buy_results = [r for r in results if r.signal_direction == "buy"]
                sell_results = [r for r in results if r.signal_direction == "sell"]
                hold_results = [r for r in results if r.signal_direction == "hold"]
                top_buy = sorted(buy_results, key=lambda r: r.weighted_score, reverse=True)[:10]
                top_sell = sorted(sell_results, key=lambda r: r.weighted_score)[:10]
                logger.info(
                    f"推送 TOP10 构建: buy={len(buy_results)}只→取{len(top_buy)}, "
                    f"sell={len(sell_results)}只→取{len(top_sell)}"
                )

                market_summary = MarketSummaryOut(
                    date=today_str,
                    signals=SignalSummary(
                        total=len(results),
                        buy_count=len(buy_results),
                        sell_count=len(sell_results),
                        hold_count=len(hold_results),
                        top_buy=top_buy,
                        top_sell=top_sell,
                    ),
                    market_flow=market_flow,
                    sector_flow=list(sector_flow_raw.values()),
                    hsgt_flow=hsgt_flow,
                    adv_decline=adv_decline,
                    turnover=turnover,
                )
                market_summary_md = report_engine.generate_market_summary_markdown(
                    market_summary, enabled_items=enabled_market
                )
                # 市场环境摘要拼接在全景报告头部
                if market_regime_md:
                    market_summary_md = f"{market_regime_md}\n\n{market_summary_md}"

                # 同步更新仪表盘行情缓存，确保推送后仪表盘看到的是最新数据
                try:
                    from backend.services.fund_cache_service import set_cached_json
                    cache_data = {
                        "market_flow": market_flow.model_dump() if market_flow else None,
                        "sector_flow": [s.model_dump() for s in sector_flow_raw.values()],
                        "hsgt_flow": hsgt_flow.model_dump() if hsgt_flow else None,
                        "adv_decline": adv_decline.model_dump() if adv_decline else None,
                        "turnover": turnover.model_dump() if turnover else None,
                    }
                    await set_cached_json(self.db, "market_summary", cache_data)
                    logger.info("推送同时已更新仪表盘行情缓存")
                except Exception as ce:
                    logger.warning(f"仪表盘行情缓存更新失败: {ce}")
            except Exception as e:
                logger.warning(f"市场概况生成失败: {e}")

        push_results: dict[str, bool] = {}

        # 基金当日实时涨跌：一次批量预取全部基金（推送任何报告文本前完成），
        # 逐只报告复用（共享快照缓存，不逐只重复请求）。
        # 触发条件：单基金报告需要（fund_daily_change）或全景报告需要 TOP10
        realtime_map: dict[str, dict] = {}
        if "fund_daily_change" in enabled_fund or "fund_realtime_top10" in enabled_market:
            try:
                realtime_map = await self._fetch_realtime_map()
            except Exception as rt_err:
                logger.warning(f"推送预取基金当日涨跌失败（报告该项置空）: {rt_err}")

        # 基金实时涨跌 TOP10（涨/跌各最多 10 只）——追加在市场全景报告尾部
        if "fund_realtime_top10" in enabled_market and realtime_map:
            try:
                ranked = sorted(
                    (r for r in realtime_map.values() if r.get("growth_pct") is not None),
                    key=lambda r: r["growth_pct"],
                    reverse=True,
                )
                if ranked:
                    def _line(r: dict) -> str:
                        return (
                            f"- {r.get('name') or r.get('code')}({r.get('code')}) "
                            f"{r['growth_pct']:+.2f}%"
                        )

                    lines = ["**基金实时涨跌 TOP10**", "", "涨幅居前："]
                    lines += [_line(r) for r in ranked[:10]]
                    if len(ranked) > 10:
                        lines += ["", "跌幅居前："]
                        lines += [_line(r) for r in ranked[-10:][::-1]]
                    top10_md = "\n".join(lines)
                    if market_summary_md:
                        market_summary_md = f"{market_summary_md}\n\n{top10_md}"
                    else:
                        market_summary_md = top10_md
                    logger.info(f"基金实时涨跌 TOP10 已加入全景报告（{len(ranked)} 只有数据）")
            except Exception as top_err:
                logger.warning(f"基金实时涨跌 TOP10 生成失败: {top_err}")

        for channel in channels:
            try:
                if channel.channel_type == "feishu":
                    pusher = FeishuPush(
                        webhook_url=channel.webhook_url or "",
                        secret=channel.token,
                    )

                    # 先推送市场全景概览
                    if market_summary_md:
                        await pusher.send_market_overview(market_summary_md)

                    # 逐只基金推送（仅当有启用的基金维度报告项时才发送）
                    if enabled_fund:
                        for r in results:
                            # 重建 FactorScoreResult 用于报告生成
                            from backend.engines.factor_engine import FactorScoreResult as FSR
                            factor_scores = [
                                FSR(
                                    factor_code=fs.factor_code,
                                    factor_name=fs.factor_name,
                                    raw_value=fs.raw_value,
                                    score=fs.score,
                                    direction=fs.direction,
                                )
                                for fs in r.factor_scores
                            ]
                            signal = SignalResult(
                                weighted_score=r.weighted_score,
                                raw_score=0.0,
                                signal_direction=r.signal_direction,
                                signal_strength=r.signal_strength,
                                operation_advice=r.operation_advice,
                                equity_ratio=getattr(r, "equity_ratio", 0.5),
                                quality_warnings=getattr(r, "quality_warnings", None) or [],
                            )

                            # 前十大持仓当日涨跌（top10_change 启用时）
                            top10_changes = None
                            top10_quote_time = ""
                            if "top10_change" in enabled_fund:
                                try:
                                    from backend.services.fund_realtime_service import (
                                        FundRealtimeService,
                                    )
                                    rt = FundRealtimeService(self.db)
                                    top10_changes = (
                                        await rt.get_top10_changes(r.fund_id) or None
                                    )
                                    top10_quote_time = (
                                        FundRealtimeService.get_spot_quote_time()
                                    )
                                except Exception as rt_err:
                                    logger.warning(
                                        f"推送拉取 top10 涨跌失败 {r.fund_code}: {rt_err}"
                                    )

                            report_md = report_engine.generate_markdown(
                                fund_code=r.fund_code,
                                fund_name=r.fund_name,
                                analysis_date=str(r.analysis_date),
                                signal=signal,
                                factor_scores=factor_scores,
                                enabled_items=enabled_fund,
                                top10_changes=top10_changes,
                                top10_quote_time=top10_quote_time,
                                fund_daily_change=realtime_map.get(r.fund_code),
                            )

                            success = await pusher.send_analysis_report(
                                fund_name=r.fund_name,
                                fund_code=r.fund_code,
                                signal_direction=r.signal_direction,
                                weighted_score=r.weighted_score,
                                report_markdown=report_md,
                            )
                            push_results[f"{channel.name}:{r.fund_code}"] = success

                else:
                    logger.warning(f"不支持的渠道类型: {channel.channel_type}")
                    push_results[channel.name] = False

            except Exception as e:
                logger.error(
                    f"推送渠道 {channel.name}({channel.channel_type}) 失败: "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )
                push_results[channel.name] = False

        return push_results
