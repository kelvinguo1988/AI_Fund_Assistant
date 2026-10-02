"""自动全量回测服务 — 定时对全部活跃基金跑信号回测并落库

设计（2026-08-30）：
- 调度：每周日 0 点（Asia/Shanghai，CronTrigger），system_config 开关控制；
  max_instances=1 防重叠
- 防封：周末低峰 + 每只基金之间 sleep 随机间隔（默认 20~60s，可配置），
  60 只基金约 30~60 分钟完成，远低于 12 小时上限
- 落库：backtest_results 表以 fund_id 唯一键逐行覆盖（含该基金完成时间），
  前端批量结果页跑的过程中即可看到逐只更新；周期性覆盖上一轮
- 单只失败不影响其余：error 记录到行内，ok=False
"""
from backend.utils.timezone import now_beijing

import asyncio
import logging
import random
import time
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.backtest_result import BacktestResult
from backend.models.fund import Fund
from backend.models.system_config import SystemConfig

logger = logging.getLogger(__name__)

# system_config 键
CFG_ENABLED = "auto_backtest_enabled"
CFG_MIN_INTERVAL = "auto_backtest_interval_min"   # 秒
CFG_MAX_INTERVAL = "auto_backtest_interval_max"   # 秒

DEFAULT_MIN_INTERVAL = 20.0
DEFAULT_MAX_INTERVAL = 60.0

# 全量回测 12 小时兜底上限（超时强制结束，防止单只 hang 死拖住整轮）
RUN_TIMEOUT_SECONDS = 12 * 3600.0


async def get_auto_config(db: AsyncSession) -> dict:
    """读取自动回测配置"""
    rows = (await db.execute(select(SystemConfig))).scalars().all()
    kv = {r.config_key: r.config_value for r in rows}
    def _f(key: str, default: float) -> float:
        try:
            return float(kv.get(key, default))
        except (TypeError, ValueError):
            return default
    return {
        "enabled": kv.get(CFG_ENABLED, "false").lower() == "true",
        "min_interval": _f(CFG_MIN_INTERVAL, DEFAULT_MIN_INTERVAL),
        "max_interval": _f(CFG_MAX_INTERVAL, DEFAULT_MAX_INTERVAL),
    }


