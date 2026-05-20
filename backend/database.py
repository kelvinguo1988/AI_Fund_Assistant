"""SQLAlchemy 异步引擎 + Session 工厂 + 初始化函数"""

import json
import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings

logger = logging.getLogger(__name__)


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

    # ── 迁移合集 ──
    async with engine.begin() as conn:
        # equity_ratio 列
        try:
            await conn.execute(text("ALTER TABLE analysis_results ADD COLUMN equity_ratio FLOAT NOT NULL DEFAULT 0.5"))
        except Exception:
            pass
        # factor 表新列
        for col_sql in [
            "ALTER TABLE factors ADD COLUMN data_fields TEXT",
            "ALTER TABLE factors ADD COLUMN formula TEXT",
            "ALTER TABLE factors ADD COLUMN window INTEGER",
            "ALTER TABLE factors ADD COLUMN window_unit VARCHAR(10)",
            "ALTER TABLE factors ADD COLUMN signal_rules TEXT",
            "ALTER TABLE factors ADD COLUMN normalization VARCHAR(30) NOT NULL DEFAULT 'none'",
            "ALTER TABLE factors ADD COLUMN normalization_config TEXT",
        ]:
            try:
                await conn.execute(text(col_sql))
            except Exception:
                pass

    # ── 修复已有因子记录的标准化配置 ──
    async with async_session_factory() as session:
        from sqlalchemy import select, update
        # 对数据库中可能因 ALTER TABLE 默认值 'none' 导致截面标准化不生效的因子做修正
        fix_normalization = {
            "inv_volatility": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
            "info_ratio": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
            "max_drawdown": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
            "size_stability": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
        }
        for code, norm_conf in fix_normalization.items():
            result = await session.execute(
                select(Factor).where(Factor.code == code, Factor.normalization == "none")
            )
            stale = result.scalars().first()
            if stale:
                stale.normalization = "cross_sectional_zscore"
                stale.normalization_config = norm_conf
                stale.signal_rules = json.dumps([]) if stale.signal_rules is None else stale.signal_rules
        await session.commit()

    # ── 因子表迁移：旧→新 8 因子体系（仅对已有数据库执行，空库跳过）──
    async with async_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(select(Factor).limit(1))
        if result.scalars().first() is None:
            logger.info("空数据库，跳过因子迁移")
        else:
            now = datetime.now()

            # 1. 禁用旧因子（roe_stability → info_ratio; volume_price → max_drawdown）
            for old_code in ("roe_stability", "volume_price"):
                result = await session.execute(select(Factor).where(Factor.code == old_code, Factor.status == "active"))
                old_factor = result.scalars().first()
                if old_factor:
                    old_factor.status = "disabled"
                    old_factor.updated_at = now
                    logger.info(f"已禁用旧因子: {old_code}")

            # 2. 更新 macd_signal 权重 0.6 → 0.5
            result = await session.execute(select(Factor).where(Factor.code == "macd_signal"))
            macd = result.scalars().first()
            if macd and abs(macd.weight - 0.6) < 0.01:
                macd.weight = 0.5
                macd.updated_at = now
                logger.info("已更新 MACD 信号权重: 0.6 → 0.5")

            # 3. 添加新因子（如尚不存在）
            new_factors_config = [
                {
                    "name": "信息比率", "code": "info_ratio", "data_field": "info_ratio",
                    "data_fields": json.dumps(["nav", "benchmark_nav"]),
                    "weight": 0.8, "direction": "positive",
                    "params": json.dumps({"window": 252}),
                    "formula": "annualize(excess_returns_mean, 252) / (std(excess_returns, 252) * sqrt(252))",
                    "window": 252, "window_unit": "day", "sort_order": 5,
                    "signal_rules": json.dumps([]),
                    "normalization": "cross_sectional_zscore",
                    "normalization_config": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                },
                {
                    "name": "最大回撤", "code": "max_drawdown", "data_field": "max_drawdown",
                    "data_fields": json.dumps(["nav"]),
                    "weight": 0.5, "direction": "positive",
                    "params": json.dumps({"window": 252}),
                    "formula": "max_drawdown(nav, 252)",
                    "window": 252, "window_unit": "day", "sort_order": 7,
                    "signal_rules": json.dumps([]),
                    "normalization": "cross_sectional_zscore",
                    "normalization_config": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                },
                {
                    "name": "规模稳定性", "code": "size_stability", "data_field": "fund_size",
                    "data_fields": json.dumps(["fund_size_quarterly"]),
                    "weight": 0.4, "direction": "positive",
                    "params": json.dumps({"window": 4}),
                    "formula": "1 / (std(size, 4) / mean(size, 4)) + size_bonus(size)",
                    "window": 4, "window_unit": "quarter", "sort_order": 8,
                    "signal_rules": json.dumps([]),
                    "normalization": "cross_sectional_zscore",
                    "normalization_config": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                },
            ]
            for cfg in new_factors_config:
                result = await session.execute(select(Factor).where(Factor.code == cfg["code"]))
                existing = result.scalars().first()
                if not existing:
                    session.add(Factor(
                        name=cfg["name"], code=cfg["code"],
                        data_field=cfg["data_field"], data_fields=cfg["data_fields"],
                        weight=cfg["weight"], direction=cfg["direction"],
                        params=cfg["params"], formula=cfg["formula"],
                        window=cfg["window"], window_unit=cfg["window_unit"],
                        signal_rules=cfg["signal_rules"],
                        normalization=cfg["normalization"],
                        normalization_config=cfg.get("normalization_config"),
                        status="active", sort_order=cfg["sort_order"],
                        created_at=now, updated_at=now,
                    ))
                    logger.info(f"已添加新因子: {cfg['name']} ({cfg['code']})")

            await session.commit()

    # 插入初始数据（空库时）
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
                    data_fields=json.dumps(["pe_ttm"]),
                    weight=1.2,
                    direction="negative",
                    params=json.dumps({"window": 1250}),
                    formula="percentile_rank(pe_ttm, 1250)",
                    window=1250,
                    window_unit="day",
                    signal_rules=json.dumps([
                        {"condition": "<= 0.2", "score": 1.0},
                        {"condition": "<= 0.4", "score": 0.5},
                        {"condition": "<= 0.6", "score": 0.0},
                        {"condition": "<= 0.8", "score": -0.5},
                        {"condition": "> 0.8", "score": -1.0},
                    ]),
                    normalization="none",
                    status="active",
                    sort_order=1,
                    created_at=now,
                    updated_at=now,
                ),
                Factor(
                    name="股债性价比FED",
                    code="fed_model",
                    data_field="fed",
                    data_fields=json.dumps(["index_pe", "bond_yield_10y"]),
                    weight=1.2,
                    direction="positive",
                    params=json.dumps({"window": 756}),
                    formula="(1 / index_pe) - bond_yield_10y",
                    window=756,
                    window_unit="day",
                    signal_rules=json.dumps([
                        {"condition": "> percentile(756, 0.8)", "score": 1.0},
                        {"condition": "> percentile(756, 0.6)", "score": 0.5},
                        {"condition": "< percentile(756, 0.4)", "score": -0.5},
                        {"condition": "< percentile(756, 0.2)", "score": -1.0},
                    ]),
                    normalization="rolling_percentile",
                    status="active",
                    sort_order=2,
                    created_at=now,
                    updated_at=now,
                ),
                Factor(
                    name="动量因子",
                    code="momentum_6m",
                    data_field="nav",
                    data_fields=json.dumps(["nav"]),
                    weight=1.0,
                    direction="positive",
                    params=json.dumps({"window": 126}),
                    formula="(nav / shift(nav, 126) - 1) / (std(returns, 126) * sqrt(126))",
                    window=126,
                    window_unit="day",
                    signal_rules=json.dumps([
                        {"condition": "> 1.0", "score": 1.0},
                        {"condition": "> 0.5", "score": 0.5},
                        {"condition": ">= -0.5 and <= 0.5", "score": 0.0},
                        {"condition": ">= -1.0 and < -0.5", "score": -0.5},
                        {"condition": "< -1.0", "score": -1.0},
                    ]),
                    normalization="none",
                    status="active",
                    sort_order=3,
                    created_at=now,
                    updated_at=now,
                ),
                Factor(
                    name="波动率倒数",
                    code="inv_volatility",
                    data_field="nav",
                    data_fields=json.dumps(["nav"]),
                    weight=0.8,
                    direction="positive",
                    params=json.dumps({"window": 60}),
                    formula="1 / std(returns, 60)",
                    window=60,
                    window_unit="day",
                    signal_rules=json.dumps([]),
                    normalization="cross_sectional_zscore",
                    normalization_config=json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                    status="active",
                    sort_order=4,
                    created_at=now,
                    updated_at=now,
                ),
                Factor(
                    name="信息比率",
                    code="info_ratio",
                    data_field="info_ratio",
                    data_fields=json.dumps(["nav", "benchmark_nav"]),
                    weight=0.8,
                    direction="positive",
                    params=json.dumps({"window": 252}),
                    formula="annualize(excess_returns_mean, 252) / (std(excess_returns, 252) * sqrt(252))",
                    window=252,
                    window_unit="day",
                    signal_rules=json.dumps([]),
                    normalization="cross_sectional_zscore",
                    normalization_config=json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                    status="active",
                    sort_order=5,
                    created_at=now,
                    updated_at=now,
                ),
                Factor(
                    name="MACD信号",
                    code="macd_signal",
                    data_field="macd",
                    data_fields=json.dumps(["nav"]),
                    weight=0.5,
                    direction="positive",
                    params=json.dumps({"fast": 12, "slow": 26, "signal": 9}),
                    formula="ema(12) - ema(26)",
                    window=26,
                    window_unit="day",
                    signal_rules=json.dumps([
                        {"condition": "dif > dea and macd_hist_delta > 0", "score": 1.0},
                        {"condition": "dif > dea and macd_hist_delta <= 0", "score": 0.5},
                        {"condition": "dif < dea and macd_hist_delta < 0", "score": -1.0},
                        {"condition": "else", "score": 0.0},
                    ]),
                    normalization="none",
                    status="active",
                    sort_order=6,
                    created_at=now,
                    updated_at=now,
                ),
                Factor(
                    name="最大回撤",
                    code="max_drawdown",
                    data_field="max_drawdown",
                    data_fields=json.dumps(["nav"]),
                    weight=0.5,
                    direction="positive",
                    params=json.dumps({"window": 252}),
                    formula="max_drawdown(nav, 252)",
                    window=252,
                    window_unit="day",
                    signal_rules=json.dumps([]),
                    normalization="cross_sectional_zscore",
                    normalization_config=json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                    status="active",
                    sort_order=7,
                    created_at=now,
                    updated_at=now,
                ),
                Factor(
                    name="规模稳定性",
                    code="size_stability",
                    data_field="fund_size",
                    data_fields=json.dumps(["fund_size_quarterly"]),
                    weight=0.4,
                    direction="positive",
                    params=json.dumps({"window": 4}),
                    formula="1 / (std(size, 4) / mean(size, 4)) + size_bonus(size)",
                    window=4,
                    window_unit="quarter",
                    signal_rules=json.dumps([]),
                    normalization="cross_sectional_zscore",
                    normalization_config=json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                    status="active",
                    sort_order=8,
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
