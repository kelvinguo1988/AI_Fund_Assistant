"""分析编排服务 — 数据获取→因子计算→评分→信号→存储→推送"""
from backend.utils.timezone import now_beijing

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, AsyncGenerator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data_sources.data_source_manager import DataSourceManager
from backend.data_sources.base import FundData
from backend.engines.factor_engine import factor_engine, FactorScoreResult
from backend.engines.scoring_engine import SignalResult, compute_with_quality_filter
from backend.engines.quality_filter import (
    quality_filter as _default_quality_filter,
    QualityFilterResult,
    merge_quality_config,
    build_quality_filter,
    apply_otc_trade_constraint,
)
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.models.fund_quarterly import FundQuarterly
from backend.models.system_config import SystemConfig
from backend.schemas.analysis import (
    AnalysisResultOut, FactorScore,
    AnalysisExportItem, AnalysisExportPayload, AnalysisImportResult,
)

logger = logging.getLogger(__name__)

# 流式处理块大小：每批处理 5 只基金后推送一次结果
_STREAM_CHUNK_SIZE = 5

# 第零层质量过滤的空数据告警只提示一次（避免每轮分析刷屏）
_quarterly_empty_warned = False


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


@dataclass
class _AnalysisConfig:
    """一轮分析的共享配置快照（批量与流式路径同一来源，防双份漂移）"""

    active_factors: list[dict]
    thresholds_json: str
    qf: Any
    regime_snapshot: Any
    regime_factors: list[dict]
    # 场外申购/赎回状态 {code: {"purchase","redeem","fee"}}；空 = 不可用时跳过约束
    otc_status_map: dict = field(default_factory=dict)


