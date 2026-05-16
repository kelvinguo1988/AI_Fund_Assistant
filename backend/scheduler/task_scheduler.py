from __future__ import annotations
"""APScheduler 封装 — 启动/停止/热更新"""

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from backend.database import async_session_factory
from backend.models.schedule import Schedule

logger = logging.getLogger(__name__)


class TaskScheduler:
    """APScheduler 封装

    - 启动时从 schedules 表加载启用任务
    - 支持 Cron + 固定时间触发
    - 前台增删改后热更新调度器
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    def start(self) -> None:
        """启动调度器"""
        self._scheduler.start()
        logger.info("调度器已启动")
        # 启动后加载任务
        import asyncio
        asyncio.create_task(self.reload_jobs())

    def shutdown(self) -> None:
        """停止调度器"""
        self._scheduler.shutdown(wait=False)
        logger.info("调度器已停止")

    async def reload_jobs(self) -> None:
        """从数据库重新加载所有调度任务"""
        # 移除所有已有任务
        for job in self._scheduler.get_jobs():
            self._scheduler.remove_job(job.id)

        # 从数据库加载
        async with async_session_factory() as session:
            result = await session.execute(
                select(Schedule).where(Schedule.enabled == True)
            )
            schedules = result.scalars().all()

            for sched in schedules:
                try:
                    trigger = self._build_trigger(sched)
                    if trigger:
                        self._scheduler.add_job(
                            self._run_task,
                            trigger=trigger,
                            id=f"schedule_{sched.id}",
                            args=[sched.id],
                            replace_existing=True,
                        )
                        logger.info(f"已加载调度: {sched.name} (id={sched.id})")
                except Exception as e:
                    logger.error(f"加载调度失败 {sched.name}: {e}")

    def _build_trigger(self, sched: Schedule) -> CronTrigger | None:
        """根据调度配置构建触发器

        优先使用 cron_expr，其次使用 time_point
        """
        if sched.cron_expr:
            try:
                parts = sched.cron_expr.strip().split()
                if len(parts) == 5:
                    return CronTrigger(
                        minute=parts[0],
                        hour=parts[1],
                        day=parts[2],
                        month=parts[3],
                        day_of_week=parts[4],
                        timezone="Asia/Shanghai",
                    )
            except Exception as e:
                logger.warning(f"Cron 表达式解析失败: {sched.cron_expr}, {e}")

        if sched.time_point:
            try:
                hour, minute = sched.time_point.split(":")
                # 工作日触发
                return CronTrigger(
                    hour=int(hour),
                    minute=int(minute),
                    day_of_week="mon-fri",
                    timezone="Asia/Shanghai",
                )
            except Exception as e:
                logger.warning(f"时间点解析失败: {sched.time_point}, {e}")

        return None

    async def _run_task(self, schedule_id: int) -> None:
        """执行调度任务"""
        logger.info(f"调度任务开始执行: schedule_id={schedule_id}")

        try:
            from backend.services.analysis_service import AnalysisService
            from backend.data_sources.trading_calendar import is_trading_day
            from datetime import date as date_type

            # 检查是否交易日
            if not is_trading_day(date_type.today()):
                logger.info(f"今天不是交易日，跳过调度 schedule_id={schedule_id}")
                return

            async with async_session_factory() as session:
                # 更新上次运行时间
                result = await session.execute(
                    select(Schedule).where(Schedule.id == schedule_id)
                )
                sched = result.scalars().first()
                if sched:
                    sched.last_run_at = datetime.now()

                await session.commit()

                # 执行分析
                svc = AnalysisService(session)
                results = await svc.run_analysis()

                # 推送
                if sched and sched.channel_id:
                    from backend.services.push_service import PushService
                    push_svc = PushService(session)
                    await push_svc.push_analysis_results(results, sched.channel_id)

            logger.info(f"调度任务完成: schedule_id={schedule_id}")
        except Exception as e:
            logger.error(f"调度任务执行失败 schedule_id={schedule_id}: {e}")


# 全局实例
task_scheduler = TaskScheduler()
