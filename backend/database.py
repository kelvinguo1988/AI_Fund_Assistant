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

            # 2. 禁用引擎内置因子（如尚存在），已由用户自定义 7 因子替代
            engine_old_codes = [
                "pe_percentile", "fed_model", "momentum_6m",
                "info_ratio", "macd_signal", "max_drawdown", "size_stability",
            ]
            for old_code in engine_old_codes:
                result = await session.execute(
                    select(Factor).where(Factor.code == old_code, Factor.status == "active")
                )
                old_factor = result.scalars().first()
                if old_factor:
                    old_factor.status = "disabled"
                    old_factor.updated_at = now
                    logger.info(f"已禁用引擎旧因子: {old_code}")

            # 3. 添加用户自定义 7 因子（如尚不存在）
            new_factors_config = [
                {
                    "name": "短期动量", "code": "short_momentum", "direction": "positive",
                    "weight": 1.2, "sort_order": 1,
                    "params": json.dumps({"window": 20}),
                    "formula": "nav / shift(nav, 20) - 1",
                    "window": 20, "window_unit": "day",
                    "signal_rules": json.dumps([
                        {"condition": "> 0.01", "score": 1.0},
                        {"condition": "< -0.01", "score": -1.0},
                        {"condition": "else", "score": 0.0},
                    ]),
                    "normalization": "cross_sectional_zscore",
                    "normalization_config": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                },
                {
                    "name": "中期动量", "code": "mid_momentum", "direction": "positive",
                    "weight": 1.2, "sort_order": 2,
                    "params": json.dumps({"window": 60}),
                    "formula": "nav / shift(nav, 60) - 1",
                    "window": 60, "window_unit": "day",
                    "signal_rules": json.dumps([
                        {"condition": "> 0", "score": 1.0},
                        {"condition": "< 0", "score": -1.0},
                        {"condition": "else", "score": 0.0},
                    ]),
                    "normalization": "cross_sectional_zscore",
                    "normalization_config": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                },
                {
                    "name": "波动率倒数", "code": "inv_volatility", "direction": "positive",
                    "weight": 1.0, "sort_order": 3,
                    "params": json.dumps({"window": 60}),
                    "formula": "1 / (std(returns, 60) * sqrt(252))",
                    "window": 60, "window_unit": "day",
                    "signal_rules": json.dumps([]),
                    "normalization": "cross_sectional_zscore",
                    "normalization_config": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                },
                {
                    "name": "回撤修复度", "code": "drawdown_recovery", "direction": "positive",
                    "weight": 0.8, "sort_order": 4,
                    "params": json.dumps({"window": 252}),
                    "formula": "nav / rolling_max(nav, 252)",
                    "window": 252, "window_unit": "day",
                    "signal_rules": json.dumps([
                        {"condition": "> 0.95", "score": 1.0},
                        {"condition": ">= 0.85", "score": 0.0},
                        {"condition": "< 0.85", "score": -1.0},
                    ]),
                    "normalization": "none",
                },
                {
                    "name": "收益风险比", "code": "return_risk_ratio", "direction": "positive",
                    "weight": 0.8, "sort_order": 5,
                    "params": json.dumps({"window": 60, "epsilon": 0.0001}),
                    "formula": "mean(returns, 60) / (std(returns, 60) + 0.0001)",
                    "window": 60, "window_unit": "day",
                    "signal_rules": json.dumps([
                        {"condition": "> 0.5", "score": 1.0},
                        {"condition": "< -0.5", "score": -1.0},
                        {"condition": "else", "score": 0.0},
                    ]),
                    "normalization": "cross_sectional_zscore",
                    "normalization_config": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                },
                {
                    "name": "动量加速度", "code": "momentum_accel", "direction": "positive",
                    "weight": 0.5, "sort_order": 6,
                    "params": json.dumps({"short_window": 20, "mid_window": 60}),
                    "formula": "mom20 - mom60",
                    "window": 60, "window_unit": "day",
                    "signal_rules": json.dumps([
                        {"condition": "> 0", "score": 1.0},
                        {"condition": "< 0", "score": -1.0},
                        {"condition": "else", "score": 0.0},
                    ]),
                    "normalization": "cross_sectional_zscore",
                    "normalization_config": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                },
                {
                    "name": "趋势一致性", "code": "trend_consistency", "direction": "positive",
                    "weight": 0.5, "sort_order": 7,
                    "params": json.dumps({"short_window": 20, "mid_window": 60}),
                    "formula": "mean([sign(mom20), sign(mom60)])",
                    "window": 60, "window_unit": "day",
                    "signal_rules": json.dumps([
                        {"condition": "> 0", "score": 1.0},
                        {"condition": "< 0", "score": -1.0},
                        {"condition": "else", "score": 0.0},
                    ]),
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
                        data_field=cfg.get("data_field"), data_fields=cfg.get("data_fields"),
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

    # ── 修复评分阈值配置中的重复 heavy_sell 档位（旧版 -100 catch-all）──
    async with async_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(SystemConfig).where(SystemConfig.config_key == "scoring_thresholds")
        )
        config = result.scalars().first()
        if config:
            try:
                data = json.loads(config.config_value)
                if isinstance(data, list) and len(data) > 5:
                    # 去重：只保留前 5 档（或唯一次序），末档 min_score 改为 -6.4
                    seen_strengths = set()
                    deduped = []
                    for t in data:
                        sig = t.get("signal_strength")
                        if sig not in seen_strengths:
                            seen_strengths.add(sig)
                            deduped.append(t)
                    # 确保末档 min_score = -6.4
                    if deduped:
                        deduped[-1]["min_score"] = -6.4
                    config.config_value = json.dumps(deduped, ensure_ascii=False)
                    config.updated_at = datetime.now()
                    await session.commit()
                    logger.info(f"已修复评分阈值：去重 {len(data)}→{len(deduped)} 档，末档 min_score=-6.4")
            except Exception as e:
                logger.warning(f"评分阈值修复失败: {e}")

    # 插入初始数据（空库时）
    async with async_session_factory() as session:
        # ── 检查是否已有因子数据 ──
        from sqlalchemy import select
        result = await session.execute(select(Factor).limit(1))
        if result.scalars().first() is None:
            now = datetime.now()
            factors = [
                Factor(
                    name="短期动量", code="short_momentum", direction="positive",
                    data_fields=json.dumps(["nav"]),
                    weight=1.2, sort_order=1,
                    params=json.dumps({"window": 20}),
                    formula="nav / shift(nav, 20) - 1",
                    window=20, window_unit="day",
                    signal_rules=json.dumps([
                        {"condition": "> 0.01", "score": 1.0},
                        {"condition": "< -0.01", "score": -1.0},
                        {"condition": "else", "score": 0.0},
                    ]),
                    normalization="cross_sectional_zscore",
                    normalization_config=json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                    status="active", created_at=now, updated_at=now,
                ),
                Factor(
                    name="中期动量", code="mid_momentum", direction="positive",
                    data_fields=json.dumps(["nav"]),
                    weight=1.2, sort_order=2,
                    params=json.dumps({"window": 60}),
                    formula="nav / shift(nav, 60) - 1",
                    window=60, window_unit="day",
                    signal_rules=json.dumps([
                        {"condition": "> 0", "score": 1.0},
                        {"condition": "< 0", "score": -1.0},
                        {"condition": "else", "score": 0.0},
                    ]),
                    normalization="cross_sectional_zscore",
                    normalization_config=json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                    status="active", created_at=now, updated_at=now,
                ),
                Factor(
                    name="波动率倒数", code="inv_volatility", direction="positive",
                    data_fields=json.dumps(["nav"]),
                    weight=1.0, sort_order=3,
                    params=json.dumps({"window": 60}),
                    formula="1 / (std(returns, 60) * sqrt(252))",
                    window=60, window_unit="day",
                    signal_rules=json.dumps([]),
                    normalization="cross_sectional_zscore",
                    normalization_config=json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                    status="active", created_at=now, updated_at=now,
                ),
                Factor(
                    name="回撤修复度", code="drawdown_recovery", direction="positive",
                    data_fields=json.dumps(["nav"]),
                    weight=0.8, sort_order=4,
                    params=json.dumps({"window": 252}),
                    formula="nav / rolling_max(nav, 252)",
                    window=252, window_unit="day",
                    signal_rules=json.dumps([
                        {"condition": "> 0.95", "score": 1.0},
                        {"condition": ">= 0.85", "score": 0.0},
                        {"condition": "< 0.85", "score": -1.0},
                    ]),
                    normalization="none",
                    status="active", created_at=now, updated_at=now,
                ),
                Factor(
                    name="收益风险比", code="return_risk_ratio", direction="positive",
                    data_fields=json.dumps(["nav"]),
                    weight=0.8, sort_order=5,
                    params=json.dumps({"window": 60, "epsilon": 0.0001}),
                    formula="mean(returns, 60) / (std(returns, 60) + 0.0001)",
                    window=60, window_unit="day",
                    signal_rules=json.dumps([
                        {"condition": "> 0.5", "score": 1.0},
                        {"condition": "< -0.5", "score": -1.0},
                        {"condition": "else", "score": 0.0},
                    ]),
                    normalization="cross_sectional_zscore",
                    normalization_config=json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                    status="active", created_at=now, updated_at=now,
                ),
                Factor(
                    name="动量加速度", code="momentum_accel", direction="positive",
                    data_fields=json.dumps(["nav"]),
                    weight=0.5, sort_order=6,
                    params=json.dumps({"short_window": 20, "mid_window": 60}),
                    formula="mom20 - mom60",
                    window=60, window_unit="day",
                    signal_rules=json.dumps([
                        {"condition": "> 0", "score": 1.0},
                        {"condition": "< 0", "score": -1.0},
                        {"condition": "else", "score": 0.0},
                    ]),
                    normalization="cross_sectional_zscore",
                    normalization_config=json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                    status="active", created_at=now, updated_at=now,
                ),
                Factor(
                    name="趋势一致性", code="trend_consistency", direction="positive",
                    data_fields=json.dumps(["nav"]),
                    weight=0.5, sort_order=7,
                    params=json.dumps({"short_window": 20, "mid_window": 60}),
                    formula="mean([sign(mom20), sign(mom60)])",
                    window=60, window_unit="day",
                    signal_rules=json.dumps([
                        {"condition": "> 0", "score": 1.0},
                        {"condition": "< 0", "score": -1.0},
                        {"condition": "else", "score": 0.0},
                    ]),
                    normalization="cross_sectional_zscore",
                    normalization_config=json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
                    status="active", created_at=now, updated_at=now,
                ),
            ]
            session.add_all(factors)

        # ── 检查并补充报告配置 ──
        now = datetime.now()
        default_report_configs = [
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
                # 市场概况项
                ReportConfig(
                    name="信号概览",
                    item_key="signal_summary",
                    enabled=True,
                    sort_order=6,
                    created_at=now,
                ),
                ReportConfig(
                    name="TOP5 买卖信号",
                    item_key="top_buy_sell",
                    enabled=True,
                    sort_order=7,
                    created_at=now,
                ),
                ReportConfig(
                    name="涨跌分布",
                    item_key="adv_decline",
                    enabled=True,
                    sort_order=8,
                    created_at=now,
                ),
                ReportConfig(
                    name="两市成交额",
                    item_key="turnover",
                    enabled=True,
                    sort_order=9,
                    created_at=now,
                ),
                ReportConfig(
                    name="大盘资金流",
                    item_key="market_flow",
                    enabled=True,
                    sort_order=10,
                    created_at=now,
                ),
                ReportConfig(
                    name="沪深港通资金流",
                    item_key="hsgt_flow",
                    enabled=True,
                    sort_order=11,
                    created_at=now,
                ),
                ReportConfig(
                    name="板块资金流(当日)",
                    item_key="sector_flow_day",
                    enabled=True,
                    sort_order=12,
                    created_at=now,
                ),
                ReportConfig(
                    name="板块资金流(周)",
                    item_key="sector_flow_week",
                    enabled=True,
                    sort_order=13,
                    created_at=now,
                ),
                ReportConfig(
                    name="板块资金流(月)",
                    item_key="sector_flow_month",
                    enabled=True,
                    sort_order=14,
                    created_at=now,
                ),
        ]
        # 逐条检查缺失的配置项，避免覆盖已有数据
        for cfg in default_report_configs:
            exists = await session.execute(
                select(ReportConfig).where(ReportConfig.item_key == cfg.item_key)
            )
            if exists.scalars().first() is None:
                session.add(cfg)

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
                SystemConfig(
                    config_key="scoring_thresholds",
                    config_value=json.dumps([
                        {"min_score": 3.0, "label": "强烈加仓", "signal_direction": "buy", "signal_strength": "heavy_buy", "operation_advice": "综合评分 {score}，强烈建议加仓，权益仓位可升至 {equity_pct}%", "equity_ratio": 0.9},
                        {"min_score": 1.5, "label": "适度加仓", "signal_direction": "buy", "signal_strength": "moderate_buy", "operation_advice": "综合评分 {score}，建议适度加仓，权益仓位可升至 {equity_pct}%", "equity_ratio": 0.7},
                        {"min_score": -1.5, "label": "中性/观望", "signal_direction": "hold", "signal_strength": "hold", "operation_advice": "综合评分 {score}，建议持有观望，维持基准仓位 {equity_pct}%", "equity_ratio": 0.5},
                        {"min_score": -3.0, "label": "适度减仓", "signal_direction": "sell", "signal_strength": "moderate_sell", "operation_advice": "综合评分 {score}，建议适度减仓，权益仓位降至 {equity_pct}%", "equity_ratio": 0.3},
                        {"min_score": -6.4, "label": "强烈减仓", "signal_direction": "sell", "signal_strength": "heavy_sell", "operation_advice": "综合评分 {score}，强烈建议减仓或清仓，权益仓位降至 {equity_pct}%", "equity_ratio": 0.1},
                    ], ensure_ascii=False),
                    description="评分阈值配置（五档对称）",
                    updated_at=now,
                ),
            ]
            session.add_all(system_configs)

        await session.commit()
