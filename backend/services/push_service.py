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


class PushService:
    """推送编排服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

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

        # 市场概况相关项（非基金维度）
        market_items = {
            "signal_summary", "top_buy_sell", "adv_decline", "turnover",
            "market_flow", "hsgt_flow",
            "sector_flow_day", "sector_flow_week", "sector_flow_month",
        }
        # 基金维度项（原 5 项）
        fund_items = {
            "factor_detail", "weighted_score", "operation_advice",
            "signal_strength", "risk_warning",
        }

        enabled_market = [i for i in enabled_items if i in market_items]
        enabled_fund = [i for i in enabled_items if i in fund_items]

        # 生成报告文本
        from backend.engines.report_engine import report_engine
        from backend.engines.scoring_engine import SignalResult

        # 获取市场概况数据（先清缓存，确保推送使用最新行情）
        market_summary_md = None
        if enabled_market:
            try:
                from backend.services.market_service import MarketService
                MarketService.clear_cache()
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

                market_summary = MarketSummaryOut(
                    date=today_str,
                    signals=SignalSummary(total=len(results)),
                    market_flow=market_flow,
                    sector_flow=list(sector_flow_raw.values()),
                    hsgt_flow=hsgt_flow,
                    adv_decline=adv_decline,
                    turnover=turnover,
                )
                market_summary_md = report_engine.generate_market_summary_markdown(
                    market_summary, enabled_items=enabled_market
                )

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

                    # 逐只基金推送
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
                        )

                        report_md = report_engine.generate_markdown(
                            fund_code=r.fund_code,
                            fund_name=r.fund_name,
                            analysis_date=str(r.analysis_date),
                            signal=signal,
                            factor_scores=factor_scores,
                            enabled_items=enabled_fund if enabled_fund else None,
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
                logger.error(f"推送渠道 {channel.name} 失败: {e}")
                push_results[channel.name] = False

        return push_results