class AnalysisService:
    # 进程级互斥：调度推送与手动触发（批量/流式）串行执行，
    # 防止并发写 analysis_results 触发 uq_fund_date 唯一约束整批失败
    _run_lock: Optional[asyncio.Lock] = None

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
        if not records:
            # fund_quarterly 目前全仓无写入链路（只有读取），空表意味着
            # 清盘否决/规模冲击/仓位漂移/机构认可度全部静默失效 —— 显式告警而非假装有数据
            global _quarterly_empty_warned
            if not _quarterly_empty_warned:
                _quarterly_empty_warned = True
                logger.warning(
                    "第零层质量过滤未生效：fund_quarterly 表为空（无季报同步任务写入），"
                    "清盘风险/规模冲击/仓位漂移/机构认可度检查均按中性处理"
                )
            return {}
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
        """批量分析（进程级互斥，见 _run_lock）"""
        if AnalysisService._run_lock is None:
            AnalysisService._run_lock = asyncio.Lock()
        async with AnalysisService._run_lock:
            return await self._run_analysis_locked(fund_ids=fund_ids)

    async def _run_analysis_locked(
        self,
        fund_ids: Optional[list[int]] = None,
    ) -> list[AnalysisResultOut]:
        """执行分析流程（配置准备与逐基金评分与流式路径共用，防双份漂移）

        流程：
        1. 获取基金列表 + 活跃因子配置 + 系统配置
        2. 逐只基金获取数据、计算因子原始值
        3. 跨基金截面标准化（如波动率倒数）
        4. 逐只基金计算加权评分、信号
        5. 存储结果并返回
        """
        # 1. 获取基金池
        funds = list(await self._load_fund_pool(fund_ids))
        if not funds:
            logger.warning("没有启用的基金，跳过分析")
            return []

        # 2. 因子/阈值/质量过滤/市场环境配置
        cfg = await self._load_analysis_config()
        if cfg is None:
            return []

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
                logger.error(
                    f"获取基金 {fund.code} 数据失败: {type(e).__name__}: {e}",
                    exc_info=True,
                )
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
                    factor_engine.calculate_all, fund_data, cfg.regime_factors
                )
                all_factor_results[fund.code] = factor_scores
                logger.info(f"因子计算完成: {fund.code} ({fund.name}), {len(factor_scores)} 个因子")
            except Exception as e:
                logger.error(
                    f"计算基金 {fund.code} 因子失败: {type(e).__name__}: {e}",
                    exc_info=True,
                )
                continue

        # 6.5 跨基金截面标准化
        all_factor_results = await asyncio.to_thread(
            factor_engine.normalize_cross_sectional, all_factor_results, cfg.regime_factors
        )

        # 7. 逐只基金评分 + 存储（与流式路径共用 _score_and_store）
        results: list[AnalysisResultOut] = []
        for fund in funds:
            if fund.code not in all_factor_results:
                continue
            fund_data = fund_data_map.get(fund.code)
            if fund_data is None:
                continue

            # 2026-08-29 修复：单基金评分异常不再中止整轮分析
            #（原先任何异常会让最终 commit 不执行，全部结果丢失且接口 500）
            try:
                result_out = await self._score_and_store(
                    fund, cfg,
                    factor_scores=all_factor_results[fund.code],
                    fund_data=fund_data,
                    quarterly=quarterly_data_map.get(fund.code, []),
                )
                if result_out:
                    results.append(result_out)
            except Exception as fund_err:
                logger.error(f"基金 {fund.code} 评分失败，跳过: {fund_err}", exc_info=True)
                continue

        # 统一提交所有分析结果（替代原来逐条 commit，60 只基金=1 次提交）
        await self.db.commit()
        return results

    async def run_analysis_streaming(
        self,
        fund_ids: Optional[list[int]] = None,
    ) -> AsyncGenerator[str, None]:
        """流式分析（进程级互斥，见 _run_lock）；连接中断时锁随生成器关闭释放"""
        if AnalysisService._run_lock is None:
            AnalysisService._run_lock = asyncio.Lock()
        async with AnalysisService._run_lock:
            async for event in self._run_analysis_streaming_locked(fund_ids=fund_ids):
                yield event

    async def _run_analysis_streaming_locked(
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
        funds = list(await self._load_fund_pool(fund_ids))

        if not funds:
            yield "data: " + json.dumps({"type": "complete", "total": 0, "succeeded": 0}) + "\n\n"
            return

        total = len(funds)

        # 2. 因子/阈值/质量过滤/市场环境配置（与批量路径共用，防双份漂移）
        cfg = await self._load_analysis_config()
        if cfg is None:
            yield "data: " + json.dumps({"type": "complete", "total": total, "succeeded": 0, "error": "没有启用的因子"}) + "\n\n"
            return

        # ── Phase 1: 逐只获取数据 + 计算因子（仅推进度，不推结果） ──
        fund_data_map: dict[str, FundData] = {}
        all_factor_results: dict[str, list[FactorScoreResult]] = {}
        quarterly_data_map: dict[str, list[dict]] = {}
        failed_codes: list[str] = []

        # 批量预加载季度数据，避免在循环内 N+1 查询
        quarterly_batch = await self._batch_load_quarterly_data([f.id for f in funds])

        # 2026-08-29 修复：原逐只串行 fetch（60 只 × 最坏 25-80s/只），与批量路径
        # 不一致。改为并发 fetch（网络层并发由 adapter 信号量控制，不碰 DB，
        # as_completed 保持逐只进度推送体验）
        async def _fetch_and_score(fund: Fund):
            try:
                fd = await self.data_source.get_fund_data(
                    fund.code, fund_type=getattr(fund, "fund_type", None)
                )
                fs = await asyncio.to_thread(factor_engine.calculate_all, fd, cfg.regime_factors)
                return fund, fd, fs, None
            except Exception as e:
                logger.error(
                    f"获取/计算基金 {fund.code} 失败: {type(e).__name__}: {e}",
                    exc_info=True,
                )
                return fund, None, None, str(e)

        done_count = 0
        for fut in asyncio.as_completed([_fetch_and_score(f) for f in funds]):
            fund, fd, fs, err = await fut
            done_count += 1
            if err is not None:
                failed_codes.append(fund.code)
            else:
                fund_data_map[fund.code] = fd
                quarterly_data_map[fund.code] = quarterly_batch.get(fund.id, [])
                all_factor_results[fund.code] = fs
            # 每只基金都推送进度
            progress_data = {"type": "progress", "current": done_count, "total": total, "fund_code": fund.code}
            yield "data: " + json.dumps(progress_data) + "\n\n"

        # 5. 跨基金截面标准化
        all_factor_results = await asyncio.to_thread(
            factor_engine.normalize_cross_sectional, all_factor_results, cfg.regime_factors
        )

        # ── Phase 2: 分块评分 + 存储 + 推送结果（与批量路径共用 _score_and_store） ──
        results: list[AnalysisResultOut] = []

        for chunk_start in range(0, len(funds), _STREAM_CHUNK_SIZE):
            chunk = funds[chunk_start:chunk_start + _STREAM_CHUNK_SIZE]
            chunk_results: list[AnalysisResultOut] = []

            for fund in chunk:
                if fund.code not in all_factor_results:
                    continue

                fund_data = fund_data_map.get(fund.code)
                if fund_data is None:
                    continue

                # 2026-08-29 修复：单基金异常不影响后续基金与 complete 事件
                try:
                    result_out = await self._score_and_store(
                        fund, cfg,
                        factor_scores=all_factor_results[fund.code],
                        fund_data=fund_data,
                        quarterly=quarterly_data_map.get(fund.code, []),
                    )
                    if result_out:
                        chunk_results.append(result_out)
                        results.append(result_out)
                except Exception as fund_err:
                    logger.error(f"基金 {fund.code} 评分失败，跳过: {fund_err}", exc_info=True)
                    continue

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

    async def _load_fund_pool(self, fund_ids: Optional[list[int]]) -> list[Fund]:
        """待分析基金池（批量/流式共用）"""
        stmt = select(Fund).where(Fund.status == "active")
        if fund_ids:
            stmt = stmt.where(Fund.id.in_(fund_ids))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _load_analysis_config(self) -> Optional[_AnalysisConfig]:
        """一轮分析的共享配置：因子/阈值/质量过滤/市场环境快照

        无启用因子返回 None（调用方决定终止方式：批量返回 []，流式发 complete）。
        """
        from backend.services.factor_service import FactorService
        factor_svc = FactorService(self.db)
        active_factors = await factor_svc.get_active_factors_as_dicts()
        if not active_factors:
            logger.warning("没有启用的因子，跳过分析")
            return None

        # buy/sell_threshold 旧配置键已删：真正生效的是五档 scoring_thresholds
        # 与质量过滤动态阈值（base_buy/sell_threshold），此前载入后从未消费
        config_map = await self._get_config_map()
        thresholds_json = config_map.get("scoring_thresholds", "")

        # 质量过滤配置（DB 覆盖默认值）
        merged_qf_config = await merge_quality_config(self.db)
        qf = build_quality_filter(merged_qf_config)

        # 市场环境快照随参数传递（任务间隔离，无模块级全局竞态）；
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
        # 场外申购状态（类级缓存 1h，不额外打接口）；失败/测试屏蔽时空 dict，
        # 买入可执行性约束自动跳过，不阻塞主流程
        otc_status_map: dict = {}
        try:
            from backend.services.index_valuation_service import OtcTradeStatusService
            otc_status_map = await OtcTradeStatusService.get_status_map()
        except Exception as e:
            logger.warning(f"场外申购状态获取失败，跳过买入可执行性约束: {e}")
        return _AnalysisConfig(
            active_factors=active_factors,
            thresholds_json=thresholds_json,
            qf=qf,
            regime_snapshot=regime_snapshot,
            regime_factors=regime_factors,
            otc_status_map=otc_status_map,
        )

    async def _score_and_store(
        self,
        fund: Fund,
        cfg: _AnalysisConfig,
        factor_scores: list[FactorScoreResult],
        fund_data: FundData,
        quarterly: list[dict],
    ) -> Optional[AnalysisResultOut]:
        """第零层质量过滤 → 加权评分 → 存储（批量/流式共用，防双份漂移）

        返回 None = 被前置否决。报告正文在查询/推送时按需生成，
        分析路径不再计算（原两份 generate_markdown 结果从未落库，纯耗 CPU）。
        """
        qf_result, corrected_scores, corrected_weights = cfg.qf.build_result(
            regime_snapshot=cfg.regime_snapshot,
            fund_code=fund.code,
            fund_data=fund_data,
            quarterly_history=quarterly,
            factor_scores=factor_scores,
            active_factors=cfg.active_factors,
        )
        if qf_result.vetoed:
            logger.info(f"基金 {fund.code} 被前置否决: {qf_result.veto_reason}")
            return None

        signal = compute_with_quality_filter(
            factor_scores=corrected_scores,
            factor_weights=corrected_weights,
            quality_result=qf_result,
            thresholds_json=cfg.thresholds_json,
        )
        # 场外申购可执行性约束：暂停申购/封闭期 → 买入降级观望（可配开关）
        signal = apply_otc_trade_constraint(
            signal,
            cfg.otc_status_map.get(fund.code),
            cfg.qf.cfg,
        )
        return await self._save_result(
            fund, signal, corrected_scores, qf_result=qf_result,
        )

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

        # 诊断地基字段（2026-09-23）：修正前原始分/动态阈值/质量警告落库，供 factor_audit 历史回算
        diag_fields = {
            "original_score": signal.original_score,
            "dynamic_buy_threshold": signal.dynamic_buy_threshold,
            "dynamic_sell_threshold": signal.dynamic_sell_threshold,
            "quality_warnings": json.dumps(signal.quality_warnings, ensure_ascii=False)
            if signal.quality_warnings else None,
        }

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
            for k, v in diag_fields.items():
                setattr(existing, k, v)
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
                **diag_fields,
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
            created_at=now_beijing(),
            original_score=signal.original_score,
            dynamic_buy_threshold=signal.dynamic_buy_threshold,
            dynamic_sell_threshold=signal.dynamic_sell_threshold,
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
                original_score=r.original_score,
                dynamic_buy_threshold=r.dynamic_buy_threshold,
                dynamic_sell_threshold=r.dynamic_sell_threshold,
                quality_warnings=json.loads(r.quality_warnings) if r.quality_warnings else None,
            )
            items.append(item)

        return AnalysisExportPayload(
            version="1.0",
            exported_at=now_beijing().isoformat(timespec="seconds"),
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

                import_diag = {
                    "original_score": item.original_score,
                    "dynamic_buy_threshold": item.dynamic_buy_threshold,
                    "dynamic_sell_threshold": item.dynamic_sell_threshold,
                    "quality_warnings": json.dumps(item.quality_warnings, ensure_ascii=False)
                    if item.quality_warnings else None,
                }

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
                    for k, v in import_diag.items():
                        setattr(existing, k, v)
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
                        **import_diag,
                    )
                    self.db.add(new_record)
                    result.created += 1

            except Exception as e:
                result.errors.append(f"导入失败 [{item.fund_code}/{item.analysis_date}]: {e}")

        if result.created > 0 or result.updated > 0:
            await self.db.commit()

        return result
