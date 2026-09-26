"""SQLAlchemy 异步引擎 + Session 工厂 + 初始化函数"""
from backend.utils.timezone import now_beijing

import json
import logging

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
    # SQLite 并发写保护：busy_timeout=30s 让并发写（后台 refresh 5 并发）
    # 排队等待而非立即报 "database is locked"（默认 5s 在高频并发下偶发失败，
    # 导致部分基金持仓/经理数据写入丢失）。timeout 由 aiosqlite 透传给
    # sqlite3.connect，等价于 PRAGMA busy_timeout=30000。
    connect_args={"timeout": 30},
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


def _build_factor_seeds() -> list[dict]:
    """11 因子默认配置 — 迁移分支与全新库分支共用

    2026-09-12 复查：原两份 150 行副本已漂移（空库副本带 data_fields、
    迁移副本没有），同参数下新库/旧库的因子行不一致。统一从此构造。
    """
    return [
    {
        "name": "短期动量", "code": "short_momentum", "data_fields": json.dumps(["nav"]), "direction": "positive",
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
        "name": "中期动量", "code": "mid_momentum", "data_fields": json.dumps(["nav"]), "direction": "positive",
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
        "name": "波动率倒数", "code": "inv_volatility", "data_fields": json.dumps(["nav"]), "direction": "positive",
        "weight": 1.0, "sort_order": 3,
        "params": json.dumps({"window": 60}),
        "formula": "1 / (std(returns, 60) * sqrt(252))",
        "window": 60, "window_unit": "day",
        "signal_rules": json.dumps([]),
        "normalization": "cross_sectional_zscore",
        "normalization_config": json.dumps({"zscore_thresholds": [1.0, 0.5, -0.5, -1.0]}),
    },
    {
        "name": "回撤修复度", "code": "drawdown_recovery", "data_fields": json.dumps(["nav"]), "direction": "positive",
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
        "name": "收益风险比", "code": "return_risk_ratio", "data_fields": json.dumps(["nav"]), "direction": "positive",
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
        "name": "动量加速度", "code": "momentum_accel", "data_fields": json.dumps(["nav"]), "direction": "positive",
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
        "name": "趋势一致性", "code": "trend_consistency", "data_fields": json.dumps(["nav"]), "direction": "positive",
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
    {
        # 双向绝对因子：独立于截面相对位置，金叉+1.0 / 死叉-1.0。
        # 关键作用：穿透普涨市，把真正走弱的基金打负，使加权分
        # 能落到 quality_filter 的 sell_threshold(-1.5) 以下，产出卖出信号。
        "name": "MACD信号", "code": "macd_signal", "data_fields": json.dumps(["nav"]), "direction": "positive",
        "weight": 0.5, "sort_order": 8,
        "params": json.dumps({"fast": 12, "slow": 26, "signal": 9}),
        "formula": "DIF=EMA(12)-EMA(26); DEA=EMA(DIF,9); 金叉+1.0/死叉-1.0",
        "window": 26, "window_unit": "day",
        "signal_rules": json.dumps([]),
        "normalization": "none",
    },
    # ── 市场环境 3 因子（读 MarketRegimeSnapshot，全池同分；绝对因子必须
    # 用 normalization=none，截面标准化会把同分因子打成 0）──
    {
        "name": "大盘估值分位", "code": "market_valuation", "data_fields": json.dumps(["market_regime"]), "direction": "negative",
        "weight": 0.8, "sort_order": 9,
        "params": json.dumps({}),
        "formula": "沪深300 PE 近5年分位（中证指数官网）",
        "window": 1215, "window_unit": "day",
        "signal_rules": json.dumps([
            {"condition": "<= 0.2", "score": 1.0},
            {"condition": "<= 0.4", "score": 0.5},
            {"condition": "<= 0.6", "score": 0.0},
            {"condition": "<= 0.8", "score": -0.5},
            {"condition": "> 0.8", "score": -1.0},
        ]),
        "normalization": "none",
    },
    {
        "name": "市场情绪", "code": "market_sentiment", "data_fields": json.dumps(["market_regime"]), "direction": "positive",
        "weight": 0.5, "sort_order": 10,
        "params": json.dumps({}),
        "formula": "全市场涨跌家数比 (up-down)/(up+down)",
        "window": 1, "window_unit": "day",
        "signal_rules": json.dumps([
            {"condition": "> 0.5", "score": 1.0},
            {"condition": "> 0.2", "score": 0.5},
            {"condition": ">= -0.2", "score": 0.0},
            {"condition": ">= -0.5", "score": -0.5},
            {"condition": "else", "score": -1.0},
        ]),
        "normalization": "none",
    },
    {
        "name": "资金面", "code": "market_fund_flow", "data_fields": json.dumps(["market_regime"]), "direction": "positive",
        "weight": 0.5, "sort_order": 11,
        "params": json.dumps({}),
        "formula": "上交所融资融券余额 7 日变化率",
        "window": 7, "window_unit": "day",
        "signal_rules": json.dumps([
            {"condition": "> 0.03", "score": 1.0},
            {"condition": "> 0.01", "score": 0.5},
            {"condition": ">= -0.01", "score": 0.0},
            {"condition": ">= -0.03", "score": -0.5},
            {"condition": "else", "score": -1.0},
        ]),
        "normalization": "none",
    },
    ]


async def init_db() -> None:
    """
    创建所有表并插入初始数据：
    - 5 个默认因子
    - 默认报告配置
    - 默认系统配置
    """
    def _migration_ok(e: Exception, what: str) -> None:
        """幂等迁移异常分类：列/索引已存在=正常跳过，其余必须可见"""
        msg = str(e).lower()
        if "already exists" in msg or "duplicate column" in msg:
            logger.debug(f"迁移跳过（已存在）: {what}")
        else:
            logger.warning(f"迁移失败: {what}: {type(e).__name__}: {e}")

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
        FundQuarterly,
        HolidayCalendar,
        UserPosition,
    )

    # 建表（先确保 WAL 模式，避免并发刷新触发 database is locked）
    # WAL 让读写互不阻塞，配合上面的 busy_timeout=30s 可彻底消除后台
    # 5 并发刷新时的 "database is locked"（曾导致部分基金持仓/经理数据
    # 写入丢失）。journal_mode 持久化在数据库文件上，设一次即可。
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.run_sync(Base.metadata.create_all)

    # ── 迁移合集 ──
    async with engine.begin() as conn:
        # equity_ratio 列
        try:
            await conn.execute(text("ALTER TABLE analysis_results ADD COLUMN equity_ratio FLOAT NOT NULL DEFAULT 0.5"))
        except Exception as e:
            _migration_ok(e, "analysis_results.equity_ratio")
        # 诊断地基四列（2026-09-23：original_score/动态阈值/质量警告落库，可空，旧行 NULL）
        for col_sql in [
            "ALTER TABLE analysis_results ADD COLUMN original_score FLOAT",
            "ALTER TABLE analysis_results ADD COLUMN dynamic_buy_threshold FLOAT",
            "ALTER TABLE analysis_results ADD COLUMN dynamic_sell_threshold FLOAT",
            "ALTER TABLE analysis_results ADD COLUMN quality_warnings TEXT",
        ]:
            try:
                await conn.execute(text(col_sql))
            except Exception as e:
                _migration_ok(e, col_sql)
        # funds 表 starred 列（星标收藏）
        try:
            await conn.execute(text("ALTER TABLE funds ADD COLUMN starred BOOLEAN NOT NULL DEFAULT 0"))
        except Exception as e:
            _migration_ok(e, "funds.starred")
        # funds 表双层标签列（2026-08-30：主标签=官方类型+基准定位，副标签=持仓暴露）
        for col_sql in [
            "ALTER TABLE funds ADD COLUMN fund_type_official VARCHAR(50)",
            "ALTER TABLE funds ADD COLUMN benchmark_text VARCHAR(300)",
            "ALTER TABLE funds ADD COLUMN exposure_tags VARCHAR(300)",
        ]:
            try:
                await conn.execute(text(col_sql))
            except Exception as e:
                _migration_ok(e, col_sql)
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
            except Exception as e:
                _migration_ok(e, col_sql)

        # AI Agent 会话工具轨迹列（2026-09-23）
        try:
            await conn.execute(text("ALTER TABLE ai_conversations ADD COLUMN agent_events TEXT"))
        except Exception as e:
            _migration_ok(e, "ai_conversations.agent_events")

        # Skill-as-tool：ai_skills 加 tool_spec 列（P4，空=保持提示词注入模式）
        try:
            await conn.execute(text("ALTER TABLE ai_skills ADD COLUMN tool_spec TEXT"))
        except Exception as e:
            _migration_ok(e, "ai_skills.tool_spec")

        # 基金经理在任快照列（2026-09-26）：旧口径把"同批写入的共同在任经理"
        # 当成前任，11 只多经理基金挂假"经理变更"。存量行回填 created_at，
        # 使历史数据按原批次分组（同批 = 共同在任），下次刷新起自动纠正。
        try:
            await conn.execute(text(
                "ALTER TABLE fund_manager_records ADD COLUMN last_seen_at DATETIME"
            ))
            await conn.execute(text(
                "UPDATE fund_manager_records SET last_seen_at = created_at "
                "WHERE last_seen_at IS NULL"
            ))
        except Exception as e:
            _migration_ok(e, "fund_manager_records.last_seen_at")

        # uq_fund_date 唯一约束回填（旧库 create_all 不会补约束；并发分析曾可插重复行）
        # 先清理历史重复（保留每组最新一条），再建唯一索引
        try:
            await conn.execute(text(
                "DELETE FROM analysis_results WHERE id NOT IN "
                "(SELECT MAX(id) FROM analysis_results GROUP BY fund_id, analysis_date)"
            ))
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_fund_date ON analysis_results (fund_id, analysis_date)"
            ))
        except Exception as e:
            _migration_ok(e, "uq_fund_date 回填/唯一索引")

        # analysis_results 索引（按日期查询、按基金查历史时加速）
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS ix_analysis_results_analysis_date ON analysis_results (analysis_date)",
            "CREATE INDEX IF NOT EXISTS ix_analysis_results_fund_id ON analysis_results (fund_id)",
        ]:
            try:
                await conn.execute(text(idx_sql))
            except Exception as e:
                _migration_ok(e, idx_sql)

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
            now = now_beijing()

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
                "price_percentile", "fed_model", "momentum_6m",
                "info_ratio", "max_drawdown", "size_stability",
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
            new_factors_config = _build_factor_seeds()
            # 一次性重激活标记：此前版本的重激活迁移执行过即不再执行，
            # 之后用户手动停用的因子不会被重启覆盖
            _factor_reactivation_done = (
                await session.execute(
                    select(SystemConfig).where(
                        SystemConfig.config_key == "factor_reactivation_v1"
                    )
                )
            ).scalars().first() is not None
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
                elif existing.status != "active" and not _factor_reactivation_done:
                    # 一次性迁移：曾被旧版禁用的关键因子（如 macd_signal）重新激活。
                    # 2026-08-29 起加标记位——否则用户在因子管理页手动停用的因子
                    # 会在每次重启后被静默重新激活并重置权重（覆盖用户意图）。
                    existing.status = "active"
                    existing.weight = cfg["weight"]
                    existing.updated_at = now
                    logger.info(f"已重新激活因子: {cfg['name']} ({cfg['code']})")


            if not _factor_reactivation_done:
                session.add(SystemConfig(
                    config_key="factor_reactivation_v1",
                    config_value="done",
                    description="内置因子一次性重激活迁移标记（2026-08-29）",
                    updated_at=now_beijing(),
                ))



            await session.commit()

    # ── 原生 Skill 补种（组合 X 光 / 调仓自进化，2026-09-24）──
    # 无条件执行（空库/已有库都补；按 name 检查，不覆盖用户改动）
    async with async_session_factory() as session:
        from backend.models.ai_skill import AISkill
        now = now_beijing()
        # ── 原生 Skill 补种（组合 X 光 / 调仓自进化，2026-09-24）──
        # 无条件执行（空库/已有库都补，按 name 检查不覆盖用户改动）
        # ── 原生 Skill 补种（组合 X 光 / 调仓自进化，2026-09-24）──
        _native_skills = [
            {
                "name": "组合X光透视",
                "description": "持仓穿透与集中度分析：个股HHI/两两Jaccard重叠/经理公司单点依赖/多样化评分",
                "system_prompt": (
                    "你是组合结构诊断专家。用户会给出组合X光数据（HHI、两两重叠、经理/公司集中度、"
                    "多样化评分），请按以下框架解读：\n"
                    "1. 评分与等级总评（80+优/60+良/40+中/其余差）\n"
                    "2. 穿透视角：HHI 与等效持股数说明真分散程度，指出组合层第一大重仓及其占比\n"
                    "3. 伪分散识别：两两重叠 ≥5 只的基金对点名，说明'两只基金实为同一暴露'\n"
                    "4. 单点依赖：同一经理 ≥35% 仓位、同一公司 ≥60% 基金的风险\n"
                    "5. 给出 1~3 条具体调仓建议（引用数据）\n"
                    "注意：评分是可解释扣分制，逐项引用扣分原因；所有建议仅供参考。"
                ),
                "enabled": True,
                "tool_spec": json.dumps({
                    "description": "对基金池或指定基金做组合X光透视（穿透HHI/重叠矩阵/集中度/评分）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "fund_ids": {"type": "string", "description": "逗号分隔基金ID，空=全部活跃"}
                        },
                    },
                }),
            },
            {
                "name": "调仓建议自进化",
                "description": "调仓建议命中率统计与阈值校准状态查询（建议落库→30天回填→自动校准）",
                "system_prompt": (
                    "你是建议质量复盘助手。用户会给出调仓建议的历史命中率统计"
                    "（sell/buy 各自总数与命中数）与当前校准参数（止盈/止损线）。\n"
                    "解读框架：\n"
                    "1. 样本量说明（<5 条时明确'样本不足，暂不下结论'）\n"
                    "2. sell 命中率低 = 卖出信号过于敏感，止损线应向保守收紧\n"
                    "3. buy 命中率低 = 买入信号追高，止盈线应下调\n"
                    "4. 引用具体数字，不做无数据支撑的断言；所有内容仅供参考。"
                ),
                "enabled": True,
                "tool_spec": json.dumps({
                    "description": "查询调仓建议的历史命中率与阈值校准状态",
                    "parameters": {"type": "object", "properties": {}},
                }),
            },
        ]
        for sk in _native_skills:
            exists = await session.execute(
                select(AISkill).where(AISkill.name == sk["name"])
            )
            if exists.scalars().first() is None:
                session.add(AISkill(
                    name=sk["name"], description=sk["description"],
                    system_prompt=sk["system_prompt"], enabled=sk["enabled"],
                    tool_spec=sk["tool_spec"], created_at=now, updated_at=now,
                ))
                logger.info(f"已补种原生 Skill: {sk['name']}")
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
                changed = False
                if isinstance(data, list) and len(data) > 5:
                    # 去重：只保留前 5 档（或唯一次序），末档 min_score 改为钳位下界 -8.5
                    seen_strengths = set()
                    deduped = []
                    for t in data:
                        sig = t.get("signal_strength")
                        if sig not in seen_strengths:
                            seen_strengths.add(sig)
                            deduped.append(t)
                    # 确保末档 min_score = -8.5
                    if deduped:
                        deduped[-1]["min_score"] = -8.5
                    data = deduped
                    changed = True
                    logger.info(f"已修复评分阈值：去重 {len(json.loads(config.config_value))}→{len(deduped)} 档")
                # 存量 -6.4 末档归一为 -8.5（catch-all 对齐钳位下界，行为不变、口径更清晰）
                if isinstance(data, list) and data and data[-1].get("signal_strength") == "heavy_sell" \
                        and data[-1].get("min_score") == -6.4:
                    data[-1]["min_score"] = -8.5
                    changed = True
                    logger.info("评分阈值迁移：末档 heavy_sell min_score -6.4 → -8.5")
                if changed:
                    config.config_value = json.dumps(data, ensure_ascii=False)
                    config.updated_at = now_beijing()
                    await session.commit()
            except Exception as e:
                logger.warning(f"评分阈值修复失败: {e}")

    # ── 调休日历同步配置默认值（已有库增量添加，空库跳过）──
    async with async_session_factory() as session:
        from sqlalchemy import select

        holiday_defaults = [
            ("holiday_sync_url", "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json",
             "调休/节假日同步数据源地址，{year} 占位符替换为年份（默认 NateScarlet/holiday-cn，溯源 gov.cn 国务院放假安排）"),
            ("holiday_auto_sync_time", "03:00",
             "调休自动同步时间 HH:MM，每日该时刻检查一次，同步成功后自动停用"),
            ("holiday_auto_sync_enabled", "true",
             "调休自动同步开关，同步成功后自动置 false（只同步一次），后期可在后台手动开启"),
            ("holiday_last_sync_at", "",
             "最近一次成功同步时间（ISO）"),
        ]
        for key, val, desc in holiday_defaults:
            exists = (await session.execute(
                select(SystemConfig).where(SystemConfig.config_key == key)
            )).scalars().first()
            if not exists:
                session.add(SystemConfig(
                    config_key=key, config_value=val, description=desc, updated_at=now_beijing()
                ))
        await session.commit()

    # 插入初始数据（空库时）
    async with async_session_factory() as session:
        # ── 检查是否已有因子数据 ──
        from sqlalchemy import select
        result = await session.execute(select(Factor).limit(1))
        if result.scalars().first() is None:
            now = now_beijing()
            factors = [
                Factor(
                    name=c["name"], code=c["code"], direction=c["direction"],
                    weight=c["weight"], sort_order=c["sort_order"],
                    params=c.get("params"), formula=c.get("formula"),
                    window=c.get("window"), window_unit=c.get("window_unit"),
                    signal_rules=c.get("signal_rules"),
                    normalization=c.get("normalization", "none"),
                    normalization_config=c.get("normalization_config"),
                    status="active", created_at=now, updated_at=now,
                )
                for c in _build_factor_seeds()
            ]
            session.add_all(factors)

        # ── 检查并补充报告配置 ──
        now = now_beijing()
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
                    name="TOP10 买卖信号",
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
                ReportConfig(
                    name="前十大持仓涨跌",
                    item_key="top10_change",
                    enabled=True,
                    sort_order=15,
                    created_at=now,
                ),
                ReportConfig(
                    name="基金当日涨跌",
                    item_key="fund_daily_change",
                    enabled=True,
                    sort_order=16,
                    created_at=now,
                ),
                ReportConfig(
                    name="基金实时涨跌TOP10",
                    item_key="fund_realtime_top10",
                    enabled=True,
                    sort_order=17,
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
            now = now_beijing()
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
                    config_key="scoring_thresholds",
                    config_value=json.dumps([
                        {"min_score": 3.0, "label": "强烈加仓", "signal_direction": "buy", "signal_strength": "heavy_buy", "operation_advice": "综合评分 {score}，强烈建议加仓，权益仓位可升至 {equity_pct}%", "equity_ratio": 0.9},
                        {"min_score": 1.5, "label": "适度加仓", "signal_direction": "buy", "signal_strength": "moderate_buy", "operation_advice": "综合评分 {score}，建议适度加仓，权益仓位可升至 {equity_pct}%", "equity_ratio": 0.7},
                        {"min_score": -1.5, "label": "中性/观望", "signal_direction": "hold", "signal_strength": "hold", "operation_advice": "综合评分 {score}，建议持有观望，维持基准仓位 {equity_pct}%", "equity_ratio": 0.5},
                        {"min_score": -3.0, "label": "适度减仓", "signal_direction": "sell", "signal_strength": "moderate_sell", "operation_advice": "综合评分 {score}，建议适度减仓，权益仓位降至 {equity_pct}%", "equity_ratio": 0.3},
                        {"min_score": -8.5, "label": "强烈减仓", "signal_direction": "sell", "signal_strength": "heavy_sell", "operation_advice": "综合评分 {score}，强烈建议减仓或清仓，权益仓位降至 {equity_pct}%", "equity_ratio": 0.1},
                    ], ensure_ascii=False),
                    description="评分阈值配置（五档对称）",
                    updated_at=now,
                ),
            ]
            session.add_all(system_configs)

        await session.commit()
