# AI Fund Assistant — 基金量化交易系统

> FastAPI + React 基金量化分析平台。8 因子配置体系、双层评分、Web 管理、流式 SSE 推送、自动化信号推送、数据源连通性检测、AI 多模型配置、信号回测评分。

---

## 系统架构

```
AI_Fund_Assistant/
├── backend/                    # FastAPI 异步后端
│   ├── main.py                 # 应用入口 + 生命周期管理 + 路由挂载
│   ├── config.py               # .env → Settings
│   ├── database.py             # SQLAlchemy 异步引擎 + 迁移
│   ├── models/                 # ORM 模型
│   ├── schemas/                # Pydantic Schema
│   ├── routers/                # API 路由（8 个模块）
│   ├── services/               # 业务逻辑层（含连通性检测、缓存、变更检测）
│   ├── engines/                # 因子引擎 + 评分引擎 + 报告引擎
│   │   ├── factor_engine.py    # 8 因子计算 + 信号规则 + 截面标准化
│   │   ├── scoring_engine.py   # 加权评分 + 信号判定
│   │   └── report_engine.py    # 报告生成（Markdown / HTML）
│   ├── data_sources/           # 多数据源适配器（AKShare/JoinQuant）
│   ├── llm/                    # AI 大模型接入（DeepSeek/智谱GLM/通义千问/OpenAI）
│   ├── patch/                  # 东方财富反爬虫补丁
│   ├── push/                   # 推送机器人（飞书等）
│   ├── scheduler/              # 定时任务调度（APScheduler）
│   └── utils/                  # 通用工具（并发控制：线程池隔离 + 超时保护 + 信号量限流）
├── frontend/                   # React + TypeScript + MUI + Tailwind
│   ├── src/pages/              # 11 个管理页面（含信号回测）
│   ├── src/components/         # 图表组件（ECharts）、AI 对话、信号指示器等
│   ├── src/api/                # API 客户端
│   ├── src/hooks/              # 自定义 Hooks（AI 对话、分析）
│   └── nginx.conf              # Nginx（API 反向代理 + SPA）
├── android/                    # Android 原生客户端（Kotlin/Compose，独立构建，不参与 Docker 部署；服务器地址在 App 内配置）
├── docker-compose.yml          # 一键部署（生产 + 开发模式）
├── .env.example
└── backend/requirements.txt
```

---

## 核心功能

