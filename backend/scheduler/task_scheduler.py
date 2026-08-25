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
        # 注册调休自动同步任务（每日检查一次，同步成功后自动停用）
        asyncio.create_task(self._register_holiday_sync())

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
                            # 防止长任务与下一次触发重叠执行；错过触发点 5 分钟内补跑
                            max_instances=1,
                            coalesce=True,
                            misfire_grace_time=300,
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
            from backend.data_sources.trading_calendar import (
                is_a_share_trading_day_async,
            )
            from datetime import date as date_type

            # 严格 A 股交易日闸门：周末 + 法定休市日（含调休休息日）一律不推送。
            # 数据源优先级：holiday_calendar 表（已同步国务院放假安排，含调休）
            #   → 缺失时回退 chinese_calendar。
            async with async_session_factory() as session:
                if not await is_a_share_trading_day_async(session, date_type.today()):
                    logger.info(
                        f"今天({date_type.today()})非 A 股交易日，跳过调度 "
                        f"schedule_id={schedule_id}"
                    )
                    return

                # 更新上次运行时间
                result = await session.execute(
                    select(Schedule).where(Schedule.id == schedule_id)
                )
                sched = result.scalars().first()
                if sched:
                    sched.last_run_at = datetime.now()

                await session.commit()

                # 执行分析
                from backend.config import settings
                svc = AnalysisService(
                    session,
                    joinquant_user=settings.JOINQUANT_USER,
                    joinquant_password=settings.JOINQUANT_PASSWORD,
                )
                results = await svc.run_analysis()

                # 推送
                if sched and sched.channel_id:
                    from backend.services.push_service import PushService
                    push_svc = PushService(session)
                    await push_svc.push_analysis_results(results, sched.channel_id)

            logger.info(f"调度任务完成: schedule_id={schedule_id}")
        except Exception as e:
            logger.error(f"调度任务执行失败 schedule_id={schedule_id}: {e}")


    async def _register_holiday_sync(self) -> None:
        """注册调休自动同步任务（每日在 holiday_auto_sync_time 触发一次）"""
        try:
            from sqlalchemy import select

            from backend.models.system_config import SystemConfig

            async with async_session_factory() as session:
                row = (await session.execute(
                    select(SystemConfig).where(SystemConfig.config_key == "holiday_auto_sync_time")
                )).scalars().first()
                tmpl = row.config_value if row else "03:00"
            hh, mm = tmpl.split(":")
            trigger = CronTrigger(
                hour=int(hh), minute=int(mm), day_of_week="*", timezone="Asia/Shanghai"
            )
            self._scheduler.add_job(
                self._run_holiday_auto_sync,
                trigger=trigger,
                id="holiday_auto_sync",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,
            )
            logger.info(f"已注册调休自动同步任务 (每日 {tmpl})")
        except Exception as e:  # noqa: BLE001
            logger.error(f"注册调休自动同步任务失败: {e}")

    async def _run_holiday_auto_sync(self) -> None:
        """执行调休自动同步（受 holiday_auto_sync_enabled 开关控制，成功后停用）"""
        try:
            from backend.services.holiday_sync_service import auto_sync_if_enabled

            async with async_session_factory() as session:
                summary = await auto_sync_if_enabled(session)
            logger.info(f"调休自动同步执行完成: {summary}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"调休自动同步执行失败: {e}")


# 全局实例
task_scheduler = TaskScheduler()
