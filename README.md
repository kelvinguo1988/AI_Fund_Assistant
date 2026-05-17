# AI Fund Assistant — 基金量化交易系统

> 基于 FastAPI + React 的基金量化分析平台。多渠道数据源采集，双层因子评分，自动化信号推送。

---

## 系统架构

```
AI_Fund_Assistant/
├── backend/                    # FastAPI 后端
│   ├── main.py                 # 应用入口 + 路由挂载
│   ├── server.py               # 独立服务启动入口
│   ├── config.py               # 配置（.env → Settings）
│   ├── database.py             # SQLAlchemy 异步引擎
│   ├── models/                 # ORM 模型（fund, analysis_result, factor...）
│   ├── schemas/                # Pydantic 输出 Schema
│   ├── routers/                # API 路由（fund, analysis, factor, schedule, ai...）
│   ├── services/               # 业务逻辑层
│   ├── data_sources/           # 数据源适配器
│   │   ├── base.py             # 抽象基类
│   │   ├── akshare_adapter.py  # 主数据源（AKShare + 东方财富 OTC 回退）
│   │   ├── tushare_adapter.py  # 次级数据源（需 Token）
│   │   ├── baostock_adapter.py # 备用数据源 1
│   │   ├── tickflow_adapter.py # 备用数据源 2
│   │   └── data_source_manager.py  # 数据源链编排 + 降级恢复
│   └── scheduler/              # 定时任务调度器
├── frontend/                   # React 前端（TypeScript + MUI）
│   ├── src/pages/              # 页面（仪表盘、基金池、因子、分析、AI 对话...）
│   ├── src/api/                # API 客户端
│   └── nginx.conf              # Nginx 配置（API 反向代理 + SPA 回退）
├── docker-compose.yml          # 一键部署
├── .env.example                # 环境变量模板
└── requirements.txt
```

---

## 核心功能

- **多数据源链**：AKShare → TuShare → BaoStock → TickFlow，自动降级与恢复
- **双层因子评分**：市场层（PE百分位/ERP/动量/广度）+ 基金层（回撤/夏普/超额收益）
- **Web 管理界面**：基金池管理、因子配置、分析结果可视化、AI 对话
- **批量导入**：一键批量添加基金，自动识别类型（ETF/场外）
- **定时分析**：交易日定时触发，支持手动触发
- **多渠道推送**：飞书机器人推送分析报告（富文本卡片）
- **AI 分析**：集成 DeepSeek/ChatGPT，生成自然语言分析建议

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

### 本地开发

```bash
# 后端
pip install -r requirements.txt
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 前端
cd frontend
npm install
npm run dev   # 默认 http://localhost:5173
```

---

## API 概览

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/funds` | GET/POST | 基金池列表 / 新增 |
| `/api/funds/import` | POST | 批量导入基金 |
| `/api/funds/{id}` | PUT/DELETE | 更新 / 删除 |
| `/api/funds/batch` | PATCH | 批量启用/停用 |
| `/api/analysis` | GET | 查询分析结果 |
| `/api/analysis/latest` | GET | 最新分析结果 |
| `/api/analysis/trigger` | POST | 手动触发分析 |
| `/api/factors` | GET/POST | 因子管理 |
| `/api/ai/chat` | POST | AI 对话分析 |
| `/api/push-channels` | GET/POST | 推送渠道管理 |
| `/api/schedules` | GET/POST | 调度计划配置 |
| `/health` | GET | 健康检查 |

---

## 数据源链

```
请求数据
    │
    ▼
┌──────────────────────────────────────────────┐
│            DataSourceManager                  │
│  主 → AKShareAdapter    (可用? 调用 → 成功 ✓) │
│  次 → TuShareAdapter    (降级? 等待 5min → 重试) │
│  备1→ BaoStockAdapter   (恢复? 提升为主)       │
│  备2→ TickFlowAdapter                         │
└──────────────────────────────────────────────┘
```

- **AKShare**（主）：东方财富接口，ETF 数据 + OTC 基金回退
- **TuShare**（次）：专业金融数据，需 [https://tushare.pro](https://tushare.pro) 注册获取 Token
- **BaoStock**（备用）：免费数据源，无需 Token
- **TickFlow**（末级保底）：可选安装

任一数据源连续失败后降级，5 分钟后自动尝试恢复。

---

## 配置说明

| 环境变量 | 必填 | 说明 |
|---------|------|------|
| `DEFAULT_AI_API_KEY` | 是 | AI 模型 API Key |
| `FEISHU_WEBHOOK_URL` | 否 | 飞书推送 Webhook |
| `TUSHARE_TOKEN` | 否 | TuShare Pro Token（增强数据源） |
| `FUND_QUANT_CORS_ORIGINS` | 否 | CORS 允许的来源 |

完整配置项见 `.env.example`。

---

## 评分体系

| 综合评分 | 信号 | 建议操作 |
|---------|------|---------|
| ≥ 4.0 | 强烈加仓 | 加仓 10% |
| ≥ 3.5 | 建议加仓 | 加仓 5% |
| ≥ 2.5 | 观望不动 | 不操作 |
| ≥ 2.0 | 适当减仓 | 减仓 5% |
| < 2.0 | 建议减仓 | 减仓 10% |

设计思路：逆向价值为主，低估+超跌时买入；动量因子辅助趋势判断。

---

## 关键设计原则

1. **逆向买入为主**：PE 低估 + 回撤大 + 动量弱 = 高分
2. **双重验证**：市场时机好 AND 基金状态好，两层都支持才给强信号
3. **数据源容错**：多源链自动降级，单一源失败不影响整体流程
4. **异步非阻塞**：FastAPI + aiosqlite，全异步 I/O
5. **配置不写死**：所有敏感信息通过 `.env` 注入