- **8 因子配置体系**：PE 百分位、股债性价比 FED、动量因子、波动率倒数、信息比率、MACD 信号、最大回撤、规模稳定性
- **-1~+1 因子评分**：信号规则映射 + 滚动百分位 / 截面 Z-score 标准化，加权总评 -6~+6
- **可调评分阈值**：前端 Web UI 五档对称阈值（强烈加仓 → 强烈减仓）
- **多数据源链**：AKShare → JoinQuant（聚宽），自动降级恢复
- **Web 管理界面**：仪表盘（含市场概况、资金流、板块排行）、基金池、基金详情、因子管理、推送配置、报告配置、调度计划、评分配置、质量过滤配置、历史报告、信号回测、系统设置（共 12 个页面）
- **基金详情模块**：阶段涨幅排序展示、季度持仓明细（可展开）、基金经理信息，含调仓 diff 和经理变更标注
- **一键批量导入**：自动识别 ETF/场外类型，自动从天天基金抓取相关主题标签
- **数据导出/导入**：支持历史报告导出备份（JSON 格式，含全部因子评分及信号）与恢复导入；基金池支持 JSON 格式一键导出，便于数据迁移与备份
- **"先展示缓存，手动/定时触发刷新"模式**：仪表盘行情数据、基金阶段涨幅均持久化缓存到数据库，页面加载直接展示缓存数据 + 时间戳；数据仅在手动手动刷新或定时推送任务触发时更新，推送后自动同步仪表盘缓存
- **定时分析**：交易日自动执行 + 手动触发
- **流式分析**：手动触发时分块处理基金数据，SSE 逐块推送结果至仪表盘，实时展示进度与中间结果
- **多渠道推送**：飞书机器人富文本卡片推送（含市场全景概览 + 逐只基金分析），推送内容严格跟随报告配置项过滤，未启用的报告项不会推送
- **AI 多模型配置**：支持 DeepSeek、智谱 GLM、通义千问、OpenAI 四种模型，系统设置页可视化配置供应商/API Key/Base URL，并支持**模型 ID 覆盖**（如 glm-4-plus / qwen-max / deepseek-reasoner，留空用预设默认）
- **AI Skills 分析技能**：可导入的提示词扩展包，前端一键启停/删除；对话时按序注入系统提示词，支持 `{{fund_pool}}`（基金池+最新分析）/ `{{market_regime}}`（市场环境）/ `{{fund:<id>}}`（单基金详情）三个数据占位符自动渲染，详见下方「AI Skills」章节
- **信号回测**：历史信号与基金净值按日期对齐，仓位策略模拟累计收益，信号有效性评分（买入看 N 日上涨/卖出看 N 日下跌），ECharts 双轴图表+评分明细表格
- **东方财富反爬虫补丁**：NID 授权令牌 + User-Agent 轮换 + 请求频率控制 + 全局默认请求超时（20s，防止死连接无限挂起耗尽线程池导致仪表盘数据更新 TimeoutError）
- **市场数据缓存**：5 分钟 TTL 缓存，大幅提升仪表盘加载速度
- **数据源连通性检测**：一键测试东方财富系列域名 + AI API 可达性，SSRF 防护，结果含延迟与状态汇总
- **AI 开关可控**：顶栏 AI 开关一键启停，配置持久化至数据库
- **并发控制架构**：独立线程池隔离（akshare 专用 16 workers，与 asyncio 默认线程池隔离）+ 全局信号量限流（并发 5）+ 强制超时保护（25s）+ asyncio.Lock 双重检查防止缓存穿透，根治 40-60 只基金批量分析时的线程池耗尽与超时堆积问题
- **共享数据缓存复用**：国债收益率、沪深300基准、指数估值（PE/PB）、ETF 行情、基金名称等全市场共享数据类级缓存（1h TTL），51 只基金并发分析时只发 1 次网络请求，其余命中缓存
- **批量并发获取**：分析流程与基金详情刷新均采用 `asyncio.gather` 并发获取（替代串行 for 循环），每只基金使用独立 DB session 避免并发冲突，51 只基金分析耗时从 15+ 分钟降至 2-4 分钟
- **调休/节假日日历自动同步**：从互联网官方通知源（默认 NateScarlet/holiday-cn，溯源国务院放假安排 gov.cn）同步当年+次年调休日历到 `holiday_calendar` 表。后台可配置同步地址、自动同步时间、开关；首次自动同步成功后自动停用（只同步一次），亦支持后台手动触发同步。后端 API 配置（暂无独立前端页面）

---

## 快速启动

### Docker 部署（推荐）

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env：填入 AI API Key、飞书 Webhook、TuShare Token（可选）

# 2. 一键启动
docker compose up -d

# 3. 访问 Web 界面
# http://localhost:8000 或 http://localhost

