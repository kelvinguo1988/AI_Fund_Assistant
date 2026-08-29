"""分析编排服务 — 数据获取→因子计算→评分→信号→存储→推送"""

import asyncio
import json
import logging
from datetime import date, datetime
from typing import AsyncGenerator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data_sources.data_source_manager import DataSourceManager
from backend.data_sources.base import FundData
from backend.engines.factor_engine import factor_engine, FactorScoreResult
from backend.engines.scoring_engine import scoring_engine, SignalResult, compute_with_quality_filter
from backend.engines.report_engine import report_engine
from backend.engines.quality_filter import (
    quality_filter as _default_quality_filter,
    QualityFilterResult,
    merge_quality_config,
    build_quality_filter,
)
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.models.fund_quarterly import FundQuarterly
from backend.models.report_config import ReportConfig
from backend.models.system_config import SystemConfig
from backend.schemas.analysis import (
    AnalysisResultOut, FactorScore,
    AnalysisExportItem, AnalysisExportPayload, AnalysisImportResult,
)

logger = logging.getLogger(__name__)

# 流式处理块大小：每批处理 5 只基金后推送一次结果
_STREAM_CHUNK_SIZE = 5


def _inject_regime_params(params_json, snapshot) -> dict:
    """把市场环境快照注入因子 params（_ 前缀 = 引擎内部字段，不落库）

    calculate_all 接受 dict 或 JSON 字符串形式的 params；这里返回 dict，
    快照对象以 "_regime_snapshot" 键原样传递给市场环境因子。
    """
    import json as _json
    try:
        params = _json.loads(params_json) if params_json else {}
    except (TypeError, ValueError):
        params = {}
    if not isinstance(params, dict):
        params = {}
    params["_regime_snapshot"] = snapshot
    return params


