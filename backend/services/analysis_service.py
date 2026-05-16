"""分析编排服务 — 数据获取→因子计算→评分→信号→存储→推送"""

import json
import logging
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data_sources.akshare_adapter import AKShareAdapter
from backend.engines.factor_engine import factor_engine
from backend.engines.scoring_engine import scoring_engine, SignalResult
from backend.engines.report_engine import report_engine
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.models.report_config import ReportConfig
from backend.models.system_config import SystemConfig
from backend.schemas.analysis import AnalysisResultOut, FactorScore

logger = logging.getLogger(__name__)


class AnalysisService:
    """分析编排服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.data_source = AKShareAdapter()

    async def run_analysis(
        self,
        fund_ids: Optional[list[int]] = None,
    ) -> list[AnalysisResultOut]:
        """执行分析流程

        Args:
            fund_ids: 指定基金 ID 列表，None 表示分析全部启用基金

        Returns:
            分析结果列表
        """
        # 1. 获取基金池
        stmt = select(Fund).where(Fund.status == "active")
        if fund_ids:
            stmt = stmt.where(Fund.id.in_(fund_ids))
        result = await self.db.execute(stmt)
        funds = result.scalars().all()

        if not funds:
            logger.warning("没有启用的基金，跳过分析")
            return []

        # 2. 获取活跃因子配置
        from backend.services.factor_service import FactorService
        factor_svc = FactorService(self.db)
        active_factors = await factor_svc.get_active_factors_as_dicts()

        if not active_factors:
            logger.warning("没有启用的因子，跳过分析")
            return []

        # 3. 获取评分阈值
        config_map = await self._get_config_map()
        buy_threshold = float(config_map.get("buy_threshold", "3.5"))
        sell_threshold = float(config_map.get("sell_threshold", "2.0"))

        # 4. 获取报告配置
        report_result = await self.db.execute(
            select(ReportConfig).where(ReportConfig.enabled == True).order_by(ReportConfig.sort_order)
        )
        enabled_report_items = [r.item_key for r in report_result.scalars().all()]

        # 5. 逐只基金分析
        results: list[AnalysisResultOut] = []

        for fund in funds:
            try:
                result_out = await self._analyze_fund(
                    fund=fund,
                    active_factors=active_factors,
                    buy_threshold=buy_threshold,
                    sell_threshold=sell_threshold,
                    enabled_report_items=enabled_report_items,
                )
                if result_out:
                    results.append(result_out)
            except Exception as e:
                logger.error(f"分析基金 {fund.code} 失败: {e}")
                # 单只基金分析失败不中断整体流程
                continue

        return results

    async def _analyze_fund(
        self,
        fund: Fund,
        active_factors: list[dict],
        buy_threshold: float,
        sell_threshold: float,
        enabled_report_items: list[str],
    ) -> Optional[AnalysisResultOut]:
        """分析单只基金"""
        # 获取基金数据
        fund_data = await self.data_source.get_fund_data(fund.code)

        # 计算因子评分
        factor_scores = factor_engine.calculate_all(fund_data, active_factors)

        # 计算加权评分 + 信号
        weights = [f.get("weight", 1.0) for f in active_factors]
        signal = scoring_engine.compute(
            factor_scores=factor_scores,
            factor_weights=weights,
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
        )

        # 生成报告
        analysis_date = date.today().isoformat()
        report_md = report_engine.generate_markdown(
            fund_code=fund.code,
            fund_name=fund.name,
            analysis_date=analysis_date,
            signal=signal,
            factor_scores=factor_scores,
            enabled_items=enabled_report_items,
        )

        # 存储结果
        factor_scores_json = json.dumps({
            fs.factor_code: {
                "name": fs.factor_name,
                "raw_value": fs.raw_value,
                "score": fs.score,
                "direction": fs.direction,
            }
            for fs in factor_scores
        }, ensure_ascii=False)

        # 检查是否已有当日结果
        existing_result = await self.db.execute(
            select(AnalysisResult).where(
                AnalysisResult.fund_id == fund.id,
                AnalysisResult.analysis_date == date.today(),
            )
        )
        existing = existing_result.scalars().first()

        if existing:
            # 更新
            existing.weighted_score = signal.weighted_score
            existing.signal_direction = signal.signal_direction
            existing.signal_strength = signal.signal_strength
            existing.operation_advice = signal.operation_advice
            existing.factor_scores = factor_scores_json
            analysis_id = existing.id
        else:
            # 新增
            new_result = AnalysisResult(
                fund_id=fund.id,
                analysis_date=date.today(),
                weighted_score=signal.weighted_score,
                signal_direction=signal.signal_direction,
                signal_strength=signal.signal_strength,
                operation_advice=signal.operation_advice,
                factor_scores=factor_scores_json,
            )
            self.db.add(new_result)
            await self.db.flush()
            analysis_id = new_result.id

        await self.db.commit()

        # 构建输出
        return AnalysisResultOut(
            id=analysis_id,
            fund_id=fund.id,
            fund_code=fund.code,
            fund_name=fund.name,
            analysis_date=date.today(),
            weighted_score=signal.weighted_score,
            signal_direction=signal.signal_direction,
            signal_strength=signal.signal_strength,
            operation_advice=signal.operation_advice,
            factor_scores=[
                FactorScore(
                    factor_code=fs.factor_code,
                    factor_name=fs.factor_name,
                    raw_value=fs.raw_value,
                    score=fs.score,
                    direction=fs.direction,
                )
                for fs in factor_scores
            ],
            created_at=datetime.now(),
        )

    async def _get_config_map(self) -> dict[str, str]:
        """获取系统配置 KV 映射"""
        result = await self.db.execute(select(SystemConfig))
        configs = result.scalars().all()
        return {c.config_key: c.config_value for c in configs}