# 查看日志
docker compose logs -f backend
docker compose logs -f frontend
```

首次启动自动建表、执行迁移、写入默认因子配置。

### 本地开发

```bash
# 后端
pip install -r requirements.txt
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 前端
cd frontend
npm install
npm run dev   # http://localhost:5173（API 默认代理到 8000）
```

---

## 因子评分体系

### 默认因子集（11 个，总权重 8.0）

自研基金因子（7 个，基于基金净值序列）+ 市场环境因子（3 个，全池共享 MarketRegimeService 快照）+ MACD。

| # | 因子 | code | 权重 | 标准化 | 数据/公式 |
|---|------|------|------|--------|----------|
| 1 | 短期动量 | short_momentum | 1.2 | 截面 Z-score | `nav/nav(-20) - 1` |
| 2 | 中期动量 | mid_momentum | 1.2 | 截面 Z-score | `nav/nav(-60) - 1` |
| 3 | 波动率倒数 | inv_volatility | 1.0 | 截面 Z-score | `1 / (std(60日收益)×√252)` |
| 4 | 回撤修复度 | drawdown_recovery | 0.8 | 无（规则） | `nav / 252日最高`，>0.95→+1 |
| 5 | 收益风险比 | return_risk_ratio | 0.8 | 截面 Z-score | `mean(60日收益) / std` |
| 6 | 动量加速度 | momentum_accel | 0.5 | 截面 Z-score | `mom20 - mom60`，正值=加速 |
| 7 | 趋势一致性 | trend_consistency | 0.5 | 截面 Z-score | `sign(mom20)+sign(mom60)` 归一 |
| 8 | MACD信号 | macd_signal | 0.5 | 无（规则） | 金叉放量 +1 / 死叉放量 -1 |
| 9 | 大盘估值分位 | market_valuation | 0.8 | 无（规则） | 沪深300 PE 5年分位（负向） |
| 10 | 市场情绪 | market_sentiment | 0.5 | 无（规则） | 全市场涨跌家数比 |
| 11 | 资金面 | market_fund_flow | 0.5 | 无（规则） | 两融余额 7 日变化率 |

每因子评分 -1.0 ~ +1.0，加权求和。市场因子快照缺失时降级中性 0 分。

可手动启用的扩展因子：`price_percentile`（价格百分位）、`fed_model`（股债性价比，场外基金无 PE 自动降级中性）、`momentum_6m`、`info_ratio`（基准硬编码沪深300，主题基金参考性有限）、`max_drawdown`、`size_stability`（场外基金无规模序列自动降级中性）。

### 使用注意（重要）

- **截面 Z-score 因子需要足够大的基金池**：它衡量的是池内相对排名，无绝对价值判断。
  - 基金池 < 5 只：得分为纯池内排名（2 只时两基金 z=±1，即使两者动量都为正，也必有一只拿满档负分），系统会输出警告日志
  - 基金池 = 1 只：全部截面因子取中性 0.0
  - **建议基金池 ≥ 10 只**，否则请在因子管理页停用截面因子或改用规则型因子
- **动量系 4 因子同源**（#1/#2/#6/#7 全部由 20/60 日动量派生，合计权重 3.4）：本体系为动量主导设计；若希望降低动量集中度，可停用动量加速度/趋势一致性
- **信号判定不使用静态五档阈值**：最终方向/强度由第零层质量过滤的动态阈值决定（基础买 1.5 / 卖 -1.5，规模冲击或仓位漂移上调买入阈值），五档配置仅旧计算路径生效

### 第零层：质量过滤

前置否决（棺材钉/心电图形态、清盘风险）→ 因子修正（动量稳定性缩放波动率得分、超额持续性提升趋势一致性权重）→ 固定偏置（机构认可度/内部人增持）→ 动态阈值决策。参数在「质量过滤配置」页调整，详见 `backend/engines/quality_filter.py` 的 `QUALITY_CONFIG`。

### 实时净值预估（展示层，不参与信号）

仪表盘"实时估值"列与报告"前十大持仓涨跌"为盘中估算：场外基金优先天天基金官方估值（fundgz），不可用时按最新季报 top10 持仓 × 个股实时涨跌加权自算（东财→腾讯→新浪三级个股源）；覆盖率 <50% 自动切换指数混合模型。**分析信号始终基于官方净值计算**，估算值仅供盘中参考。

### AI Skills 分析技能

Skill 是一段可启停的**系统提示词扩展包**，用于给 AI 对话注入专业分析框架。管理入口：系统设置页「AI 分析技能」区块（新建/导入/启停/删除）。

**调用逻辑**：

```
用户消息 → 构建系统提示词：
  ① 基础角色提示词（量化助手身份与注意事项）
  ② 系统数据上下文（基金池最新分析 + 评分阈值）
  ③ 会话上下文（单基金/基金池/市场）
  ④ 启用的 Skills（按创建顺序，占位符已渲染为真实数据）→ LLM
