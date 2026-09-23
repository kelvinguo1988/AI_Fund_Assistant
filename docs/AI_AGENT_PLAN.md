# AI 分析 Agent 重构设计方案（2026-09-23）

> 目标：AI 模块从"纯聊天挂件"升级为**全项目分析 Agent** — 通过工具调用（function calling）串联基金池、因子、评分、质量过滤、回测、市场数据与历史分析报告，输出因子有效性诊断、调仓换基金建议与每日 AI 简报；支持 Skill 扩展与外部 Agent 接入。
>
> 状态：设计待确认，未开发。

## 1. 现状基线（调研结论）

- LLM 层：`backend/llm/base.py` 仅 `chat(system_prompt, messages)→str`，**无 tool calling、无流式**；四家 provider（deepseek/openai/tongyi/glm）均基于 OpenAI SDK AsyncOpenAI，**底层天然支持 tools/stream**，缺的只是抽象层透传。
- 编排层：`services/ai_service.py` 把基金池+评分**预塞进 system prompt**（4000 字符截断），AI 只能"看快照说话"，不能按需取数 — 这是本次重构的核心痛点。
- 数据面已具备但 AI 用不到：逐因子分已整包 JSON 落库（`AnalysisResult.factor_scores`）、回测有效性统计（`backtest_service` avg/buy/sell_effectiveness + `auto_backtest_service` 全量落表）、市场资金流/估值分位/市场环境（market_service / index_valuation / market_regime）、持仓重叠（holding-overlap）、赛道暴露标签、错误日志统计。
- 缺口：① `original_score / quality_warnings / dynamic_buy_threshold` 未落库（因子/质量有效性历史诊断的前置数据）；② **系统没有"用户持仓"概念**（Fund 表无份额/成本，fund_holdings 是季报股票持仓）；③ 无胜率/盈亏比统计；④ AISkill 只做提示词注入，不是可执行工具；⑤ 前端非流式、纯文本渲染。
- 可复用机制：feature_flags（`_feature_flag()`）、SSE 分析流（analysis_service 已有流式模式）、调度器（Schedule 表 + reload_jobs 热更新）、推送策略（push/base.py + feishu）。
- 场外策略调研结论（业界通行评估法，映射到本系统数据）：因子层用 **IC / RankIC / IR + 分位收益**；信号层用 **N 日前瞻胜率、盈亏比、持有期收益、翻转（whipsaw）率**；质量过滤用"警告组 vs 无警告组后续收益差"；配置层用"阈值平移敏感性"。以上全部可由已落库的 factor_scores + 净值历史回算，**无需新增每日埋点即可冷启动**。

## 2. 总体架构

```
前端 AI 工作台（流式 + markdown + 工具过程时间线 + 一键分析任务）
        │ SSE
routers/ai_agent.py ──► ai/agent_runner.py（ReAct 循环：LLM ⇄ 工具，≤8 轮，预算/超时护栏）
                              │
                     ai_tools/registry.py（统一 ToolSpec：name/schema/handler/只读白名单/行数上限）
                              │
        ┌── 内置数据工具（包装既有 services，全部只读）
        │    pool/signals/factor_scores/factor_stats/backtest_summary/
        │    signal_history/market_snapshot/regime/index_valuation/
        │    otc_trade_status/holding_overlap/error_stats/nav_series
        ├── 分析任务工具（"重活"由纯 Python 统计引擎产出，LLM 只解读）
        │    factor_audit.evaluate(...)   → 因子 IC/IR/分位/胜率
        │    rebalance.analyze(...)       → 调仓候选+理由
        │    daily_brief.generate()       → 每日简报结构化数据
        └── 扩展工具：AISkill-as-tool（带参数 schema 的技能注册为可调用工具）；
             外部 Agent/MCP 适配器（P4 可选，接口预留 ToolSource 抽象）
```

分层原则：**统计计算不交给 LLM**（IC/胜率/组合优化全部 Python 实现，确定性、可测试、零 token 成本），LLM 负责取数编排、交叉解释与报告成文。

## 3. 模块设计

