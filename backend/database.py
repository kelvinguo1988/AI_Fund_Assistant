"""SQLAlchemy 异步引擎 + Session 工厂 + 初始化函数"""

import json
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings


# ── 异步引擎 & Session 工厂 ──────────────────────────────────────────
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Declarative Base ─────────────────────────────────────────────────
class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""
    pass


# ── 依赖注入：获取 DB Session ────────────────────────────────────────
async def get_db() -> AsyncSession:
    """FastAPI 依赖注入用，yield 一个 async session"""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


# ── 初始化数据库 ─────────────────────────────────────────────────────
async def init_db() -> None:
    """
    创建所有表并插入初始数据：
    - 5 个默认因子
    - 默认报告配置
    - 默认系统配置
    """
    # 导入所有模型以确保 Base.metadata 知道它们
    from backend.models import (  # noqa: F401
        Fund,
        Factor,
        PushChannel,
        Schedule,
        ReportConfig,
        AnalysisResult,
        AIConversation,
        SystemConfig,
    )

    # 建表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 插入初始数据
    async with async_session_factory() as session:
        # ── 检查是否已有因子数据 ──
        from sqlalchemy import select
        result = await session.execute(select(Factor).limit(1))
        if result.scalars().first() is None:
            now = datetime.now()
            factors = [
                Factor(
                    name="PE百分位",
                    code="pe_percentile",
                    data_field="pe",
                    weight=1.5,
                    direction="positive",
                    params=json.dumps({"period": 5}),
                    status="active",
                    sort_order=1,
                    created_at=now,
                    updated_at=now,
                ),
                Factor(
                    name="股债性价比FED",
                    code="fed_model",
                    data_field="fed",
                    weight=1.5,
                    direction="positive",
                    params=json.dumps({}),
                    status="active",
                    sort_order=2,
                    created_at=now,
                    updated_at=now,
                ),
                Factor(
                    name="MACD信号",
                    code="macd_signal",
                    data_field="macd",
                    weight=1.0,
                    direction="positive",
                    params=json.dumps({"fast": 12, "slow": 26, "signal": 9}),
                    status="active",
                    sort_order=3,
                    created_at=now,
                    updated_at=now,
                ),
                Factor(
                    name="均线趋势",
                    code="ma_trend",
                    data_field="ma",
                    weight=1.0,
                    direction="positive",
                    params=json.dumps({"short_period": 20, "long_period": 60}),
                    status="active",
                    sort_order=4,
                    created_at=now,
                    updated_at=now,
                ),
                Factor(
                    name="成交量变化",
                    code="volume_change",
                    data_field="volume",
                    weight=1.0,
                    direction="positive",
                    params=json.dumps({"period": 20}),
                    status="active",
                    sort_order=5,
                    created_at=now,
                    updated_at=now,
                ),
            ]
            session.add_all(factors)

        # ── 检查是否已有报告配置 ──
        result = await session.execute(select(ReportConfig).limit(1))
        if result.scalars().first() is None:
            now = datetime.now()
            report_configs = [
                ReportConfig(
                    name="因子详情",
                    item_key="factor_detail",
                    enabled=True,
                    sort_order=1,
                    created_at=now,
                ),
                ReportConfig(
                    name="加权评分",
                    item_key="weighted_score",
                    enabled=True,
                    sort_order=2,
                    created_at=now,
                ),
                ReportConfig(
                    name="操作建议",
                    item_key="operation_advice",
                    enabled=True,
                    sort_order=3,
                    created_at=now,
                ),
                ReportConfig(
                    name="信号强度",
                    item_key="signal_strength",
                    enabled=True,
                    sort_order=4,
                    created_at=now,
                ),
                ReportConfig(
                    name="风险提示",
                    item_key="risk_warning",
                    enabled=True,
                    sort_order=5,
                    created_at=now,
                ),
            ]
            session.add_all(report_configs)

        # ── 检查是否已有系统配置 ──
        result = await session.execute(select(SystemConfig).limit(1))
        if result.scalars().first() is None:
            now = datetime.now()
            system_configs = [
                SystemConfig(
                    config_key="ai_enabled",
                    config_value="true",
                    description="AI 功能总开关",
                    updated_at=now,
                ),
                SystemConfig(
                    config_key="ai_model",
                    config_value="deepseek",
                    description="AI 模型选择（deepseek/openai/tongyi）",
                    updated_at=now,
                ),
                SystemConfig(
                    config_key="ai_api_key",
                    config_value="",
                    description="AI 模型 API Key",
                    updated_at=now,
                ),
                SystemConfig(
                    config_key="ai_base_url",
                    config_value="https://api.deepseek.com/v1",
                    description="AI 模型 API Base URL",
                    updated_at=now,
                ),
                SystemConfig(
                    config_key="buy_threshold",
                    config_value="3.5",
                    description="买入信号阈值（加权评分≥此值判定为买入）",
                    updated_at=now,
                ),
                SystemConfig(
                    config_key="sell_threshold",
                    config_value="2.0",
                    description="卖出信号阈值（加权评分≤此值判定为卖出）",
                    updated_at=now,
                ),
            ]
            session.add_all(system_configs)

        await session.commit()