class AnalysisService:
    """分析编排服务"""

    def __init__(self, db: AsyncSession,
                 joinquant_user: str = "", joinquant_password: str = "") -> None:
        self.db = db
        self.data_source = DataSourceManager(
            joinquant_user=joinquant_user,
            joinquant_password=joinquant_password,
        )

    async def _load_quarterly_data(self, fund_id: int) -> list[dict]:
        """从数据库加载基金的季度扩展数据"""
        result = await self.db.execute(
            select(FundQuarterly)
            .where(FundQuarterly.fund_id == fund_id)
            .order_by(FundQuarterly.report_date)
        )
        records = result.scalars().all()
        return [
            {
                "report_date": r.report_date,
                "effective_date": r.effective_date,
                "fund_size": r.fund_size,
                "stock_position_ratio": r.stock_position_ratio,
                "institution_holding_ratio": r.institution_holding_ratio,
                "insider_holding_shares": r.insider_holding_shares,
            }
            for r in records
        ]

    async def _batch_load_quarterly_data(self, fund_ids: list[int]) -> dict[int, list[dict]]:
        """批量加载多只基金的季度扩展数据，避免 N+1 查询。

        一次 SELECT ... WHERE fund_id IN (...) 取回全部季度数据，
        返回 {fund_id: [季度数据dict, ...]} 映射。
        """
        if not fund_ids:
            return {}
        result = await self.db.execute(
            select(FundQuarterly)
            .where(FundQuarterly.fund_id.in_(fund_ids))
            .order_by(FundQuarterly.fund_id, FundQuarterly.report_date)
        )
        records = result.scalars().all()
        by_fund: dict[int, list[dict]] = {}
        for r in records:
            by_fund.setdefault(r.fund_id, []).append({
                "report_date": r.report_date,
                "effective_date": r.effective_date,
                "fund_size": r.fund_size,
                "stock_position_ratio": r.stock_position_ratio,
                "institution_holding_ratio": r.institution_holding_ratio,
                "insider_holding_shares": r.insider_holding_shares,
            })
        return by_fund

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

        # 5. 加载质量过滤配置（DB 覆盖默认值）
        merged_qf_config = await merge_quality_config(self.db)
        qf = build_quality_filter(merged_qf_config)

        # 5.5 获取市场环境快照（市场环境因子 + 极端估值阈值调节）
        # 快照随参数传递（任务间隔离，无模块级全局竞态）；
        # 失败不阻塞主流程：快照缺失时市场因子返回中性 0 分，阈值不调节
        try:
            from backend.services.market_regime_service import MarketRegimeService
            regime_snapshot = await MarketRegimeService().get_snapshot()
        except Exception as e:
            logger.warning(f"市场环境快照获取失败，市场因子将使用中性分: {e}")
            regime_snapshot = None
        # 市场环境因子不依赖 fund_data，通过 params 注入快照（_ 前缀 = 引擎内部字段）
        regime_factors = [
            {**f, "params": _inject_regime_params(f.get("params"), regime_snapshot)}
            if f.get("code", "").startswith("market_") else f
            for f in active_factors
        ]

        # 6. 逐只基金获取数据 + 计算因子（第一遍）
        # 修复：原实现串行 for 循环，40-60 只基金 × (3s 限流 + 2-5s jitter + 请求耗时)
        # = 10-40 分钟。改为并发获取基金数据（网络密集型，最慢），
        # 串行做 DB 查询 + 因子计算（AsyncSession 非并发安全 + CPU 快）。
        # get_fund_data 内部已有全局信号量（并发 5）控制实际网络并发数。
        fund_data_map: dict[str, FundData] = {}
        all_factor_results: dict[str, list[FactorScoreResult]] = {}
        quarterly_data_map: dict[str, list[dict]] = {}

        async def _fetch_one(fund: Fund) -> tuple[Fund, Optional[FundData]]:
            """并发获取单只基金数据（网络密集型）"""
            try:
                fd = await self.data_source.get_fund_data(
                    fund.code, fund_type=getattr(fund, "fund_type", None)
                )
                return fund, fd
            except Exception as e:
                logger.error(f"获取基金 {fund.code} 数据失败: {e}")
                return fund, None

        # 并发获取所有基金数据（信号量在底层 _call 中控制并发为 5）
        fetch_results = await asyncio.gather(
            *[_fetch_one(f) for f in funds], return_exceptions=False
        )

        # 串行加载季度数据 + 计算因子（AsyncSession 非并发安全）
        # 批量加载季度数据，避免 N+1 查询
        valid_fund_ids = [f.id for f, fd in fetch_results if fd is not None]
        quarterly_batch = await self._batch_load_quarterly_data(valid_fund_ids)

        for fund, fund_data in fetch_results:
            if fund_data is None:
                continue
            try:
                fund_data_map[fund.code] = fund_data
                quarterly = quarterly_batch.get(fund.id, [])
                quarterly_data_map[fund.code] = quarterly
                # numpy 密集计算放线程池，避免阻塞事件循环（批量分析时拖慢所有并发请求）
                factor_scores = await asyncio.to_thread(
                    factor_engine.calculate_all, fund_data, regime_factors
                )
                all_factor_results[fund.code] = factor_scores
                logger.info(f"因子计算完成: {fund.code} ({fund.name}), {len(factor_scores)} 个因子")
            except Exception as e:
                logger.error(f"计算基金 {fund.code} 因子失败: {e}")
                continue

        # 6. 跨基金截面标准化
        all_factor_results = await asyncio.to_thread(
            factor_engine.normalize_cross_sectional, all_factor_results, regime_factors
        )

        # 7. 逐只基金评分 + 信号 + 存储
        results: list[AnalysisResultOut] = []
        for fund in funds:
            if fund.code not in all_factor_results:
                continue

            factor_scores = all_factor_results[fund.code]
            fund_data = fund_data_map.get(fund.code)
            quarterly = quarterly_data_map.get(fund.code, [])

            # ── 第零层：质量过滤 ──
            if fund_data is None:
                continue

            qf_result, corrected_scores, corrected_weights = qf.build_result(
                regime_snapshot=regime_snapshot,
                fund_code=fund.code,
                fund_data=fund_data,
                quarterly_history=quarterly,
                factor_scores=factor_scores,
                active_factors=active_factors,
            )

            # 被否决的基金跳过
            if qf_result.vetoed:
                logger.info(f"基金 {fund.code} 被前置否决: {qf_result.veto_reason}")
                continue

            # ── 加权评分 + 质量过滤决策 ──
            signal = compute_with_quality_filter(
                factor_scores=corrected_scores,
                factor_weights=corrected_weights,
                quality_result=qf_result,
                thresholds_json=thresholds_json,
            )

            # 生成报告
            analysis_date = date.today().isoformat()
            top10_changes = None
            if "top10_change" in enabled_report_items:
                top10_changes = await self._get_top10_changes(fund.id)
            report_md = report_engine.generate_markdown(
                fund_code=fund.code,
                fund_name=fund.name,
                analysis_date=analysis_date,
                signal=signal,
                factor_scores=corrected_scores,
                enabled_items=enabled_report_items,
                top10_changes=top10_changes,
            )

            # 存储结果（含质量过滤扩展字段）
            result_out = await self._save_result(
                fund, signal, corrected_scores,
                qf_result=qf_result,
            )
            if result_out:
                results.append(result_out)

        # 统一提交所有分析结果（替代原来逐条 commit，60 只基金=1 次提交）
        await self.db.commit()
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

        # 加载质量过滤配置
        merged_qf_config = await merge_quality_config(self.db)
        qf = build_quality_filter(merged_qf_config)

        # 获取市场环境快照（随参数传递，失败不阻塞主流程）
        try:
            from backend.services.market_regime_service import MarketRegimeService
            regime_snapshot = await MarketRegimeService().get_snapshot()
        except Exception as e:
            logger.warning(f"市场环境快照获取失败，市场因子将使用中性分: {e}")
            regime_snapshot = None
        regime_factors = [
            {**f, "params": _inject_regime_params(f.get("params"), regime_snapshot)}
            if f.get("code", "").startswith("market_") else f
            for f in active_factors
        ]

        # ── Phase 1: 逐只获取数据 + 计算因子（仅推进度，不推结果） ──
        fund_data_map: dict[str, FundData] = {}
        all_factor_results: dict[str, list[FactorScoreResult]] = {}
        quarterly_data_map: dict[str, list[dict]] = {}
        failed_codes: list[str] = []

        # 批量预加载季度数据，避免在循环内 N+1 查询
        quarterly_batch = await self._batch_load_quarterly_data([f.id for f in funds])

        for i, fund in enumerate(funds):
            try:
                fund_data = await self.data_source.get_fund_data(fund.code, fund_type=getattr(fund, "fund_type", None))
                fund_data_map[fund.code] = fund_data

                quarterly = quarterly_batch.get(fund.id, [])
                quarterly_data_map[fund.code] = quarterly

                factor_scores = await asyncio.to_thread(
                    factor_engine.calculate_all, fund_data, regime_factors
                )
                all_factor_results[fund.code] = factor_scores
            except Exception as e:
                logger.error(f"获取/计算基金 {fund.code} 失败: {e}")
                failed_codes.append(fund.code)
            # 每只基金都推送进度
            progress_data = {"type": "progress", "current": i + 1, "total": total, "fund_code": fund.code}
            yield "data: " + json.dumps(progress_data) + "\n\n"

        # 5. 跨基金截面标准化
        all_factor_results = await asyncio.to_thread(
            factor_engine.normalize_cross_sectional, all_factor_results, regime_factors
        )

        # ── Phase 2: 分块评分 + 存储 + 推送结果 ──
        results: list[AnalysisResultOut] = []

        for chunk_start in range(0, len(funds), _STREAM_CHUNK_SIZE):
            chunk = funds[chunk_start:chunk_start + _STREAM_CHUNK_SIZE]
            chunk_results: list[AnalysisResultOut] = []

            for fund in chunk:
                if fund.code not in all_factor_results:
                    continue

                factor_scores = all_factor_results[fund.code]
                fund_data = fund_data_map.get(fund.code)
                quarterly = quarterly_data_map.get(fund.code, [])

                if fund_data is None:
                    continue

                # 第零层：质量过滤
                qf_result, corrected_scores, corrected_weights = qf.build_result(
                    regime_snapshot=regime_snapshot,
                    fund_code=fund.code,
                    fund_data=fund_data,
                    quarterly_history=quarterly,
                    factor_scores=factor_scores,
                    active_factors=active_factors,
                )

                if qf_result.vetoed:
                    logger.info(f"流式分析: 基金 {fund.code} 被前置否决: {qf_result.veto_reason}")
                    continue

                signal = compute_with_quality_filter(
                    factor_scores=corrected_scores,
                    factor_weights=corrected_weights,
                    quality_result=qf_result,
                    thresholds_json=thresholds_json,
                )

                top10_changes = None
                if "top10_change" in enabled_report_items:
                    top10_changes = await self._get_top10_changes(fund.id)
                report_md = report_engine.generate_markdown(
                    fund_code=fund.code,
                    fund_name=fund.name,
                    analysis_date=date.today().isoformat(),
                    signal=signal,
                    factor_scores=corrected_scores,
                    enabled_items=enabled_report_items,
                    top10_changes=top10_changes,
                )

                result_out = await self._save_result(
                    fund, signal, corrected_scores,
                    qf_result=qf_result,
                )
                if result_out:
                    chunk_results.append(result_out)
                    results.append(result_out)

            # 批量提交本 chunk 的结果（替代原来逐条 commit）
            if chunk_results:
                await self.db.commit()

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

    async def _get_top10_changes(self, fund_id: int) -> Optional[list[dict]]:
        """前十大持仓当日涨跌（报告 top10_change 项）

        FundRealtimeService 内部有 60s 全市场快照缓存，批量分析时只请求一次行情。
        """
        try:
            from backend.services.fund_realtime_service import FundRealtimeService
            rt_svc = FundRealtimeService(self.db)
            changes = await rt_svc.get_top10_changes(fund_id)
            return changes or None
        except Exception as e:
            logger.warning(f"获取前十大持仓涨跌失败 fund_id={fund_id}: {e}")
            return None

    async def _save_result(
        self,
        fund: Fund,
        signal: SignalResult,
        factor_scores: list[FactorScoreResult],
        qf_result: Optional[QualityFilterResult] = None,
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

        # 不在此处 commit，由调用方在循环结束后统一提交，避免逐条提交（60 只=60 次提交）

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
            original_score=signal.original_score,
            dynamic_buy_threshold=signal.dynamic_buy_threshold,
            quality_warnings=signal.quality_warnings or None,
        )

    async def _get_config_map(self) -> dict[str, str]:
        """获取系统配置 KV 映射"""
        result = await self.db.execute(select(SystemConfig))
        configs = result.scalars().all()
        return {c.config_key: c.config_value for c in configs}

    # ── 历史报告导出/导入 ─────────────────────────────────────────

    async def export_analysis(self) -> AnalysisExportPayload:
        """导出全部分析结果为 JSON 载体"""
        result = await self.db.execute(
            select(AnalysisResult).order_by(AnalysisResult.analysis_date.desc())
        )
        records = result.scalars().all()

        items: list[AnalysisExportItem] = []
        # 批量加载基金，避免 N+1 查询
        fund_ids = {r.fund_id for r in records}
        if fund_ids:
            fund_result = await self.db.execute(select(Fund).where(Fund.id.in_(fund_ids)))
            fund_map = {f.id: f for f in fund_result.scalars().all()}
        else:
            fund_map = {}
        for r in records:
            fund = fund_map.get(r.fund_id)
            factor_scores = json.loads(r.factor_scores) if isinstance(r.factor_scores, str) else (r.factor_scores or {})

            item = AnalysisExportItem(
                fund_code=fund.code if fund else "",
                fund_name=fund.name if fund else "",
                analysis_date=r.analysis_date.isoformat() if hasattr(r.analysis_date, "isoformat") else str(r.analysis_date),
                weighted_score=r.weighted_score,
                signal_direction=r.signal_direction,
                signal_strength=r.signal_strength or "",
                operation_advice=r.operation_advice or "",
                equity_ratio=r.equity_ratio,
                factor_scores=factor_scores if isinstance(factor_scores, dict) else {},
            )
            items.append(item)

        return AnalysisExportPayload(
            version="1.0",
            exported_at=datetime.now().isoformat(timespec="seconds"),
            items=items,
        )

    async def import_analysis(
        self,
        payload: AnalysisExportPayload,
        overwrite: bool = False,
    ) -> AnalysisImportResult:
        """从 JSON 载体导入分析结果

        Args:
            payload: 导入载体
            overwrite: 已存在的记录是否覆盖（默认跳过）
        """
        result = AnalysisImportResult()
        for item in payload.items:
            try:
                # 查找 fund_id
                fund_result = await self.db.execute(
                    select(Fund).where(Fund.code == item.fund_code)
                )
                fund = fund_result.scalars().first()
                if not fund:
                    result.errors.append(f"基金代码不存在: {item.fund_code}")
                    continue

                analysis_date = date.fromisoformat(item.analysis_date)

                existing_result = await self.db.execute(
                    select(AnalysisResult).where(
                        AnalysisResult.fund_id == fund.id,
                        AnalysisResult.analysis_date == analysis_date,
                    )
                )
                existing = existing_result.scalars().first()

                factor_scores_json = json.dumps(item.factor_scores, ensure_ascii=False) if item.factor_scores else "{}"

                if existing:
                    if not overwrite:
                        result.skipped += 1
                        continue
                    existing.weighted_score = item.weighted_score
                    existing.signal_direction = item.signal_direction
                    existing.signal_strength = item.signal_strength
                    existing.operation_advice = item.operation_advice
                    existing.equity_ratio = item.equity_ratio
                    existing.factor_scores = factor_scores_json
                    result.updated += 1
                else:
                    new_record = AnalysisResult(
                        fund_id=fund.id,
                        analysis_date=analysis_date,
                        weighted_score=item.weighted_score,
                        signal_direction=item.signal_direction,
                        signal_strength=item.signal_strength,
                        operation_advice=item.operation_advice,
                        equity_ratio=item.equity_ratio,
                        factor_scores=factor_scores_json,
                    )
                    self.db.add(new_record)
                    result.created += 1

            except Exception as e:
                result.errors.append(f"导入失败 [{item.fund_code}/{item.analysis_date}]: {e}")

        if result.created > 0 or result.updated > 0:
            await self.db.commit()

        return result