### 3.1 LLM Provider 扩展（backend/llm/）
- `base.py` 新增 `async chat_with_tools(messages, tools, timeout) → LLMResponse{content|tool_calls, usage}` 与 `astream(...)`；旧 `chat()` 保留为兼容包装。
- 四 provider 透传 OpenAI SDK `tools/tool_choice/stream`；不支持 tools 的模型自动回退旧 prompt 模式（factory 检测）。
- 消息格式沿用 OpenAI：`role=tool` 结果回填。

### 3.2 Agent 运行器（新增 backend/ai/agent_runner.py）
- ReAct 循环：系统提示（项目能力说明书+口径宪法：±8.5 评分、8.3 权重、五档阈值、场外申购约束）→ LLM → 并行执行 tool_calls → 结果截断回填 → 直至 final answer 或轮次/预算耗尽。
- 事件流：`step / tool_call / tool_result / token / done / error`，经 SSE 推前端；全量落库供回放。
- 护栏：单任务 max_rounds=8、max_tool_rows=500、单工具 20s 超时、总 token 预算；`_feature_flag("ai_agent_enabled")` 灰度开关，默认开。
- 会话扩展：`ai_messages` 增 `tool_calls(JSON)`、`agent_events(JSON)` 列（SQLite 直接可空新列，无迁移风险）。

### 3.3 工具层（新增 backend/ai_tools/）
- `registry.py`：`@tool(name, description, schema, read_only=True)` 装饰器注册；runner 按白名单导出 OpenAI tools。
- 内置工具首批（全部薄包装现有 service，含返回裁剪）：
  | 工具 | 数据源 | 用途 |
  |---|---|---|
  | list_fund_pool / get_fund_detail | FundService | 池内清单/单基详情 |
  | get_latest_signals / get_signal_history(code, days) | AnalysisResult | 当前信号/历史信号序列（翻转检测） |
  | get_factor_snapshot / get_factor_history(code, factor) | factor_scores JSON | 截面分布/单因子时序 |
  | get_backtest_summary(code|all) | backtest_results | 有效性统计 |
  | get_market_overview / get_regime / get_index_valuation | market_service 等 | 市场环境 |
  | get_otc_trade_status(codes) | OtcTradeStatusService | 申购可执行性 |
  | get_holding_overlap / get_exposure_tags | 既有路由服务 | 组合重合度/赛道暴露 |
  | get_analysis_stats(days) | AnalysisResult 聚合 | 池级评分/信号分布、翻转次数 |
- 只读铁律：不注册任何写库/触发分析工具（分析执行仍走现有页面/调度）。

### 3.4 因子与策略效果诊断（新增 backend/ai/factor_audit.py，纯 Python）
- 输入：AnalysisResult 历史（factor_scores JSON、score、signal）× 后续净值（fund_nav/原始 API 已有）。
- 指标：逐因子 IC/RankIC（日截面 vs T+5/T+20 前瞻收益）、IR（IC 均值/标准差）、五分位年化、信号胜率/盈亏比/平均持有收益（按方向、按强度档）、whipsaw 率、动态阈值触碰统计、质量警告组 vs 对照组收益差。
- 输出：`FactorAuditReport` 结构（可 JSON 导出），Agent 任务 `factor_audit` 将其喂给 LLM 产出人话诊断（哪些因子失效/共线、权重调整方向、阈值敏感性）。
- 前置 schema 补齐（P0）：`AnalysisResult` 新增可空列 `original_score`、`quality_warnings(JSON)`、`dynamic_buy_threshold`、`dynamic_sell_threshold`，分析落库时写入 — 冷启动回算 + 增量精确并行。

### 3.5 持仓与调仓分析（新增 models/user_position.py + backend/ai/rebalance.py）
- 新表 `user_positions`：fund_id/code、shares、cost_nav、updated_at、source(manual|import)；API CRUD + CSV 导入（兼容支付宝/天天基金导出列名探测）。**不接入第三方账户同步**（防封禁与合规）。
- `RebalanceService.analyze()`：确定性打分 → 候选四清单：
  - 减仓/卖出：低分且（因子恶化 或 该信号历史胜率低 或 触发质量警告）
  - 买入：高分且 场外申购可执行（复用 otc_pause_veto 逻辑）且 与现有持仓 overlap 低
  - 换仓配对：同赛道内"卖出候选×买入候选"按 factor_audit 加权重排
  - 观望：阈值边界带（±0.5）与翻转高发的基金，标注确认天数建议（承接上轮"2-3 日确认"结论）
  - 组合层约束：赛道集中度（exposure_tags）、QDII 占比、永赢系双胞胎重合度（holding-overlap 数据）