```

**数据占位符**（写在 skill 的 system_prompt 里，注入前自动渲染；数据缺失降级为"（数据暂不可用）"不阻塞对话）：

| 占位符 | 渲染内容 |
|--------|---------|
| `{{fund_pool}}` | 全部活跃基金 + 最新评分/方向/强度 |
| `{{market_regime}}` | 沪深300 PE 分位、涨跌家数比、两融 7 日变化 |
| `{{fund:<id>}}` | 指定基金最新分析详情（评分/信号/因子/建议） |

**导入格式**（前端「导入 JSON」粘贴，按名称 upsert）：

```json
[
  {
    "name": "深度基本面分析",
    "description": "从持仓/风格/风险维度深度分析",
    "system_prompt": "你将扮演资深基金分析师…\n\n{{fund_pool}}\n\n{{market_regime}}",
    "enabled": true
  }
]
```

---

## API 概览

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/funds` | GET/POST | 基金池列表 / 新增 |
| `/api/funds/import` | POST | 批量导入 |
| `/api/funds/{id}` | PUT/DELETE | 更新 / 删除 |
| `/api/funds/batch` | PATCH | 批量启用/停用 |
| `/api/funds/detail` | GET | 基金阶段涨幅列表（优先缓存） |
| `/api/funds/detail/status` | GET | 基金详情缓存状态 |
| `/api/funds/{id}/holdings` | GET | 基金最新季度持仓 |
| `/api/funds/{id}/manager` | GET | 基金经理信息 |
| `/api/funds/change-summary` | GET | 持仓调仓 + 经理变更摘要 |
| `/api/funds/refresh-details` | POST | 触发后台刷新所有基金详情（阶段涨幅 + 扩展数据 + 持仓 + 经理），立即返回，前端轮询进度 |
| `/api/funds/refresh-details/status` | GET | 查询后台刷新进度（status/total/done/current/message/updated_at） |
| `/api/funds/{id}/refresh-themes` | POST | 重新抓取天天基金主题标签 |
| `/api/funds/realtime` | GET | 实时净值预估（场外 fundgz/持仓自算，场内 ETF 行情；force 跳过缓存） |
| `/api/analysis` | GET | 查询分析结果 |
| `/api/analysis/latest` | GET | 最新分析结果 |
| `/api/analysis/summary` | GET | 市场概况汇总（信号TOP10 + 资金流 + 板块排行 + 涨跌分布 + 成交额） |
| `/api/analysis/trigger` | POST | 手动触发分析（同步返回全部结果） |
| `/api/analysis/trigger-stream` | POST | 手动触发分析（SSE 流式推送，逐块返回结果） |
| `/api/analysis/refresh-summary` | POST | 后台刷新行情缓存数据（资金流 + 板块排行 + 涨跌分布 + 成交额） |
| `/api/backtest/{id}` | GET | 信号回测（含有效性评分） |
| `/api/factors` | GET/POST | 因子 CRUD |
| `/api/factors/export` | GET | 因子导出 JSON |
| `/api/factors/import` | POST | 因子导入 JSON |
| `/api/report-config` | GET/PUT | 报告配置项（14 项：5 基金维度 + 9 市场维度） |
| `/api/ai/chat` | POST | AI 对话 |
| `/api/ai/skills` | GET/POST | AI Skill 列表 / 新建 |
| `/api/ai/skills/import` | POST | Skill 批量导入 JSON（按名称 upsert） |
| `/api/ai/skills/{id}/toggle` | PATCH | Skill 启用/禁用 |
| `/api/ai/skills/{id}` | PUT/DELETE | 更新 / 删除 Skill |
| `/api/push-channels` | GET/POST | 推送渠道 |
| `/api/schedules` | GET/POST | 调度计划 |
| `/api/system` | GET/PUT | 系统配置（AI 开关、模型、API Key） |
| `/api/system/scoring-config` | GET/PUT | 评分阈值配置 |
| `/api/system/quality-config` | GET/PUT | 质量过滤参数配置（32 个参数分 6 组，含前置否决 / 因子修正 / 动态阈值 / 固定偏置） |
| `/api/system/connectivity` | GET | 数据源连通性测试 |
| `/api/holiday` | GET | 查看已同步的调休/节假日日历（`?year=2026`） |
| `/api/holiday/config` | GET/PUT | 调休同步配置（同步地址 / 自动同步时间 / 开关 / 最近同步时间） |
| `/api/holiday/sync` | POST | 后台手动同步调休日历（不受自动开关限制，可指定 year/url） |
| `/health` | GET | 健康检查 |