class AutoBacktestService:
    """自动全量回测任务"""

    _running = False  # 进程级防重入（调度触发 + 手动触发互斥）

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run_full_backtest(self, force: bool = False) -> dict:
        """全量回测全部活跃基金（逐只落库）

        Returns:
            {total, ok, failed, skipped}
        """
        if AutoBacktestService._running and not force:
            logger.info("自动回测已在运行中，跳过本次触发")
            return {"total": 0, "ok": 0, "failed": 0, "skipped": 1}
        AutoBacktestService._running = True
        start_ts = time.time()
        try:
            cfg = await get_auto_config(self.db)
            lo = max(1.0, cfg["min_interval"])
            hi = max(lo, cfg["max_interval"])

            funds = list((await self.db.execute(
                select(Fund).where(Fund.status == "active")
            )).scalars().all())
            total = ok = failed = 0

            from backend.services.backtest_service import (
                BacktestService, load_fee_pct, load_measurement_policy,
            )
            svc = BacktestService(self.db)
            # 一轮一个费率/口径：逐只重读会让中途改配置导致同轮结果不可比
            fee_pct = await load_fee_pct(self.db)
            policy = await load_measurement_policy(self.db)

            for i, fund in enumerate(funds):
                # 12 小时兜底：单轮超时则终止，已完成的逐只结果保留
                if time.time() - start_ts > RUN_TIMEOUT_SECONDS:
                    logger.warning("自动回测超过 12 小时兜底上限，终止本轮")
                    break
                total += 1
                try:
                    # 2026-08-30 修复：单只 hang 死原会让循环永远到不了 12h
                    # 检查点，_running 永久锁死（后续触发全部 409/跳过）。
                    # 单只上限 10 分钟（净值拉取重试最坏 ~80s × 3 接口 + 余量）
                    summary = await asyncio.wait_for(
                        svc.run_backtest(
                            fund_id=fund.id, fee_pct=fee_pct, policy=policy
                        ),
                        timeout=600.0,
                    )
                    if summary is None:
                        # run_backtest 用 None 表示"基金不存在/无净值"，
                        # 直接落行会让前端看到 'NoneType' object has no attribute 之类的假错误。
                        # 注意不走 continue：防封间隔（循环末尾的 sleep）必须照样执行
                        failed += 1
                        await self._reset_session()
                        try:
                            await self._upsert_error(fund, "无净值数据")
                        except Exception as ue:
                            logger.error(f"自动回测失败行落库失败 {fund.code}: {ue}")
                    else:
                        await self._upsert_result(fund, summary)
                        ok += 1
                        logger.info(
                            f"自动回测 [{i + 1}/{len(funds)}] {fund.code} 完成"
                        )
                except asyncio.TimeoutError:
                    failed += 1
                    logger.error(f"自动回测 {fund.code} 超时(10 分钟)，记为失败")
                    # 超时/异常可能留下已 flush 未提交的部分写入：不回滚则 session 卡在
                    # PendingRollbackError，_upsert_error 与后续每只基金都会连锁失败
                    await self._reset_session()
                    try:
                        await self._upsert_error(fund, "回测超时(10 分钟)")
                    except Exception as ue:
                        logger.error(f"自动回测失败行落库失败 {fund.code}: {ue}")
                except Exception as e:
                    failed += 1
                    logger.error(f"自动回测 {fund.code} 失败: {e}")
                    await self._reset_session()
                    try:
                        await self._upsert_error(fund, str(e)[:180])
                    except Exception as ue:
                        logger.error(f"自动回测失败行落库失败 {fund.code}: {ue}")

                # 周末防封：只与下一只之间拉长随机间隔（最后一只不等待）
                if i < len(funds) - 1:
                    await asyncio.sleep(random.uniform(lo, hi))

            logger.info(
                f"自动全量回测结束: total={total} ok={ok} failed={failed} "
                f"耗时={(time.time() - start_ts) / 60:.1f} 分钟"
            )
            return {"total": total, "ok": ok, "failed": failed, "skipped": 0}
        finally:
            AutoBacktestService._running = False

    async def _reset_session(self) -> None:
        """回滚到干净事务边界；单只基金失败不能污染后续所有基金"""
        try:
            await self.db.rollback()
        except Exception as rb_err:
            logger.error(f"回测会话回滚失败: {rb_err}")

    async def _upsert_result(self, fund: Fund, summary) -> None:
        """逐基金覆盖落库（fund_id 唯一）"""
        row = (await self.db.execute(
            select(BacktestResult).where(BacktestResult.fund_id == fund.id)
        )).scalars().first()
        if row is None:
            row = BacktestResult(fund_id=fund.id)
            self.db.add(row)
        row.fund_code = fund.code
        row.fund_name = fund.name or fund.code
        row.period = summary.period
        row.effectiveness_window = summary.effectiveness_window
        row.total_nav_return = summary.total_nav_return
        row.total_strategy_return = summary.total_strategy_return
        row.excess_return = summary.excess_return
        row.max_drawdown = summary.max_drawdown
        row.signal_count = summary.signal_count
        row.avg_effectiveness = summary.avg_effectiveness
        row.buy_effectiveness = summary.buy_effectiveness
        row.sell_effectiveness = summary.sell_effectiveness
        row.effectiveness_rate = summary.effectiveness_rate
        # Q6 度量口径：基线 / 样本下限 / 覆盖度（前端据此决定画不画结论）
        row.baseline_buy_hold = summary.baseline_buy_hold
        row.baseline_static_half = summary.baseline_static_half
        row.excess_vs_static_half = summary.excess_vs_static_half
        row.signal_count_non_hold = summary.signal_count_non_hold
        row.signal_coverage_ratio = summary.signal_coverage_ratio
        row.low_sample = summary.low_sample
        row.caveat = summary.caveat
        row.coverage_start_date = summary.coverage_start_date
        row.coverage_days = summary.coverage_days
        row.pool_size_at = summary.pool_size_at
        row.carry_position = summary.carry_position
        row.finished_at = now_beijing()
        row.error = None
        row.ok = True
        await self.db.commit()

    async def _upsert_error(self, fund: Fund, message: str) -> None:
        """失败也落行（保留旧数值，仅更新错误与时间），前端可见失败状态"""
        row = (await self.db.execute(
            select(BacktestResult).where(BacktestResult.fund_id == fund.id)
        )).scalars().first()
        if row is None:
            row = BacktestResult(fund_id=fund.id, fund_code=fund.code, fund_name=fund.name or "")
            self.db.add(row)
        row.error = message
        row.ok = False
        row.finished_at = now_beijing()
        await self.db.commit()

    @staticmethod
    async def clear_results(db: AsyncSession) -> int:
        """清空批量结果"""
        result = await db.execute(delete(BacktestResult))
        await db.commit()
        return result.rowcount
