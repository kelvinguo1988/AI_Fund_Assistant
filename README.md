# -AI-
个人基金量化交易AI助手

基金量化交易系统——5量化因子引擎+加权评分+飞书推送+AI多模型对话。
模块概览
模块	说明
后端 FastAPI	可启动，8张表自动建库
5因子引擎	PE百分位/FED/MACD/MA趋势/成交量变化
加权评分	≥3.5买入(3档) / ≤2.0卖出(3档) / 2.0-3.5观望
飞书推送	卡片消息，红涨绿跌
AI助手	DeepSeek/OpenAI/通义千问可切换
前端7页面	仪表盘+基金池+因子+推送+报告+调度+历史
	
	
启动方式
# 后端（已验证可启动）
cd /基金AI助手
pip install -r backend/requirements.txt
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

# 前端
cd frontend && npm install && npm run dev

docker
# 一键启动
cp .env.example .env          # 复制配置
vim .env                      # 填写 AI_API_KEY 等
chmod +x deploy.sh
./deploy.sh                   # 生产模式 → http://localhost
./deploy.sh dev               # 开发模式 → http://localhost:5173