---

## 数据源链

```
请求数据 → DataSourceManager
  主 → AKShare（东财，按 fund_type 路由 ETF/OTC，另一方自动降级）
  备 → JoinQuant 聚宽（需账号，jqdatasdk）
```

任一源连续失败后降级，5min 后自动尝试恢复。
基金导入时自动根据代码前缀标记场内/场外类型（fund_type），查询时直接路由到对应接口，无需轮询降级。

---

## 配置说明

### 环境变量

| 环境变量 | 必填 | 默认值 | 说明 |
|---------|------|--------|------|
| `DEFAULT_AI_MODEL` | 否 | `deepseek` | AI 模型名称 |
| `DEFAULT_AI_BASE_URL` | 否 | `https://api.deepseek.com/v1` | AI API 基础 URL |

> AI API Key 通过 Web 界面"系统配置"设置（存入 system_config 表），不再从 .env 读取。
| `FEISHU_WEBHOOK_URL` | 否 | — | 飞书机器人 Webhook URL |
| `FEISHU_WEBHOOK_SECRET` | 否 | — | 飞书签名密钥 |
| `JOINQUANT_USER` | 否 | — | 聚宽账号 |
| `JOINQUANT_PASSWORD` | 否 | — | 聚宽密码 |
| `FUND_QUANT_DATABASE_DIR` | 否 | `data/` | 数据库目录 |
| `FUND_QUANT_DATABASE_NAME` | 否 | `fund_quant.db` | 数据库文件名 |
| `FUND_QUANT_HOST` | 否 | `0.0.0.0` | 服务监听地址 |
| `FUND_QUANT_PORT` | 否 | `8000` | 服务监听端口 |
| `FUND_QUANT_DEBUG` | 否 | `false` | 调试模式 |
| `FUND_QUANT_CORS_ORIGINS` | 否 | `http://localhost:5173,...` | CORS 允许的来源 |
| `TZ` | 否 | `Asia/Shanghai` | 时区 |

完整项见 `.env.example`。

### 运行时配置（数据库存储）

以下配置存储在 `system_config` 表中，可通过前端「系统设置」页面动态调整：

- AI 开关（`ai_enabled`）
- AI 模型名称（`ai_model`，支持 deepseek / glm / tongyi / openai）
- AI API Key（`ai_api_key`）
- AI API 基础 URL（`ai_base_url`）
- 系统设置页提供可视化配置卡片，支持预设模型快速切换
- 评分阈值（`scoring_thresholds`，五档对称阈值 JSON）
- 质量过滤参数（`quality_filter_config`，32 个数值参数，覆盖棺材钉 / 心电图 / 清盘 / 因子修正 / 动态阈值 / 固定偏置 6 组）
- ⚠️ **关键约束**：`quality_filter_config.base_sell_threshold` 必须等于五档阈值的中性下界 **-1.5**（对应「适度减仓 / 卖出」）。若写成 `-3.0`，`(-3.0, -1.5]` 整个区间会被错归「观望」，导致**永久不出现卖出信号**。当前正确值：`{"base_buy_threshold": 1.5, "base_sell_threshold": -1.5}`（详见文末「修复记录」）。
- 调休/节假日同步配置（键名 → 默认值）：
  - `holiday_sync_url` → `https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json`（数据源地址，`{year}` 占位符在抓取时替换为年份；可选 timor.tech 等兼容格式）
  - `holiday_auto_sync_time` → `03:00`（每日自动检查时间，HH:MM）
  - `holiday_auto_sync_enabled` → `true`（自动同步开关；首次同步成功后自动置 `false`，只同步一次）
  - `holiday_last_sync_at` → 空（最近一次同步时间，ISO 时间戳）
  - 同步数据落 `holiday_calendar` 表（`holiday_date` 唯一，`is_off_day` 表示休市/补班开市），通过 `GET/PUT /api/holiday/config` 与 `POST /api/holiday/sync` 管理。