- LLM 角色：解释"为什么换、换成什么、代价（费率/赎回到账/跟踪误差）"，生成调仓工单 markdown。

### 3.6 每日 AI 简报 + 调度（scheduler 扩展）
- 新增任务类型 `ai_daily_brief`：分析完成后触发 agent_runner（固定任务 prompt + factor_audit/rebalance 结果），产出简报走既有 feishu 推送；Schedule 表可配开关/时间，失败静默不影响主分析。

### 3.7 Skill / 外部 Agent 扩展
- AISkill 升级：`ai_skills` 表加 `tool_spec(JSON, 可空)` — 填了即注册为 agent 可调用工具（skill 内部可引用内置工具与模板），空则保持现有提示词注入模式，完全向后兼容。
- 预留 `ToolSource` 抽象，P4 评估 MCP 客户端接入（外部 agent 作为工具源），本期只留接口不实现。

### 3.8 前端（frontend/src/）
- 新增页面「AI 工作台」（路由 /ai-workbench）：会话列表 + 主区流式 markdown 渲染（react-markdown）+ **Agent 过程时间线**（工具调用折叠卡片：入参/行数/耗时）+ 三个一键任务按钮（因子诊断 / 调仓分析 / 今日简报）。
- AIChatWidget 升级为消费同一 SSE 端点（保留快捷问）；持仓录入/CSV 导入页；调仓建议结果页（表格 + 理由展开 + 导出）。
- 遵循项目铁律：改动须浏览器实测取证后才提交。

## 4. 开发计划（确认后执行）

| 阶段 | 内容 | 验收 | 预估 |
|---|---|---|---|
| P0 数据地基 | AnalysisResult 4 新列+落库+启动回算迁移；user_positions 表+CRUD+CSV 导入 | pytest 全绿；前端持仓录入可浏览器实测 | 0.5-1d |
| P1 Agent 内核 | llm tool-calling/stream 抽象 ×4 provider、agent_runner、tools registry+首批 12 内置工具、SSE 端点、feature flag | mock-LLM 工具链端到端测试、真实 DeepSeek 冒烟 | 2-3d |
| P2 因子诊断 | factor_audit 统计引擎（IC/胜率/whipsaw/质量过滤）、诊断 agent 任务、导出 | 已知合成序列断言指标值；259 行历史真实回算报告 | 2-3d |
| P3 调仓分析 | rebalance 引擎+组合约束、调仓 agent 任务、持仓页/结果页 | 单测+浏览器实测；对 09-22 导出集人工核对建议合理性 | 2-3d |
| P4 闭环 | AI 每日简报调度+飞书推送、AISkill-as-tool、工作台页收尾、README/ARCHITECTURE 同步 | 全量回归+连续 2 日调度实跑观察 | 1-2d |

合计约 8-12 个开发日；每阶段独立可交付、独立提交（type(scope): 中文），推送仅在"提交推送"指令时执行。

## 5. 测试策略

- LLM 层：`_FakeProvider` 脚本化 tool_calls 序列 → 断言 agent 循环/截断/护栏/落库；真实模型仅冒烟（不进 CI）。
- 统计引擎：合成数据黄金用例（完全正相关因子 IC≈1、随机因子 IC≈0.5 衰减、翻转序列 whipsaw 计数）。
- 工具层：每工具喂 fixture service 返回值，断言 schema 与行数裁剪。
- API/SSE：httpx AsyncClient 断事件序列；前端 vitest 不做（项目无此基设），以浏览器实测截图代替。
- 回归基线：当前 313 pytest 全绿，每阶段结束时不得回退。

## 6. 边界与不做项

- 不做自动下单/交易执行，输出仅为建议与工单。
- 不做外部账户 API 同步（仅手动/CSV）。
- 不引入鉴权/端口收敛（用户既定决策）。
- 不删任何既有限频/sleep/退避逻辑；agent 工具复用现有数据服务即自动继承防封策略。
- LLM 不执行数值计算（防幻觉），一切指标 Python 产出。
