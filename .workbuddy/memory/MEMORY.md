# Memory - 基金量化交易系统

## 项目概况
- 项目路径: /Users/sec-t/Downloads/daily_stock_analysis-main/基金AI助手/
- 项目名: fund_quant_system
- 架构: 前后端分离 (FastAPI + SQLite + React + MUI + Tailwind CSS + ECharts)
- 完全独立项目，不与上级 daily_stock_analysis 共享代码

## 用户关键决策
- QQ机器人: 暂不接，先做飞书推送
- AI大模型: 多模型可切换 (DeepSeek/OpenAI/通义千问)，架构抽象LLM接口
- 初始因子: 精简5因子 (PE百分位/股债性价比FED/MACD信号/均线趋势MA20-60/成交量变化)
- 量化信号: 买入≥3.5分(3.5-4.0轻度/4.0-4.5中度/≥4.5重度)，卖出≤2.0分，2.0-3.5观望

## 技术细节
- Python 3.9 兼容: 所有 Mapped[X | None] 需用 Mapped[Optional[X]]，非model文件用 from __future__ import annotations
- akshare 版本约束: ≥1.16.72 (1.14.3不支持Python 3.9)
- Pydantic v2: model_name 字段需设置 model_config = {"protected_namespaces": ()}
- FundData 是 dataclass 不是 BaseModel，无 nav/turnover/pe_history 字段
- 数据库路径: data/fund_quant.db (自动创建)

## Docker 部署
- 生产模式: docker-compose.yml (nginx:alpine + python:3.9-slim)
- 开发模式: docker-compose.dev.yml (源码挂载 + 热重载)
- WORKDIR=/app, config.py PROJECT_ROOT 在容器中解析为 /app
- nginx 反向代理: /api → http://backend:8000, /health → http://backend:8000
- SQLite 持久化: ./data:/app/data volume
- 部署命令: ./deploy.sh (prod) / ./deploy.sh dev (开发)
- vite.config.ts 支持 VITE_API_PROXY_TARGET 环境变量 (Docker dev 模式用)