---

## 性能优化与并发控制

针对 40-60 只基金批量分析时出现的线程池耗尽、超时堆积、调度计划严重延迟等问题，系统实现了完整的并发控制架构。

### 核心组件：`backend/utils/concurrency.py`

| 组件 | 作用 |
|------|------|
| `_AKSHARE_POOL` | akshare 专用独立线程池（16 workers），与 asyncio 默认线程池隔离，防止 akshare 卡死耗尽默认池 |
| `_AKSHARE_SEM` | 全局信号量（并发 5），替代原全局时间戳串行限流，允许合理并发 |
| `run_with_timeout()` | 强制超时保护（默认 25s），超时后释放线程槽位，防止死连接无限挂起 |
| `run_batch_with_timeout()` | 批量并发执行 + 超时控制 |
| `random_ua()` / `rotate_ua_for_akshare()` | User-Agent 轮换，降低反爬风险 |
| `shutdown_pool()` | 应用关闭时清理线程池 |

### 共享数据缓存（asyncio.Lock + 双重检查）

以下全市场共享数据使用类级缓存 + asyncio.Lock 双重检查模式，51 只基金并发时只发 1 次网络请求：

| 缓存项 | TTL | 锁粒度 | 说明 |
|--------|-----|--------|------|
| 基金名称（场外） | 1h | 全局锁 | `fund_open_fund_rank_em` 批量拉取，内存 + JSON 文件双重持久化 |
| 基金名称（ETF） | 1h | 全局锁 | `fund_etf_spot_em` 批量拉取 |
| 国债收益率 | 1h | 全局锁 | 复用 `_index_value_cache` 中的 000300 数据，0 次额外请求 |
| 沪深300基准 | 1h | 全局锁 | `stock_zh_index_daily`，全市场共享基准 |
| 指数估值（PE/PB） | 1h | 按 index_code 分锁 | `stock_zh_index_value_csindex`，同指数多 ETF 不重复请求 |

### 批量并发获取

- **分析流程**（`analysis_service.py`）：`asyncio.gather` 并发获取基金数据，季度数据加载 + 因子计算保持串行（AsyncSession 非并发安全）
- **基金详情刷新**（`fund_refresh_task.py`）：`asyncio.gather` + `Semaphore(5)` 并发刷新，每只基金独立 `async_session_factory()` 避免 AsyncSession 并发冲突
- **仪表盘资金流**（`market_service.py`）：信号量并发限流替代全局时间戳串行，超时从 45s 降至 25s

### Docker 构建优化

Dockerfile 使用阿里云镜像源加速依赖下载（apt + pip），构建时间从 20+ 分钟降至 2-3 分钟：

```dockerfile
# apt 镜像源替换
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources

# pip 阿里云镜像源
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com
```

### 性能基准（51 只基金）

| 场景 | 优化前 | 优化后 |
|------|--------|--------|
| 仪表盘数据加载 | TimeoutError（线程池耗尽） | < 1s |
| 基金详情批量刷新 | 严重延迟 / 无响应 | 60-90s |
| 全量分析任务 | 15+ 分钟（含 229 次超时重试） | 2-4 分钟（0 次超时） |
| 共享数据网络请求 | 51 次/项（缓存穿透） | 1 次/项 |

