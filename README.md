# AI Fund Assistant — 基金量化交易系统

> FastAPI + React 基金量化分析平台。8 因子配置体系、双层评分、Web 管理、自动化信号推送。

---

## 系统架构

```
AI_Fund_Assistant/
├── backend/                    # FastAPI 异步后端
│   ├── main.py                 # 应用入口 + 路由挂载
│   ├── server.py               # 独立服务启动
│   ├── config.py               # .env → Settings
│   ├── database.py             # SQLAlchemy 异步引擎 + 迁移
│   ├── models/                 # ORM 模型
│   ├── schemas/                # Pydantic Schema
│   ├── routers/                # API 路由
│   ├── services/               # 业务逻辑层
│   ├── engines/                # 因子引擎 + 评分引擎 + 报告引擎
│   │   ├── factor_engine.py    # 8 因子计算 + 信号规则 + 截面标准化
│   │   ├── scoring_engine.py   # 加权评分 + 信号判定
│   │   └── report_engine.py    # 报告生成（Markdown / HTML）
│   ├── data_sources/           # 多数据源适配器
│   ├── push/                   # 推送机器人（飞书等）
│   └── scheduler/              # 定时任务调度
├── frontend/                   # React + TypeScript + MUI
│   ├── src/pages/              # 8 个管理页面
│   ├── src/components/         # 图表组件（ECharts）
│   ├── src/api/                # API 客户端
│   └── nginx.conf              # Nginx（API 反向代理 + SPA）
├── docker-compose.yml          # 一键部署
├── .env.example
└── requirements.txt
```

---

## 核心功能

- **8 因子配置体系**：PE 百分位、股债性价比 FED、动量因子、波动率倒数、信息比率、MACD 信号、最大回撤、规模稳定性
- **-1~+1 因子评分**：信号规则映射 + 滚动百分位 / 截面 Z-score 标准化，加权总评 -6~+6
- **可调评分阈值**：前端 Web UI 五档对称阈值（强烈加仓 → 强烈减仓）
- **多数据源链**：AKShare → TuShare → BaoStock → TickFlow，自动降级恢复
- **Web 管理界面**：仪表盘（含市场概况、资金流、板块排行）、基金池、因子管理、报告配置、调度计划
- **一键批量导入**：自动识别 ETF/场外类型
- **定时分析**：交易日自动执行 + 手动触发
- **多渠道推送**：飞书机器人富文本卡片推送（含市场全景概览 + 逐只基金分析）
- **AI 分析**：集成 DeepSeek / ChatGPT，生成自然语言建议
- **东方财富反爬虫补丁**：NID 授权令牌 + User-Agent 轮换 + 请求频率控制
- **市场数据缓存**：5 分钟 TTL 缓存，大幅提升仪表盘加载速度

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

### 8 个内置因子

| 因子 | 方向 | 权重 | 数据字段 | 公式 | 标准化 |
|------|------|------|---------|------|--------|
| PE 百分位 | 负向 | 1.2 | pe_ttm | `percentile_rank(pe, 1250)` | 无 |
| 股债性价比 FED | 正向 | 1.2 | index_pe, bond_yield_10y | `(1/pe) - bond_yield` | 滚动百分位 |
| 动量因子 | 正向 | 1.0 | nav | `(nav/shift(nav,126)-1) / (std×√126)` | 无 |
| 波动率倒数 | 正向 | 0.8 | nav | `1 / std(returns, 60)` | 截面 Z-score |
| 信息比率 | 正向 | 0.8 | nav, benchmark_nav | `annualize(excess_mean,252) / (std×√252)` | 截面 Z-score |
| MACD 信号 | 正向 | 0.5 | nav | `ema(12) - ema(26)` | 无 |
| 最大回撤 | 正向 | 0.5 | nav | `max_drawdown(nav, 252)` | 截面 Z-score |
| 规模稳定性 | 正向 | 0.4 | fund_size_quarterly | `1 / cv(size) + size_bonus` | 截面 Z-score |

每因子评分范围 -1.0 ~ +1.0（信号规则 IF-THEN 映射），加权求和总分 -6.4 ~ +6.4。

### 信号判定（五档对称阈值）

| 加权评分 | 信号 | 建议权益仓位 | 操作建议 |
|---------|------|------------|---------|
| ≥ 3.0 | 强烈加仓 (heavy_buy) | 90% | 强烈建议加仓 |
| ≥ 1.5 | 适度加仓 (moderate_buy) | 70% | 建议适度加仓 |
| ≥ -1.5 | 中性观望 (hold) | 50% | 持有观望 |
| ≥ -3.0 | 适度减仓 (moderate_sell) | 30% | 建议适度减仓 |
| < -3.0 | 强烈减仓 (heavy_sell) | 10% | 强烈建议减仓 |

阈值可在前端「评分配置」页面动态调整。

### 标准化机制

- **滚动百分位**（FED 模型）：使用自身历史分布做动态阈值
- **截面 Z-score**（波动率倒数、信息比率、最大回撤、规模稳定性）：同一分组内跨基金标准化，按 Z 值映射到 -1~+1

---

## API 概览

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/funds` | GET/POST | 基金池列表 / 新增 |
| `/api/funds/import` | POST | 批量导入 |
| `/api/funds/{id}` | PUT/DELETE | 更新 / 删除 |
| `/api/funds/batch` | PATCH | 批量启用/停用 |
| `/api/analysis` | GET | 查询分析结果 |
| `/api/analysis/latest` | GET | 最新分析结果 |
| `/api/analysis/summary` | GET | 市场概况汇总（信号TOP5 + 资金流 + 板块排行 + 涨跌分布 + 成交额） |
| `/api/analysis/trigger` | POST | 手动触发分析 |
| `/api/factors` | GET/POST | 因子 CRUD |
| `/api/system/scoring-config` | GET/PUT | 评分阈值配置 |
| `/api/report-config` | GET/PUT | 报告配置项（14 项：5 基金维度 + 9 市场维度） |
| `/api/ai/chat` | POST | AI 对话 |
| `/api/push-channels` | GET/POST | 推送渠道 |
| `/api/schedules` | GET/POST | 调度计划 |
| `/health` | GET | 健康检查 |

---

## 数据源链

```
请求数据 → DataSourceManager
  主 → AKShare（东财，ETF + OTC 回退）
  次 → TuShare Pro（需 Token）
  备 → BaoStock（免费）
  末 → TickFlow（保底）
```

任一源连续失败后降级，5min 后自动尝试恢复。

---

## 配置说明

| 环境变量 | 必填 | 说明 |
|---------|------|------|
| `DEFAULT_AI_API_KEY` | 是 | AI 模型 API Key |
| `FEISHU_WEBHOOK_URL` | 否 | 飞书推送 |
| `TUSHARE_TOKEN` | 否 | TuShare Pro Token |
| `FUND_QUANT_CORS_ORIGINS` | 否 | CORS 源 |

完整项见 `.env.example`。

---

## 关键设计原则

1. **价值为主，动量辅助**：PE 低估 + 高 FED + 动量确认
2. **可配置**：所有因子参数、信号阈值、权重在 UI 中可调
3. **数据容错**：多源链自动降级
4. **前后端解耦**：因子配置 → 因子引擎 → 评分引擎 → 报告引擎，各层独立
5. **异步非阻塞**：FastAPI + 异步 SQLAlchemy
6. **配置不写死**：敏感信息通过 `.env`，阈值通过数据库存储
