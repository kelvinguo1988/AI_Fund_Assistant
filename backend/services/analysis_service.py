"""分析编排服务 — 数据获取→因子计算→评分→信号→存储→推送"""

import json
import logging
from datetime import date, datetime
from typing import AsyncGenerator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data_sources.data_source_manager import DataSourceManager
from backend.data_sources.base import FundData
from backend.engines.factor_engine import factor_engine, FactorScoreResult
from backend.engines.scoring_engine import scoring_engine, SignalResult
from backend.engines.report_engine import report_engine
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.models.report_config import ReportConfig
from backend.models.system_config import SystemConfig
from backend.schemas.analysis import AnalysisResultOut, FactorScore

logger = logging.getLogger(__name__)

# 流式处理块大小：每批处理 5 只基金后推送一次结果
_STREAM_CHUNK_SIZE = 5


class AnalysisService:
    """分析编排服务"""

    def __init__(self, db: AsyncSession, tushare_token: str = "") -> None:
        self.db = db
        self.data_source = DataSourceManager(tushare_token=tushare_token)

    async def run_analysis(
        self,
        fund_ids: Optional[list[int]] = None,
    ) -> list[AnalysisResultOut]:
        """执行分析流程

        流程：
        1. 获取基金列表 + 活跃因子配置 + 系统配置
        2. 逐只基金获取数据、计算因子原始值
        3. 跨基金截面标准化（如波动率倒数）
        4. 逐只基金计算加权评分、信号、报告
        5. 存储结果并返回
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

        # 3. 获取评分阈值配置
        config_map = await self._get_config_map()
        buy_threshold = float(config_map.get("buy_threshold", "3.5"))
        sell_threshold = float(config_map.get("sell_threshold", "2.0"))
        thresholds_json = config_map.get("scoring_thresholds", "")

        # 4. 获取报告配置
        report_result = await self.db.execute(
            select(ReportConfig).where(ReportConfig.enabled == True).order_by(ReportConfig.sort_order)
        )
        enabled_report_items = [r.item_key for r in report_result.scalars().all()]

        # 5. 逐只基金获取数据 + 计算因子（第一遍）
        fund_data_map: dict[str, FundData] = {}
        all_factor_results: dict[str, list[FactorScoreResult]] = {}

        for fund in funds:
            try:
                fund_data = await self.data_source.get_fund_data(fund.code, fund_type=getattr(fund, "fund_type", None))
                fund_data_map[fund.code] = fund_data

                factor_scores = factor_engine.calculate_all(fund_data, active_factors)
                all_factor_results[fund.code] = factor_scores

                logger.info(f"因子计算完成: {fund.code} ({fund.name}), {len(factor_scores)} 个因子")
            except Exception as e:
                logger.error(f"获取/计算基金 {fund.code} 失败: {e}")
                continue

        # 6. 跨基金截面标准化
        all_factor_results = factor_engine.normalize_cross_sectional(all_factor_results, active_factors)

        # 7. 逐只基金评分 + 信号 + 存储
        results: list[AnalysisResultOut] = []
        for fund in funds:
            if fund.code not in all_factor_results:
                continue

            factor_scores = all_factor_results[fund.code]

            # 加权评分 + 信号
            weights = [f.get("weight", 1.0) for f in active_factors]
            signal = scoring_engine.compute(
                factor_scores=factor_scores,
                factor_weights=weights,
                buy_threshold=buy_threshold,
                sell_threshold=sell_threshold,
                thresholds_json=thresholds_json,
            )

            # 生成报告
            fund_data = fund_data_map.get(fund.code)
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
            result_out = await self._save_result(fund, signal, factor_scores)
            if result_out:
                results.append(result_out)

        return results

    async def run_analysis_streaming(
        self,
        fund_ids: Optional[list[int]] = None,
    ) -> AsyncGenerator[str, None]:
        """流式分析 — 分块处理基金，通过 SSE 逐批推送结果

        Yield 格式 (SSE)：
        - {"type":"progress","current":5,"total":50,"fund_code":"007491"}
        - {"type":"chunk","results":[...],"progress":"10/50"}
        - {"type":"complete","total":50,"succeeded":48}
        """
        # 1. 获取基金池
        stmt = select(Fund).where(Fund.status == "active")
        if fund_ids:
            stmt = stmt.where(Fund.id.in_(fund_ids))
        result = await self.db.execute(stmt)
        funds = result.scalars().all()

        if not funds:
            yield "data: " + json.dumps({"type": "complete", "total": 0, "succeeded": 0}) + "\n\n"
            return

        total = len(funds)

        # 2. 获取活跃因子配置
        from backend.services.factor_service import FactorService
        factor_svc = FactorService(self.db)
        active_factors = await factor_svc.get_active_factors_as_dicts()

        if not active_factors:
            yield "data: " + json.dumps({"type": "complete", "total": total, "succeeded": 0, "error": "没有启用的因子"}) + "\n\n"
            return

        # 3. 获取评分阈值
        config_map = await self._get_config_map()
        buy_threshold = float(config_map.get("buy_threshold", "3.5"))
        sell_threshold = float(config_map.get("sell_threshold", "2.0"))
        thresholds_json = config_map.get("scoring_thresholds", "")

        # 4. 获取报告配置
        report_result = await self.db.execute(
            select(ReportConfig).where(ReportConfig.enabled == True).order_by(ReportConfig.sort_order)
        )
        enabled_report_items = [r.item_key for r in report_result.scalars().all()]

        # ── Phase 1: 逐只获取数据 + 计算因子（仅推进度，不推结果） ──
        fund_data_map: dict[str, FundData] = {}
        all_factor_results: dict[str, list[FactorScoreResult]] = {}
        failed_codes: list[str] = []

        for i, fund in enumerate(funds):
            try:
                fund_data = await self.data_source.get_fund_data(fund.code, fund_type=getattr(fund, "fund_type", None))
                fund_data_map[fund.code] = fund_data

                factor_scores = factor_engine.calculate_all(fund_data, active_factors)
                all_factor_results[fund.code] = factor_scores
            except Exception as e:
                logger.error(f"获取/计算基金 {fund.code} 失败: {e}")
                failed_codes.append(fund.code)
            # 每只基金都推送进度
            progress_data = {"type": "progress", "current": i + 1, "total": total, "fund_code": fund.code}
            yield "data: " + json.dumps(progress_data) + "\n\n"

        # 5. 跨基金截面标准化
        all_factor_results = factor_engine.normalize_cross_sectional(all_factor_results, active_factors)

        # ── Phase 2: 分块评分 + 存储 + 推送结果 ──
        results: list[AnalysisResultOut] = []
        weights = [f.get("weight", 1.0) for f in active_factors]

        for chunk_start in range(0, len(funds), _STREAM_CHUNK_SIZE):
            chunk = funds[chunk_start:chunk_start + _STREAM_CHUNK_SIZE]
            chunk_results: list[AnalysisResultOut] = []

            for fund in chunk:
                if fund.code not in all_factor_results:
                    continue

                factor_scores = all_factor_results[fund.code]
                signal = scoring_engine.compute(
                    factor_scores=factor_scores,
                    factor_weights=weights,
                    buy_threshold=buy_threshold,
                    sell_threshold=sell_threshold,
                    thresholds_json=thresholds_json,
                )

                report_md = report_engine.generate_markdown(
                    fund_code=fund.code,
                    fund_name=fund.name,
                    analysis_date=date.today().isoformat(),
                    signal=signal,
                    factor_scores=factor_scores,
                    enabled_items=enabled_report_items,
                )

                result_out = await self._save_result(fund, signal, factor_scores)
                if result_out:
                    chunk_results.append(result_out)
                    results.append(result_out)

            if chunk_results:
                chunk_data = {
                    "type": "chunk",
                    "results": [r.model_dump(mode="json") for r in chunk_results],
                    "progress": f"{len(results)}/{total}",
                }
                yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"

        # 完成事件
        complete_data = {
            "type": "complete",
            "total": total,
            "succeeded": len(results),
            "failed": failed_codes,
        }
        yield f"data: {json.dumps(complete_data, ensure_ascii=False)}\n\n"

    async def _save_result(
        self,
        fund: Fund,
        signal: SignalResult,
        factor_scores: list[FactorScoreResult],
    ) -> Optional[AnalysisResultOut]:
        """存储分析结果到数据库"""
        factor_scores_json = json.dumps({
            fs.factor_code: {
                "name": fs.factor_name,
                "raw_value": fs.raw_value,
                "score": fs.score,
                "direction": fs.direction,
            }
            for fs in factor_scores
        }, ensure_ascii=False)

        existing_result = await self.db.execute(
            select(AnalysisResult).where(
                AnalysisResult.fund_id == fund.id,
                AnalysisResult.analysis_date == date.today(),
            )
        )
        existing = existing_result.scalars().first()

        if existing:
            existing.weighted_score = signal.weighted_score
            existing.signal_direction = signal.signal_direction
            existing.signal_strength = signal.signal_strength
            existing.operation_advice = signal.operation_advice
            existing.equity_ratio = signal.equity_ratio
            existing.factor_scores = factor_scores_json
            analysis_id = existing.id
        else:
            new_result = AnalysisResult(
                fund_id=fund.id,
                analysis_date=date.today(),
                weighted_score=signal.weighted_score,
                signal_direction=signal.signal_direction,
                signal_strength=signal.signal_strength,
                operation_advice=signal.operation_advice,
                equity_ratio=signal.equity_ratio,
                factor_scores=factor_scores_json,
            )
            self.db.add(new_result)
            await self.db.flush()
            analysis_id = new_result.id

        await self.db.commit()

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
            equity_ratio=signal.equity_ratio,
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