---

## 关键设计原则

1. **价值为主，动量辅助**：PE 低估 + 高 FED + 动量确认
2. **可配置**：所有因子参数、信号阈值、权重在 UI 中可调
3. **数据容错**：多源链自动降级
4. **前后端解耦**：因子配置 → 因子引擎 → 评分引擎 → 报告引擎，各层独立
5. **异步非阻塞**：FastAPI + 异步 SQLAlchemy + aiosqlite
6. **API 调用缓存**：高频数据源 API 自动缓存（全量基金列表 1h TTL），避免重复请求触发限流
7. **配置不写死**：敏感信息通过 `.env`，运行时配置通过数据库存储
8. **缓存优先展示**：仪表盘行情数据、基金阶段涨幅均持久化缓存，页面加载直接展示 + 时间戳，手动/定时触发刷新
9. **安全加固**：连通性测试含 SSRF 防护（内网地址校验），反爬虫补丁自动加载
10. **线程池隔离**：akshare 调用使用专用独立线程池，与 asyncio 默认线程池隔离，防止数据源卡死拖垮整个应用
11. **共享数据复用**：全市场共享数据（国债收益率、基准指数、估值）类级缓存 + asyncio.Lock 双重检查，避免 N 只基金 N 次重复请求
12. **强制超时保护**：所有外部数据调用均通过 `run_with_timeout` 包裹，超时后释放资源，永不无限挂起

---

## 修复记录（2026-07-22）

### 1. 信号永远无卖出（已修复，两次根因）

- **一次根因（2026-07-19）**：`backend/engines/quality_filter.py` 中 `QUALITY_CONFIG["base_sell_threshold"] = -3.0` 与五档阈值的「适度减仓」边界 `-1.5` 不一致，导致 `determine_signal` 要求 `score <= -3.0` 才卖出；实测最低分仅 `-2.3`，整个 `(-3.0, -1.5]` 区间被错归「观望」，永久不出现卖出信号。
- **一次修复**：代码默认值改为 `-1.5`（commit `caf53a5`），并**固化进数据库** `system_config.quality_filter_config = {"base_buy_threshold": 1.5, "base_sell_threshold": -1.5}`，使前后台一致、防止前台「保存」覆盖回旧值。
- **验证**：修复后 025657（国金智远量化选股混合A）等 `score <= -1.5` 的基金正确判为「适度减仓 / 卖出」。
- **二次根因（2026-08-15 复现）**：阈值改回 -1.5 后**仍 0 卖出**。因子计算与数据获取完全正常（数据非瓶颈），但当时激活的 7 因子中 6 个是截面相对 z-score（同一基金池内互相抵消≈0），唯一绝对因子 `drawdown_recovery` 在普涨市恒为 +1.0（白送 +0.8 地板），`trend_consistency`(权重0.5) 是唯一可负项（最差 -0.5）。理论最低加权分≈-0.5，远低于卖出阈值 -1.5 → 卖出在数学上不可达。
- **二次修复**：激活因子集须含「双向绝对因子」。已在 `backend/database.py` 的因子种子（`new_factors_config` 迁移块 + 空库初始化块）中补齐 `macd_signal`（金叉+1.0 / 死叉-1.0，权重 0.5，normalization=none）。`init_db` 的迁移块对已有库「缺失则补齐」——自动激活该因子且**不破坏用户已调的权重/启用状态**；新库亦默认含之。走弱基金（死叉 + 相对池最弱）加权分可落到 -1.5 以下，卖出信号恢复。
- **验证**：因子集含 macd_signal 后，普涨市仍 0 卖出（正确，不该卖）；分化市中走弱的基金能被正确判为「适度减仓 / 卖出」。

### 2. 历史报告导出接口 500（已修复）

- **根因**：`backend/routers/analysis.py` 的 `export_analysis` 调用 `payload.model_dump_json(indent=2, ensure_ascii=False)`。Pydantic v2 的 `model_dump_json()` 不接受 `ensure_ascii` 参数（`ensure_ascii` 是 `json.dumps` 的参数），抛 `TypeError` → `GET /api/analysis/export` 返回 500。
- **修复**：改为 `json.dumps(payload.model_dump(), ensure_ascii=False, indent=2)`（commit `27e0990`）。

### 已知问题（待优化，非阻塞）

- **因子标准化不一致**：落库的因子分值与使用存储 `raw_value` 重新跑引擎 `apply_cross_sectional_zscore` 的结果存在偏差（short_momentum 18/22、mid_momentum 8/22 不一致），疑似 `run_analysis` 中 `normalize_cross_sectional` 的截面 cohort 或因子 `normalization` 标志传入不一致，需专项排查后最小化修改。取数与因子配置值本身均正常。
- **部分基金因子塌缩**：实时批量分析时少数基金（价格历史不足或 akshare 抓取超时）多因子 `raw=0.0`（计算器的「数据不足」哨兵值），因子归中性 → 偏观望，属数据完整性问题。

---

## 修复记录（2026-08-05）

### 1. 定时推送周一不触发（已修复）

- **根因**：`backend/scheduler/task_scheduler.py` 的推送闸门用 `is_trading_day()`（内部 `chinese_calendar.is_workday`）判断，该方法会**额外跳过调休休息日**（如 2026-02-16、02-23、04-06、05-04、10-05 等调休周一），导致「调度计划填写 mon-fri 但每周一都没推送」。
- **修复**：推送闸门改为仅跳过周末（`if date.today().weekday() >= 5: return`），用户的 mon-fri 星期计划被如实尊重；调休周一虽非交易日，但属用户显式配置的工作日，照常推送（commit `0018a4c`）。
- **前端配套**：`frontend/src/pages/SchedulePlan.tsx` 的 cron 占位符从 `0 50 14 * * mon-fri`（6 字段非法）修正为 `50 14 * * mon-fri`（5 字段标准 APScheduler cron）。

### 2. 新增调休/节假日日历自动同步（新功能）

- **需求**：27 年调休无法预测，需从互联网官方通知源读取调休安排，后台可配置自动同步时间及地址，同步成功后不再自动同步，后期可手动触发。
- **实现**（commit `32dce80`）：
  - 新增 `backend/models/holiday_calendar.py`：`holiday_calendar` 表（`holiday_date` 唯一，`is_off_day` 区分休市/补班开市，`holiday_name`/`source`/`synced_at`）。
  - 新增 `backend/services/holiday_sync_service.py`：抓取并解析 NateScarlet/holiday-cn（默认，溯源 gov.cn）与 timor.tech 两种 JSON 格式；`sync_holiday_calendar` 幂等 upsert 当年+次年；`auto_sync_if_enabled` 仅在开关开启时执行，同步成功后自动置 `enabled=false`（只同步一次）。
  - 新增 `backend/routers/holiday.py`：`GET /api/holiday`（查看）、`GET/PUT /api/holiday/config`（配置）、`POST /api/holiday/sync`（手动同步，绕过开关）。
  - `backend/scheduler/task_scheduler.py` 注册 `holiday_auto_sync` 每日任务（在 `holiday_auto_sync_time` 触发）。
  - `backend/database.py` 迁移写入默认配置（`holiday_sync_url` / `holiday_auto_sync_time=03:00` / `holiday_auto_sync_enabled=true` / `holiday_last_sync_at`）。
- **验证**：py_compile + 导入冒烟 + upsert 幂等 + 自动同步成功后开关置 false 均通过；真实网络抓取受沙箱代理限制未实跑（生产环境按默认地址可达）。
- **说明**：当前推送闸门仍为「仅跳过周末」；`holiday_calendar` 数据尚未回接到推送闸门（如需「调休周一补班开市也推送 / 调休周六休市不推送」可后续接线，本次未改动用户既定行为）。
