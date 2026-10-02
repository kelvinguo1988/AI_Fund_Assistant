# 第二批量化口径决策方案（2026-10-02）

> **本文只做设计与取证，不含任何实现**。第一批（#53–#61）已改完并通过 499 项测试 +
> 浏览器实测；第二批全部涉及**信号口径变更**——一旦上线，历史 `analysis_results`、
> 回测结论、飞书推送与建议命中率都会与新口径不一致，因此必须先由你确认方向再动手。
>
> 每条给出：**现状证据（file:line，均已在本地源码/库中逐行核对）** → **选项** →
> **建议** → **信号漂移影响** → **回滚路径**。
>
> 红线遵循：以下任何方案都不放松防封禁措施（限流/抖动/退避/UA 轮换/冷却/熔断/翻页上限）；
> 凡需要新增上游请求的方案（Q11 全收益基准）单独标注预算影响。§3.5 历史告警行清理未动。

---

## 0. 结论速览

| 编号 | 问题（审查报告出处） | 建议 | 是否改生产信号 | 是否动前端 | 需你拍板 |
|---|---|---|---|---|---|
| Q1 | 五档阈值配置对实盘无效但可编辑，且进 AI 提示词（§3.3 P1） | **B（让它真生效于文案/仓位）+ 修提示词来源** | 是（advice/equity 文本，方向不变） | 是（加"仅影响建议措辞"说明） | ✅ |
| Q2 | 市场三因子既进加权和又调阈值 → 同一件事计两遍（§3.7 P1） | **只调阈值，退出加权和**（权重 0.8/0.5/0.5 → 0，保留因子给展示） | 是（全池分数下移 ≤1.8） | 是（因子页标注"仅参与阈值/展示"） | ✅ |
| Q3 | 动量族 = 同一信号计四遍，3.4/8.3=41%（§3.7 P1） | **加因子簇权重上限 35%（≈2.9）；trend_consistency 移出加权和改符号位** | 是 | 是（配置页加簇字段） | ✅ |
| Q4 | data_valid 已透传但权重不做覆盖率归一（§3.7 P1 后半） | **覆盖率 ≥60% 才评分；阈值按有效权重折算（复用 total_weight 折算机制）** | 是（新基金更容易出信号） | 是（卡片显示覆盖率） | ✅ |
| Q5 | 截面分依赖"池里恰好有谁"，不可跨期比较（§3.2-2） | **显式标注"池内相对分"+ 落库池规模**，不做绝对化改造 | 否 | 是（文案） | ✅ |
| Q6 | 回测无信号日强制回 50% 仓位；excess_return 主要衡量半仓（§3.4-1 / §3.7 P1） | **沿用最近信号仓位 + 三条基线 + 样本量下限** | 是（回测结论重写） | 是（图表加基线） | ✅ |
| Q7 | 建议自进化用绝对涨跌判命中，bench 恒传 0.0（§3.7 P1） | **改超额命中 + 固定 30 交易日窗口**；review 同向率同步改 | 是（校准输入） | 否 | ✅ |
| Q8 | 棺材钉恢复窗口不完整时也否决（§3.7 P2） | **恢复期未走完则暂不判定**（不新增否决） | 是（放开最近暴跌候选） | 否 | ✅ |
| Q9 | 无净值新鲜度门槛，陈旧净值可拿到今天的信号（§3.7 P2） | **超 N 个交易日硬否决 + 落库 as-of 日期** | 是 | 是（详情页显示 as-of） | ✅ |
| Q10 | 调仓权重用成本价 + `1.0` 哨兵（§3.7 P2）；无持有期/赎回费约束（§3.5） | **市值权重 + 去哨兵 + min_holding_days 阶梯赎回费** | 是（四清单权重与卖出建议） | 是（持仓表单加首次买入日） | ✅ |
| Q11 | 复盘基准是价格指数；场外复盘/回填仍用未复权净值（§3.7 P2 + 第一批 #56 遗留） | **三条链路统一复权口径 + 基准加回股息或用全收益** | 是（复盘数字变小） | 是（口径说明头） | ✅ |
| Q12 | factor_audit：重叠前瞻收益 + IR 未年化 + 无多重比较校正 + 无样本外（§3.7 P2） | **非重叠采样 + IR 年化标注 + Bonferroni 提示** | 否（只影响诊断结论） | 是（诊断表加口径列） | ⬜ |
| Q13 | P3 死配置与口径不一致打包（§3.7 P3） | **一次清扫**：signal_rules 死配置、MACD 文档、两种分位定义、size_stability 量纲、`pb` 字段 | 否 | 是（因子页禁编辑死字段） | ⬜ |
| Q14 | 工程闸门：CI 无一条 `run:`、无 ruff/mypy/eslint/pre-commit（§体检基线） | **先补"测试+构建"两条 job**，再考虑 lint | 否 | 否 | ⬜ |

推荐实施顺序：**Q6 → Q7 → Q8 → Q9 → Q10 → Q11 → Q4 → Q1 → Q2 → Q3 → Q5 → Q12 → Q13 → Q14**。
理由：Q6–Q11 属于"验证口径自身有问题"，先把尺子校准，再调因子（Q1–Q4），否则调参结论
建立在被污染的指标上；Q5/Q12–Q14 是标注与卫生项，随时可做。

---

## 1. 事实基线（只读查库与读码取证）

### 1.1 当前因子权重表（`data/fund_quant.db` → `factors`，status='active'）

| 因子 | 权重 | 标准化 | 输入维度 | 备注 |
|---|---|---|---|---|
| short_momentum | 1.2 | 截面 z | mom20 | `factor_engine.py:510-525` |
| mid_momentum | 1.2 | 截面 z | mom60 | `factor_engine.py:528-542` |
| inv_volatility | 1.0 | 截面 z | 60 日 std | `factor_engine.py:343-360` |
| return_risk_ratio | 0.8 | 截面 z | mean/std(60) | `factor_engine.py:575-594`，与 inv_vol 共用同一个分母 |
| drawdown_recovery | 0.8 | 规则（绝对） | nav/rolling_max(252) | `factor_engine.py:545-572` |
| market_valuation | 0.8 | 规则 | regime 快照 | `factor_engine.py:685-704`，**全池同分** |
| momentum_accel | 0.5 | 截面 z | mom20 − mom60 | `factor_engine.py:597-617` |
| trend_consistency | 0.5 | 截面 z | (sign(mom20)+sign(mom60))/2 | `factor_engine.py:620-645` |
| macd_signal | 0.5 | 规则（绝对） | dif/dea | `factor_engine.py:400-435` |
| market_sentiment | 0.5 | 规则 | 涨跌家数比 | `factor_engine.py:707-725`，**全池同分** |
| market_fund_flow | 0.5 | 规则 | 两融 7 日变化 | `factor_engine.py:728-746`，**全池同分** |

**∑ = 8.3**（与钳位 ±8.5 一致）。按构造分三类：

- 截面相对：6 个，权重 **5.2**（63%）→ 池内零均值，不表达绝对好坏
- 绝对规则（个基）：`drawdown_recovery` + `macd_signal` = **1.3**（16%）→ 唯一能把单只基金打到负分的个基因子
- 市场同分：3 个 = **1.8**（21%）→ 对全池加同一个常数

### 1.2 阈值与配置（生产库实查）

- `system_config.quality_filter_config` = `{"base_buy_threshold":1.5, "base_sell_threshold":-1.5, "threshold_ref_total_weight":8.3}`
  → 阈值折算 `scale = total_weight / 8.3 = 1.0`（`quality_filter.py:716-719`），当前不缩放，但**已启用**：后续改因子权重，阈值会等比跟随。
- `system_config.scoring_thresholds` = 五档（3.0 / 1.5 / −1.5 / −3.0 / −8.5），**生产无人消费其 direction/strength/equity**（见 Q1）。
- 生产唯一评分路径：`analysis_service.py:554-559` → `compute_with_quality_filter`。
- 回测费率 `backtest_fee_pct` 未配置 → 默认 0.6（`backtest_service.py:29`）。

### 1.3 样本统计（决定"改口径能验证到什么程度"）

- `funds`：active **57** 只。
- `analysis_results`：**123 行 / 6 个 analysis_date**，跨度 **2026-05-23 ~ 2026-07-22**。
  最新一天 22 行：buy 4（2.00~2.65）、hold 18（−0.50~1.15）、**sell 0**。
  全样本分位分布最低 −4.5，≤ −1.5 的历史行共 19 条（≈15%）→ 卖出侧历史上可达，
  但**近端样本里不可达**，任何阈值调参都缺近期弱势市证据。
- `backtest_results`：**0 行**；`advice_log` / `advice_outcomes`：**0 行**；`user_positions`：**2 行**（均填了 cost_nav）。
  → Q7/Q10 的口径改动**不会推翻任何已产出结论**（库里没有结论），但也**无法立刻用数据验证**，
  需要 §3 的影子评分积累样本。

### 1.4 第一批已修，勿再重复决策

| 审查条目 | 状态 |
|---|---|
| §5 场外净值未复权（因子链） | ✅ 已改分红复权（`akshare_adapter.py:615-631`），**但复盘/建议回填链路仍是单位净值** → 见 Q11 |
| §3.7 估值分位恒为空（csindex 100 行 < 250 门槛） | ✅ 主源改乐咕月频 + 60 点门槛 + 日历 10 年窗口（`market_regime_service.py:75-78,199-231`），实测已产出真实分位（0.5833 / PE 12.48 / 120 样本） |
| §3.7 `data_valid` 各出口丢失 | ✅ 已全链路透传（落库 JSON/API/推送/AI 上下文/`quality_filter.py:656` 不再洗回 True），**剩下的只有权重归一** → Q4 |
| §3.7 亏损基金年化"—" | ✅ 已修（`fund_compare_service.py:34-47`） |
| §3.7 / §5 单只无数据降级整源、市场概况缓存被 None 毒化、阶段涨幅部分成功覆盖、估值/申购无失败冷却 | ✅ 已在 #57/#58 处理 |

---

## 2. 逐项方案

### Q1【P1】五档阈值：删掉，还是让它真生效？

**现状证据**

- 生产路径只用 `compute()` 的 `weighted_score/raw_score`：`scoring_engine.py:227-233`；
  `weighted_score` 仅是加权和钳位（`scoring_engine.py:148`）。
- `compute()` 依五档算出的 `direction/strength/advice/equity` 被整体丢弃
  （`scoring_engine.py:241-254`），最终方向来自 `determine_signal`（`quality_filter.py:743-782`），
  权益仓位来自硬编码 `equity_map`（`scoring_engine.py:249-254`）。
- 五档唯一消费者：`routers/system_config.py:127-208`（读写自身）、`database.py:529,772`（迁移/种子）、
  **`ai_service.py:219-227`（把五档拼进系统提示词【评分阈值配置】）**。
- 前端 `frontend/src/pages/ScoringConfig.tsx`（205 行）+ 路由 `/scoring`（`App.tsx:60,245`）
  可编辑 5 档的 `min_score/label/operation_advice/equity_ratio`，页面**无任何"此项不生效"提示**。
- `analysis_service.py:489-490` 注释写着"真正生效的是五档 scoring_thresholds"——与实现相反。

**选项**

- **A. 删**：移除 `/api/system/scoring-config` 路由、`ScoringConfig.tsx` 页面与 `/scoring` 路由、
  `database.py` 五档种子、`ai_service.py` 的【评分阈值配置】段；唯一阈值入口收敛到"质量过滤配置"页的
  `base_buy_threshold / base_sell_threshold`。
- **B. 真生效（推荐）**：方向/强度仍由动态阈值决定（这是历史根因修复的资产，不能回退），
  但**建议文本与权益仓位改由五档渲染**：
  1. `compute_with_quality_filter` 里用五档按 `adjusted_score` 选 tier，取 `label/operation_advice/equity_ratio`；
  2. 若 tier 的 `signal_direction` 与动态阈值给出的 direction 冲突，**以 direction 为准、tier 只提供措辞**，
     并把冲突记进 `quality_warnings`（可配开关）；
  3. `equity_map`（`scoring_engine.py:249-254`）改为读 tier 的 `equity_ratio`；
  4. `ai_service.py:219-227` 改为提示"档位仅供参考，实际买卖由动态阈值 + 质量过滤决定"，
     并追加每只基金落库的 `dynamic_buy_threshold/dynamic_sell_threshold`（字段已在 #35 落库）。
  5. 修 `analysis_service.py:489-490` 注释。
- **C. 折中**：只做措辞（B 的 1、2、4、5），`equity_ratio` 保持硬编码。

**建议**：**B**。理由：五档是你唯一"看得见、改得动"的语义层（强烈加仓/适度减仓），删掉后
前端只剩一堆数字阈值，可读性反而下降；而 B 的风险面小——direction/strength 不变，
只有 advice 文案与 equity 数字改变。

**信号漂移影响**

- `signal_direction / signal_strength`：**不变**（决策仍走动态阈值）。
- `operation_advice` 文本：会变。例如 score=2.0 且动态 buy=1.5 时，现文案"建议加仓…50%"→
  按五档 1.5 档变"建议适度加仓，权益仓位可升至 70%"；若该基金触发规模冲击+漂移（buy=3.5），
  score=2.0 实际 direction=hold，但五档仍会说"适度加仓" → **这就是冲突项 2 必须存在的原因**。
- `equity_ratio`：现硬编码映射（heavy_buy 0.9 / moderate_buy 0.7 / hold 0.5 / moderate_sell 0.3 / heavy_sell 0.1）
  与五档默认值**恰好相同**（`scoring_engine.py:31-75` vs `:249-253`）→ 若你不改五档数字，equity 零漂移；
  改动即生效。
- AI 解释质量：提示词来源修正后，LLM 不再拿死配置解释真实信号（这是本条最大收益）。

**回滚路径**：新增开关 `quality_config` 键 `tier_advice_enabled`（默认 0=旧行为），
一行 if 即可回旧文案；前端提示与注释属纯文本，无需回滚。

---

### Q2【P1】市场三因子：进加权和 vs 只调阈值（当前两头都占）

**现状证据**

- 加权和侧：`market_valuation` 0.8 + `market_sentiment` 0.5 + `market_fund_flow` 0.5 = **1.8**，
  三只都只读 regime 快照（`factor_engine.py:692/713/734`），不读 `fund_data` → 全池同分；
  同一快照由 `analysis_service.py:507-511` 注入每一只基金。
- 阈值侧：`compute_dynamic_thresholds` 用同一个 `valuation_percentile`：≥0.85 → buy **+1.0**，
  ≤0.15 → buy **−0.5**（`quality_filter.py:729-738`）。
- **同一信号、同一方向、被计两次**。极端低估日：市场分最高 **+1.8**，同时买入门槛降 **0.5** →
  相对难度变化 2.3；极端高估日：市场分最低 −0.8（valuation −1.0×0.8，另两只至多 0），
  同时门槛升 1.0 → 收紧 1.8。
- 而 1.8 > `base_buy_threshold` 1.5（`quality_filter.py:62`）→ 中位数基金（截面部分≈0）
  在市场顺风日只靠 `drawdown_recovery`(+0.8，新高时满格) + 偏置(+0.5) 就能越过 1.5，
  出现"整池一起加仓"。
- 代码注释表明"希望市场参与决策"是有意设计（`factor_engine.py:649-666`、`quality_filter.py:1000-1017`），
  所以这是**口径选择**，不是 bug。

**选项**

- **A. 市场因子退出加权和（推荐）**：把三只 market 因子 `weight` 置 0（DB 值，不改代码即可灰度），
  择时职责完全交给 `compute_dynamic_thresholds`；因子本身保留（`raw_value/score` 仍落库供展示与诊断），
  前端因子页显示"仅用于市场环境展示与阈值调节，不计入总分"。
- **B. 保留加权和，删阈值调节**：删 `quality_filter.py:729-738` 那段（含 `extreme_*` 四个配置键），
  市场只影响分数不影响门槛。
- **C. 只对最终分做截面标准化后再套个基阈值**：把"市场常数"在截面化时消掉，等价于 A 但改动更隐蔽。
- **D. 保留双重计入，但把阈值调节改成非对称**（只在高估时收紧，低估时不加分不降门槛）。

**建议**：**A**，并顺手把 `extreme_high/low` 两个 increment 按"去掉 1.8 后重新标定"：
现值 1.0/0.5 是在"分数已被市场推着走"的背景下拍的，A 之后门槛是唯一的择时通道，
建议先保持 1.0/0.5 观察，再用 Q6 的回测基线复核。

**信号漂移影响（最大的一条）**

- 全池分数**系统性下移**：市场三因子满分时 −1.8，中性时 0，最低时 +? （valuation 负向：高估 −0.8，
  情绪/资金面 −1.0 → 最低 −1.8）。即改动后每日全池分数相对现值偏移 −1.8 ~ +1.8 不等。
- 因为 1.8 是常数（同分），**池内排序完全不变** → 相对排名、quintile、RankIC 不受影响；
  变的只是"与 ±1.5 绝对门槛比较"的结果。
- 预期效果：买入数量下降（顺风日不再白送 1.8），卖出数量上升（逆风日不再被 1.8 托住）；
  "全池一起买/一起卖"的羊群日显著减少。参考 §1.3 最新一天：4 buy / 18 hold / 0 sell →
  改后 buy 数很可能降到 0~2（因为当前 4 个 buy 的分数 2.0~2.65，减掉市场顺风部分后可能掉到 1.5 以下）。
- **这是本批次最需要你确认的实质风险**：信号数量与分布都会变，历史 `analysis_results` 与新记录不可比。

**回滚路径**：A 只需把 DB 里三只 market 因子的 `weight` 改回 0.8/0.5/0.5（纯配置，秒级回滚，
无需发版）；`threshold_ref_total_weight` 需同步从 8.3 改到 6.5（否则 scale<1，阈值一起缩，
等于变相放宽）。**建议把"market 权重 + ref 权重"当成一组配置一起改，并写入 CHANGELOG。**

---

### Q3【P1】动量族是同一个信号计了四遍

**现状证据（代数关系）**

- `short_momentum = P[-1]/P[-21] − 1`（`factor_engine.py:522`）
- `mid_momentum = P[-1]/P[-61] − 1`（`factor_engine.py:540`）
- `momentum_accel = mom20 − mom60`（`factor_engine.py:613-615`）**恒等于 `short_momentum − mid_momentum`**
- `trend_consistency = (sign(mom20)+sign(mom60))/2`（`factor_engine.py:641-643`）= 同两个输入的确定性函数
- 权重 1.2 + 1.2 + 0.5 + 0.5 = **3.4 / 8.3 = 41.0%**；
  另 `return_risk_ratio`（`:593`）与 `inv_volatility`（`:360`）共用同一个 60 日 std 分母，再叠 1.8。
- `quality_filter.py:665-674` 还会在超额持续性=1 且 trend_consistency 满格时把 trend 权重抬到
  `max(配置, 0.8)`，即动量簇实际暴露最高可达 3.7。

**选项**

- **A. 直接停用 `momentum_accel` 与 `trend_consistency`**（weight=0），动量只留 short/mid（2.4）。
- **B. 正交化（推荐）**：`momentum_accel` 保留（它确实是 short/mid 之外的独立轴——斜率差），
  但改为**对 short/mid 做截面回归取残差**（把 accel 中与 level 相关的部分剔掉）；
  `trend_consistency` 降权到 0.2 或转为"否决位"（符号分歧时把总分乘 0.8，而不是再加一份分）。
- **C. 簇权重上限**：给因子配置加 `cluster` 字段（momentum / volatility / market / absolute），
  `build_result` 计算 total_weight 时按簇裁剪（每簇 ≤ 总权重 35% = 2.9）。
- **D. 什么都不改，只在文档与前端标注"实质是动量策略"**。

**建议**：**C 为主 + A 的一半**——先加簇上限（结构性防线，一劳永逸），并把
`trend_consistency` 从加权和里移出改为符号过滤位；`momentum_accel` 在簇上限内保留。
B 的正交化数学上最干净，但需要截面回归实现 + 样本 ≥ 20 才稳，池内只有 57 只且覆盖率不齐，
建议作为 B 方案的第二步。

**信号漂移影响**

- 动量簇从 3.4 降到 ≤2.9（簇上限 35%）→ 动量反转期分数波动变小，但**趋势市里买入信号变少**
  （当前强势基金靠 short+mid+accel 同向叠加最多拿 2.9 分，减到簇内上限后仍在，差别主要在与
  volatility 簇冲突时不再被动量挤占）。
- `trend_consistency` 改乘性位后：一正一负（consistency=0）的基金从"少加 0 分"变成"总分 ×0.8"，
  对正分基金是 −20%，对负分基金反而是 +20%（更接近 0）→ **卖出信号会减少**，需要与 Q2 一起评估，
  避免两个改动同时压制卖出。
- 截面因子的 z-score 分布会变（ accel 残差化后其截面 std 重新估计），历史分数不可比。

**回滚路径**：簇上限设 `quality_filter` 键 `cluster_weight_cap_pct=0`（0=关闭，默认关闭上线，
观察一轮后再开）；trend 位改回加权和只需把 weight 恢复 + 删去乘性分支（单函数改动）。

---

### Q4【P1】data_valid：透传已完成，权重归一还没做

**现状证据**

- 20 处 `data_valid=False`（缺数据即中性 0）；`normalize_cross_sectional` 已把它们剔出标准化池
  （`factor_engine.py:903-906`）。
- **但加权求和按满权重**：`scoring_engine.py:142-143` `weighted_sum += score.score * weight`，
  0 分照样占权重；`dynamic_buy_threshold` 不做覆盖率缩放（`quality_filter.py:716-740` 只按总权重折算）。
- 后果算例（净值只有 60 个交易日的新基金）：
  有效 = short_momentum(需 22 根) 1.2 + drawdown_recovery(需 60) 0.8 + macd_signal(需 40) 0.5 = **2.5**；
  失效 = mid/accel/trend/return_risk/inv_vol(需 62~65) 共 4.7 + market 1.8（若 Q2 未改）→
  该基金理论最高加权分只有 ~2.5，而门槛 1.5 → **必须几乎满分开仓才能买入，且建议文本看起来毫无保留**。
- 目前 `factor_scores` JSON 已含 `data_valid`（`analysis_service.py:584`），报告侧已能出
  "以下因子数据不足"（`report_engine.py:262`）→ 缺的只是"按有效权重归一 + 覆盖率门槛"。

**选项**

- **A. 归一化**：`raw_score_effective = Σ(score×w) / Σ(w where data_valid) × Σ(w all)`
  （等价于把分数放大到满覆盖口径）→ 覆盖率越低放大越厉害（最坏 ×3.3）。
- **B. 缩门槛**：`buy_threshold × coverage_ratio`（数学上与 A 同向，但改动只落在阈值侧，
  且已有的 `scale=total_weight/ref` 机制天然可以复用：把 `total_weight` 传成**有效权重和**）。
  → **实现成本最低**：`quality_filter.py:993` 的 `total_weight=sum(...)` 改为
  `sum(w for f with data_valid)`，一行；`threshold_ref_total_weight` 保持 8.3 不动。
- **C. 拒绝评分**：覆盖率 < 60% 时不写 `analysis_results`，只记 `data_missing`（与第一批
  `_no_nav_reason` 同一风格，最保守）。
- **D. 只标注不修正**：把覆盖率显示到前端/AI/推送（现已透传），数字不动。

**建议**：**B + C 组合**：覆盖率 ≥0.6 → 用有效权重折算门槛（B）；<0.6 → 不评分并埋点（C）。
A 会让新基金分数被放大 3 倍，噪声同倍放大，不推荐。

**信号漂移影响**

- 方向：**新基金/停牌复牌基金更容易触发买卖信号**（门槛被折算降低），同时覆盖率 <0.6 的
  基金从"永远观望"变为"没有记录"。
- 数量：池内 57 只中净值不足 60 日的只数需要实测确认（第一批已把无净值基金整体跳过评分）；
  预计影响个位数基金，方向为 buy/sell 各增加。
- 与 Q2 冲突点：若 Q2 把 market 三因子权重清零，覆盖率分母也同时变小 → 两项必须**同一批次**
  上线，否则折算口径互相打架。

**回滚路径**：新增 `quality_filter` 键 `coverage_threshold=0`（0=关闭折算与拒绝），
默认关闭上线；关掉即回到现状。

---

### Q5【方法学，非缺陷】截面分是"池内相对分"，不能跨期比较

**现状证据**

- 6 个截面 z 因子（权重 5.2）的均值恒为 0，池子构成变化即分数变化；
  分数落库（`analysis_results.weighted_score`）、被回测复用（`backtest_service.py:208-226`）、
  被 AI 引用（`ai_service.py:194-208`）、被推送（`push_service.py`）。
- 小池退化已有告警（`factor_engine.py:919-927`，<5 只时 warning），但结论仍会落库。

**选项**

- **A. 只标注**：落库新增 `pool_size`（或塞进 `quality_warnings`）；前端/AI/推送文案统一写
  "池内相对分（当日 57 只）"。
- **B. 落库因子原始值**：`raw_value` 已在 JSON 里，可直接用 → 加一条"绝对口径重算"端点，
  需要历史净值可重放（数据源预算敏感）。
- **C. 改成全市场截面**：需扩池到数百只，取数量与限流风险大增。

**建议**：**A**。成本极低、消除误用，不触碰任何数字。C 与防封禁红线冲突（拉数面扩大数倍），不建议。

**信号漂移影响**：无（纯标注）。

**回滚路径**：文案回退。

---

### Q6【P1】回测：仓位延续、基线、样本下限、现金利息

**现状证据**

- 已正确的部分（勿动）：next-bar execution（`backtest_service.py:274-278,332-343`）、
  几何复利（`:339`）、`|Δ仓位|×费率` 成本（`:336`）、回撤几何口径（`:419-440`）、
  非交易日信号前向对齐（`:228-262`）。
- **问题 1**：`:326-330` 未命中信号日 `current_position = default_position(0.5)` 并成为次日仓位。
  分析不是每交易日都跑（失败/非交易日/手动），"漏一天分析"= 仓位被打回 50% 且产生一次换仓成本
  （`:336` 会按 |0.5−0.9|×0.6% 扣费）。
- **问题 2**：`excess_return = total_strategy_return − total_nav_return`（`:174-176`），
  基准是"满仓买入持有"，而策略大部分时间 50% 仓位 → 上涨市里负超额主要衡量的是半仓，不是信号能力。
- **问题 3**：`signal_count`（`:178`）把 hold 也计入；库里只有 6 个信号日 / ~250 交易日，
  却照样输出完整结论，无样本量下限。
- **问题 4**：现金部分按 0 收益（`:334` `strategy_daily = daily_return × position`），
  未计无风险利率；对比 `fund_compare_service.py:29` 的 `RF_ANNUAL=2.0` 口径不统一。
- **问题 5**（连带 §3.4-2/3）：回测起点=该基金首次被分析之日，池子非 point-in-time；
  用户"先看好才入池"→ 选择偏差，无法用代码消除，只能标注。

**选项**

- **A. 仓位状态机（推荐）**：`position = 最近一次信号对应的仓位`，直到出现更强调的信号或
  显式 `hold` 才改变；无信号日沿用（换手大幅下降）。可配 `carry_position=true/false`。
- **B. 三条基线**：`BacktestSummary` 增加
  `baseline_buy_hold`（=100% 仓位，即现 `nav_return`）、`baseline_static_half`（恒 50%，即"什么都不做"）、
  `excess_vs_static_half`（**新的头号指标**）。前端图表画三条线。
- **C. 样本量下限**：`signal_count_non_hold < MIN_SIGNALS(默认 8)` 或
  `信号覆盖天数/总天数 < 0.3` → 不返回结论，返回 `caveat`（或 `None` + 明确原因）。
- **D. 现金利息**：`(1−position) × rf_daily`，rf 取可配 `cash_yield_pct`（默认 1.8%，标注为近似）。
- **E. 选择偏差**：输出 `coverage_start_date/coverage_days/pool_size_at`，前端显示
  "回测区间自该基金首次被分析起，样本由入池时点决定"。

**建议**：**A + B + C + E**，D 作为可选项（默认 0=不计息，但**必须**在文案里写"不计息"）。
B 是本条的核心——把"超额收益"从半仓噪声里解放出来。

**信号漂移影响**（注意：这是**回测口径**，不改生产信号）

- A 之后策略仓位更连续 → 换手下降 → 成本扣减减少 → 策略收益**上升**（同信号集下）；
  同时暴露更久 → 最大回撤**变差**。两个方向都会改变结论，必须重跑全量回测建立新基线。
- B 之后 `excess_return` 的定义改变：现值 vs "买入持有"，新值 vs "静态 50%"。
  历史 `backtest_results` 表**当前 0 行**（§1.3），无兼容负担——这正是改动窗口。
- C 会让多数当前基金直接返回"样本不足"（6 个信号日）→ **短期内回测页基本没有可用结论**，
  需要你确认是否接受这个"诚实但难看"的结果。

**回滚路径**：`system_config.backtest_carry_position`（默认 1）、
`backtest_min_signals`（默认 0=不拦），两条配置即可回到今日行为；
新指标是**增加字段**（`baseline_*`），前端读不到就不画，天然向后兼容。

---

### Q7【P1】命中率口径：学到的是 beta，不是信号质量

**现状证据**

- `advice_learning_service.py:120-128`：`hit = fund_change > 0`（buy）/ `< 0`（sell），
  **绝对涨跌**判命中；`bench_change` 是形参但调用方传字面量 0：`routers/analysis.py:471`。
- 评估窗口不是 30 天：`pending_evaluations` 以 30 自然日为"到期"（`:25,104-118`），
  但 `routers/analysis.py:461` 取 90 天净值序列，`:467` 过滤"建议日之后全部"，
  `:470` 用首末两点 → 实际窗口 = 建议日 → 最新净值日（30~90 天不等，且同一批样本窗口长度不齐）。
- `calibrate()`（`:172-199`）据此改写 `stop_loss_pct/profit_take_pct`，窗口/样本数已修
  （近 `CALIBRATION_WINDOW_DAYS` 天、≥`CALIBRATION_MIN_SAMPLES` 条），但**命中定义仍被市场涨跌污染**。
- 同一病根的第二处：`review_service.py:153-169` 的"同向率"也是绝对涨跌。
- 该统计会被 AI Skill 当"已验证结论"呈现（`ai_tools/skill_tools.py`）。
- 库现状：`advice_log/advice_outcomes` **0 行** → 改口径无历史包袱。

**选项**

- **A. 超额命中**：`hit = (fund_change − bench_change) > 0`（buy）/ `< 0`（sell），
  bench 用真实基准（沪深300 同区间，见 Q11 关于价格/全收益口径的说明）。
- **B. 固定评估窗口**：`routers/analysis.py` 改为"建议日 + 30 个**交易日**"，
  窗口未走完的样本跳过（`pending_evaluations` 用交易日历判定），基准与基金同区间。
- **C. 双口径并存**：同时存 `hit_abs` 与 `hit_excess`，校准只用 excess，UI 显示两个数字
  （便于你判断 beta 贡献 vs 选基贡献）。
- **D. 最低样本门槛**：窗口 `CALIBRATION_WINDOW_DAYS=90` 天、样本 `CALIBRATION_MIN_SAMPLES=30` 条
  （`advice_learning_service.py:27,29`，棘轮问题已在 2026-09-29 审查中修为"近窗口 + 可回退"），
  建议再加"同 action 且同窗口长度一致"的约束，避免混窗校准。

**建议**：**A + B + C**（C 的成本只是多一列），D 视样本积累速度。
注意 A 依赖基准数据可得性：基准序列走 `adapter.get_benchmark_series()`（1h 缓存），
**不新增上游请求**（回填时已经在打 `_fetch_nav_series`，基准走缓存）。

**信号漂移影响**

- 不改生产买卖信号，只改**自进化闭环的输入**。上涨市里 buy 命中率会从现在的接近 100% 掉到
  ~50-60%，`calibrate()` 因此不再被单侧推动 → 止损/止盈线更稳定。
- 历史 0 行 → 无跨期比较问题；但**新口径需要重新积累 ≥20 条**才会开始校准（30 交易日窗口，
  意味着约 1.5~2 个月才有一次有效校准），要有预期。

**回滚路径**：`hit` 定义收在一个纯函数里（建议新建 `_judge_hit(action, fund_chg, bench_chg, mode)`），
`mode` 走 `advice_calibration` 表键 `hit_mode`（`abs` / `excess`），默认 `excess`，改回 `abs` 即回滚。

---

### Q8【P2】棺材钉：恢复窗口不完整时也生效

**现状证据**

- `quality_filter.py:192-243`：`prices = close_history[-252:]`，`for start in range(n - consec)`
  （consec=20），`recovery_start = start + consec`，`end_idx = min(recovery_start + 60, n)`。
  当 `start` 接近序列末尾时，`end_idx − recovery_start` 可以只有 1~几天，
  但判定仍是 `max_future < peak × 0.90` → **昨天刚暴跌的基金被按"60 日未恢复"否决**。
- 影响方向恰好最糟：最近跌下来的（可能最便宜的）候选被整体剔除，而日志理由是"充分"的（`:236-240`）。

**选项**

- **A. 样本不足则暂不判定**：`if recovery_start + recovery_days > n: continue`（严格按定义）。
- **B. 临时否决 + 复核标记**：不足则写入 `quality_warnings`（"形态待确认：恢复期仅观测 X 天"），
  但不否决（`vetoed=False`）。
- **C. 按已观测天数折算阈值**（`recovery_pct` 随窗口线性放宽）——数学上任意，不推荐。

**建议**：**A 为主 + B 为展示**（否决判定按 A，同时在结果里标注"最近存在待确认的棺材钉形态"，
让调仓清单的观望位能看到原因）。

**信号漂移影响**

- 纯**放开**方向：原先被否决的"最近暴跌"基金重新进入评分池 → buy/sell 数量都可能上升。
- 只影响 `close_history` 长度 ≥252 且最近 80 日内暴跌 ≥20% 的基金；当前 57 只池里
  需要实测确认只数（建议实现时先跑一遍只读统计，把命中清单给你过一遍再上线）。

**回滚路径**：`quality_filter` 键 `coffin_nail_require_full_recovery_window`（0=旧行为），默认 1。

---

### Q9【P2】净值新鲜度门槛（陈旧净值不得拿到今天的信号）

**现状证据**

- `analysis_service.py:45-55` `_no_nav_reason` 只判"空序列"；全仓无任何地方把
  `fund_data.date`（`akshare_adapter.py:635-636` 已正确设为末值日期）与 `beijing_today()` 比较。
- 上游静默返回旧缓存 / 基金暂停披露时，会以 `analysis_date=今天` 写入陈旧信号，
  回测再把这段陈旧尾巴当真实交易日重放（`backtest_service.py:208-226` 照单全收）。
- 第一批 #57 已把"代码级无记录"区分出来（`NoDataError`），但"有记录且很旧"仍无防线。

**选项**

- **A. 硬否决**：最新净值日期落后 > N 个交易日 → 跳过评分 + `data_missing` 埋点（复用
  `_log_missing_nav`，无需新链路，**零新增请求**）。
- **B. 软标记**：照常评分但写 `quality_warnings`，前端/AI/推送显示 as-of 日期。
- **C. A + B 分档**：落后 ≤5 交易日标注；>5 否决（或 >10 否决，N 由你定）。

**建议**：**C，N=5 标注 / 10 否决**（QDII/海外基金 T+2 披露很常见，N 太小会误杀；
实现时须按交易日历而非自然日，且**优先用库里已有的节假日表**，避免为此新增上游请求）。

**信号漂移影响**

- 减少信号（QDII/停牌基金在披露停摆期不再产出建议）；落库 `analysis_results` 行数下降。
- 对回测：陈旧尾巴不再进入信号集 → 回测更真实但样本更少（与 Q6-C 叠加，注意"样本不足"更常见）。
- `analysis_results` 建议同时落 `nav_as_of_date` 列（字段新增，向后兼容）。

**回滚路径**：`quality_filter` 键 `nav_staleness_max_trading_days=0`（0=关闭）。

---

### Q10【P2 + §3.5】调仓权重与可执行性的另一半

**现状证据**

- `ai/rebalance.py:196-208`：
  `weights[fid] = p.shares * p.cost_nav if p.cost_nav else 1.0`（`:200`）→ 归一后成为所有
  `weight_pct`（`:243`）与组合约束（主题集中度 ≥30% = `BOUNDARY_WEIGHT_PCT` `:40`）的输入。
  哨兵 `1.0` 与 `shares×cost_nav`（当前库：12000×1.35=16200、8000×1.12=8960）**量级差 4 个数量级**
  → 漏填成本的持仓归一后权重≈0.006%，而不是"等权"。
  且 caveat 只在**全部**持仓都缺成本时才加（`:203-204`），混合场景静默失真。
- 用的是成本市值，不含浮盈浮亏 → 集中度/QDII 告警与展示都可能错。
- **§3.5 的另一半**：全仓无 `min_holding_days` / 阶梯赎回费逻辑
  （`grep holding_days|赎回费|redemption` 后端 0 命中）；买入侧已有
  `apply_otc_trade_constraint`（`quality_filter.py:785-811`）与 `otc_pause_veto_buy` 开关，
  卖出/换仓侧完全没有成本约束。
- `models/user_position.py:22-38`：只有 `shares/cost_nav/source/created_at/updated_at`，
  **没有首次买入日期**（覆盖式更新，`created_at` 只是"首次录入系统"的时间）。

**选项**

- **A. 市值权重**：`shares × 最新单位净值`（净值已在分析链路里，走缓存即可，**注意别新增逐只请求**——
  建议复用 `Fund.latest_nav` 或已有的实时估值链路 `fund_realtime_service`）；
  缺失时才回退成本市值，并逐只标注"市值未知，按成本估算"。
- **B. 去哨兵**：缺 `cost_nav`/市值时用**等权份额**（`shares / Σshares`）而不是 1.0；
  混合场景也走同一路径并加 caveat。
- **C. 持有期与赎回费**：`user_positions` 增列 `first_buy_date`（可空）；
  新增阶梯费率配置 `redemption_fee_ladder`（默认 `[[7,1.5],[365,0.5],[null,0.25]]`）；
  四清单在卖出/换仓项里输出 `holding_days` 与 `estimated_fee_pct`，
  当 `fee_pct > 预期收益改善` 时降级为"观望（持有不足 7 天，赎回费 1.5%）"。
- **D. 只做 B**（先把明显错的修掉），C 延后。

**建议**：**B 立即（纯 bug）+ A（口径更正，注意净值来源必须是已有缓存）+ C 分两步**：
先加列并在文案里显示"持有 X 天（未填首买日则为—）"，再引入阶梯费与降级。
红线提示：C 不产生任何上游请求；A 若要从实时估值取净值，必须走**已有的** `fund_realtime_service`
缓存，禁止为权重计算逐只拉净值。

**信号漂移影响**

- 不改买卖信号本身，改的是**四清单里的权重数字与集中度告警触发与否**，以及
  卖出/换仓建议是否被"成本过高"降级为观望 → 建议条数下降、更保守。
- `first_buy_date` 需用户补录；未补录时**必须显式标注"未知，未做持有期约束"**（不可默认 0 天，
  那会把所有建议都拦掉）。

**回滚路径**：`redemption_fee_enabled=0` 关闭 C；A/B 属单函数改动，git revert 单提交即可。

---

### Q11【P1，第一批遗留】三条链路的收益口径必须统一（含基准全收益）

**现状证据**

- 第一批 #56 只改了**因子链**：`akshare_adapter.py:615-631` → `FundData.close_history` 为分红复权序列，
  `close/date` 仍为官方单位净值（注释明示，量纲一致性有保证）。
- **回测**走 `adapter.get_fund_data` → `close_history`（复权）✓（`backtest_service.py:99-117`）。
- **复盘 / 建议回填仍用单位净值**：`review_service.py:261-299` 场外分支
  `raw = [(d, v) for d, v in zip(df["净值日期"], df["单位净值"])]`（`:289-292`），
  ETF 分支是 `adjust="qfq"`（`:274`）→ 同一张表里场内复权、场外未复权。
  `routers/analysis.py:461` 的回填复用同一函数 → 分红除权日被记成真实下跌。
- **基准是价格指数**：`review_service.py:173-187` 取 `get_benchmark_series()`（沪深300 点位），
  文案已诚实标注"（价格回报）"（`:196`），但 `excess_pct` 仍直接拿来判"跑赢/跑输沪深300"（`:202-207`）。
  沪深300 股息率约 2.7%/年 → 只跟踪指数的组合被白记约 2.7pp/年的"超额"。
- 结论：**#56 之后这个偏差方向变严重了**——基金侧已含分红、基准侧不含，
  复盘的"跑赢"数字比修复前更乐观（约 +2.7pp/年）。这条必须由你决定怎么处理。

**选项**

- **A. 统一复权**：`review_service._fetch_nav_series` 场外分支改取复权序列
  （复用 `build_forward_adjusted_nav`，或直接消费 `FundData.close_history`），
  与因子链/回测一致。ETF 分支保持 qfq。
- **B. 基准含股息**：三选一 ——
  ① 用全收益指数代码（如 H03003 沪深300 全收益，需确认数据源可得）；
  ② 价格指数 + 可配 `benchmark_dividend_yield_pct`（默认 2.7%/年，按区间天数折算）；
  ③ 只改文案："跑赢沪深300价格指数（未计股息，约 2.7pp/年）"。
- **C. 口径头统一**：所有含"区间收益/年化"的报告头部（复盘、对比、推送、回测）
  固定打印三行：净值口径（分红复权/单位净值）、基准口径（价格/全收益）、是否计息。

**建议**：**A + B② + C**。B① 需要新增一个上游接口与列语义校验（受 csindex 可用性影响，
§3.7 那条 100 行/证书问题的教训），预算与稳定性都 riskier；B② 用一个可配的股息率常数，
透明、零请求，且你能自己校准。

**信号漂移影响**

- 复盘/回填数字**变小**（基金侧加分红后其实变大；基准侧加回股息后超额变小——净效应：
  `excess_pct` 预计下降约 2.5~3pp/年）。
- 建议回填（Q7）的基准侧与本条同源，**两条要一起定**：若 A 改了场外序列，回填的
  `fund_change` 会自动含分红（这是对的），bench 用 B② 后的含息基准。
- 历史 `review` 报告（未落库，每次实时生成）会与新口径不可比；`analysis_results` 不受影响。

**回滚路径**：`benchmark_dividend_yield_pct=0` 即回到价格指数口径；
A 属正确性修复，不建议回滚（若必须，用配置 `review_nav_adjusted=0` 切回单位净值）。

---

### Q12【P2】因子诊断的统计口径

**现状证据**

- `ai/factor_audit.py:288` `horizons=(5,20)`；`forward_return`（`:41-52`）在**每个交易日**都算
  一个 horizon-20 前瞻收益 → 相邻样本重叠 19 天，IC 序列强自相关，而 `rank_ic_ir =
  mean/std`（`:137`）按每日计数 → horizon=20 的 IR 约被放大 √20 ≈ 4.5 倍。
- `MIN_CROSS_SECTION = 5`（`:30`）；`days=90`（`presets.py:97`）→ 实际独立周期约 4.5 个，
  11 个因子里挑"最优"接近挑噪声；诊断窗口与用户调参窗口是同一段数据（无样本外）。
- markdown 表（`:242`）按 `rank_ic_mean` 排序，无多重比较校正。

**选项**

- **A. 非重叠采样**：每 `horizon` 个交易日取一个截面（IC 序列长度从 ~60 降到 ~4~12，诚实反映样本）。
- **B. IR 明确口径**：新增 `ic_n_eff`（独立周期数）与 `rank_ic_ir_annualized`，
  表头写"IR 基于 N 个非重叠周期"。
- **C. Newey–West / block bootstrap**：更严谨但实现与解释成本高。
- **D. 多重比较校正**：对 11 因子同 horizon 做 Bonferroni/BH，表里加 `p_adj` 或直接标注
  "未达显著（m=11）"。
- **E. 样本外**：诊断窗口切 train/validate（例如前 60 天调参、后 30 天验证）。

**建议**：**A + B + D**（C 作为后续增强；E 需要更长历史，与 §1.3 的 6 个信号日现状冲突，暂缓）。

**信号漂移影响**：不改任何生产信号，只改**诊断结论的措辞**——原本"某因子 IR=1.2 建议加仓权重"
会变成"独立周期 4 个，未达显著"，从而抑制你基于噪声调权重的冲动。前端诊断表需加口径列。

**回滚路径**：`horizons` 与非重叠步长为函数入参，回退即恢复；建议加 `overlapping_ic=False` 参数开关。

---

### Q13【P3】死配置与口径不一致打包清扫

| 子项 | 证据 | 建议 |
|---|---|---|
| 截面因子的 `signal_rules` 是死配置 | 种子给 `short_momentum` 等写了规则（`database.py:67,81,105,118,132,146,174,189,204`），但 `calculate_short_momentum` 直接返回原始动量（`factor_engine.py:522-525`），分档由 `zscore_thresholds` 决定；`rules_from_params` 永不被调用 → 前端编辑无效果（与 Q1 同类） | 前端对 `normalization=cross_sectional_zscore` 的因子**禁用 signal_rules 编辑并显示"由 z 分档决定"**；DB 里把这几只的 `signal_rules` 清空 |
| MACD 文档 vs 实现 | `factor_engine.py:404` 称"金叉+放量→1.0"，实现只判 `dif>dea` 与 `hist_delta>0`（`:426-435`）；`volume_history` 被两个 adapter 赋值（`akshare_adapter.py:389`、`joinquant_adapter.py:134`）却**无任何因子/服务消费** | 改文档；`volume_history` 要么留作 ETF 量能扩展（加 TODO + 保留），要么连字段一起删（**当前无消费方，倾向删**） |
| 同一统计两种分位定义 | `index_valuation_service.py:96` 用严格 `<`；`market_regime_service.py:296` 用 `<=`；分区阈值也不同（30/70 vs 15/85） | 统一为一个 helper `percentile_rank_inclusive()` + 一处分区常量；两条路径的阈值差异**保留但改名**（"仪表盘低/高估"vs"策略极端低/高估"），避免误认为同一件事 |
| `size_stability` 量纲不一致 | `akshare_adapter.py` 取深交所"基金份额"（份），`factor_engine.py:494-499` 按"2亿~50亿元"判断；且仅 `159xxx` 可得 | 因子当前未启用（DB active 列表里没有）→ 先改成 `份额 × 净值` 或直接用库里的 `fund_size`，并在启用前跑一次覆盖率检查 |
| `FundData.pb` 口径错标 | `akshare_adapter.py:786/805/820` 取 csindex `市盈率2` 赋给 `pb`（`base.py:37` 注释"市净率"）；`grep \bpb\b` 显示**除赋值外无任何消费者** | P3：要么删字段（干净），要么改为 `pe_ttm` 另存并展示。我建议**删**，因为 `pe`（市盈率1）已喂给 `fed_model`（`factor_engine.py:282`），而 `fed_model` 当前未启用 → 保留 `pe` 但去掉 `pb` |

**信号漂移影响**：全部无（死字段/文案/未启用因子）。
**回滚路径**：git revert；`pb` 删除属字段移除，若有外部脚本依赖需先确认（仓内无依赖）。

---

### Q14【工程闸门，非量化，附议】

- `.github/workflows/` 只有 `docker-publish.yml`，**`run:` 出现 0 次** → CI 只构建镜像，不跑测试。
- 无 `ruff.toml` / `pyproject.toml` / `mypy.ini` / `.pre-commit-config.yaml` / eslint 配置；
  `frontend/package.json` scripts 只有 `dev/build/preview`（**无 lint**）。
- 现状代价已经在本轮显形：499 项测试全靠本地手动跑；`ai_service.py:489` 那类"注释与实现相反"
  没有任何自动防线。

**建议**：先补最小 CI（`pytest -q` + `npm run build` 两个 job，跑在 ubuntu，沿用现有
`requirements.txt`），**不加 lint/type-check**（一次性引入会产生上千条待修，与你的迭代节奏冲突）；
lint 作为 Q14b 单独决策。

**回滚路径**：删 workflow 文件即可，不影响本地与生产。

---

## 3. 上线方法：影子评分（强烈建议采纳）

第二批的共同风险是"改完无法证明变好"。而库里只有 **6 个信号日 / 123 行**，
无法支撑统计意义上的调参结论。建议**先做一层不改生产输出的影子对比**：

1. **影子字段**：新增 `analysis_results.shadow_score / shadow_direction / shadow_variant`
   （或一张 `analysis_shadow` 表），同一轮分析用两套口径各算一次，只写影子列，
   生产 `weighted_score/signal_direction` 保持旧口径。
2. **每日差异报表**：飞书/页面显示"今日口径分歧：N 只，其中旧 buy→新 hold 的有 M 只"，
   并按 `original_score / dynamic_buy_threshold`（已落库）分档统计分布迁移。
3. **切换判据**（建议明确写死，避免拍脑袋）：
   - 分歧比例连续 5 个交易日 < 15%，或
   - 影子口径在 Q6 新基线（vs 静态 50%）上的超额不劣于旧口径；
   满足后再把开关切到生产。
4. **成本**：影子评分只多算一次纯 Python 加权（无上游请求，符合红线）。

配套需要落库的元数据（Q5/Q6/Q9/Q12 都要用到）：`pool_size`、`nav_as_of_date`、
`factor_coverage`（有效权重和 / 总权重）。属**新增列**，向后兼容。

## 4. 批次划分（确认后按批开发，每批独立可回滚）

- **批次 2A（尺子先准）**：Q6 回测 + Q7 命中率 + Q11 口径统一（含 #56 遗留的复盘未复权）。
  纯"度量"改动，不动买卖信号 → 风险最低、收益最直接，且是后续所有调参的前提。
- **批次 2B（防线补齐）**：Q8 棺材钉 + Q9 净值新鲜度 + Q10 调仓权重与持有期。
  方向以"减少错误信号/减少失真权重"为主。
- **批次 2C（评分结构，需影子评分先行）**：Q2 市场因子 + Q3 动量簇 + Q4 覆盖率归一 + Q1 五档。
  **这一批会实质改变每日买卖数量**，必须走 §3 影子对比，且 Q2 与 Q3/Q4 需同批
  （`threshold_ref_total_weight` 与有效权重折算互相依赖）。
- **批次 2D（卫生）**：Q5 标注 + Q12 诊断口径 + Q13 死配置 + Q14 CI。

## 5. 需要你明确回答的 6 个问题（2026-10-02 已全部裁定）

1. **Q2**：市场三因子是否退出加权和？（这会让全池分数下移最多 1.8，近期 4 个 buy 信号很可能归 0）
2. **Q6-C**：样本不足（<8 个非 hold 信号）时是否**拒绝输出回测结论**？当前库状态下这会让回测页短期"无结论"。
3. **Q11-B**：基准股息用可配常数（默认 2.7%/年）还是尝试接全收益指数（需新增一个上游接口）？
4. **Q9**：净值陈旧否决的天数 N（建议 >5 标注 / >10 否决，QDII 需要宽松档）。
5. **Q10-C**：`user_positions` 是否加 `first_buy_date` 并由你补录 2 条现有持仓？没有它，阶梯赎回费只能显示"未知"。
6. **§3**：是否先做影子评分再切生产？（若否，2C 将直接改变推送与页面的信号，需你接受不可比区间）

### 5.1 裁定结果（用户 2026-10-02 逐项选定，均为推荐档）

| # | 裁定 | 落地要点（开发时照此执行，勿再解释成别的口径） |
|---|---|---|
| Q2 | **退出加权和，只调阈值** | 三只 market 因子 `weight=0`（DB，不改代码即可灰度），`raw_value/score` 继续落库供展示与诊断；**同时** `threshold_ref_total_weight` 8.3 → 6.5（两项必须成对改/成对回滚）；因子页与配置页标注"仅参与阈值/展示，不计入总分"；`extreme_high/low` 两个 increment 先保持 1.0/0.5，用 Q6 新基线复核后再提调整 |
| Q6 | **仓位延续 + 三基线 + 样本下限（A+B+C+E）** | `backtest_carry_position=1`；新增 `baseline_buy_hold` / `baseline_static_half` / `excess_vs_static_half`（头号指标改为 vs 静态半仓）；非 hold 信号 <8 或信号覆盖天数占比 <30% → 只出 `caveat` 不出结论；落 `coverage_start_date/coverage_days/pool_size_at` 并在前端写明"回测区间由入池时点决定"；现金利息默认 0，但文案必须写明"不计息" |
| Q9 | **>5 交易日标注 / >10 否决** | 按交易日历（优先用库内 `holiday_calendar`，**不新增上游请求**）；QDII/海外（含 96 开头互认）额外宽一档 +5；否决走既有 `_log_missing_nav` + `analysis.data_missing` 埋点；同时落 `analysis_results.nav_as_of_date`；回滚键 `nav_staleness_max_trading_days=0` |
| Q10 | **去哨兵 + 市值权重 + 持有期/赎回费（B+A+C）** | `ai/rebalance.py` 缺成本时改 `shares/Σshares` 等权并逐只加 caveat；市值权重 = `shares × 最新净值`，净值**只走已有 `fund_realtime_service` 缓存**，禁止为算权重逐只拉净值；`user_positions` 加可空列 `first_buy_date`（启动迁移 + 前端持仓表单加字段），阶梯费率 `redemption_fee_ladder=[[7,1.5],[365,0.5],[null,0.25]]`，费 > 预期改善则降级"观望"；未填首买日显示"未知，未做持有期约束"，**不得默认 0 天** |
| Q11 | **复盘统一复权 + 基准加回股息常数 2.7%（A+B②+C）** | `review_service._fetch_nav_series` 场外分支改消费复权序列（ETF 保持 qfq），回填链路 `routers/analysis.py:461` 同步；新增 `benchmark_dividend_yield_pct=2.7` 按区间天数折算，与 Q7 的 bench 同源一起改；复盘/对比/推送/回测头部固定三行口径（净值口径 / 基准口径 / 是否计息）；不接全收益指数（新增上游接口 riskier） |
| §3 | **先影子评分，达标再切生产** | 新增 `analysis_results.shadow_score/shadow_direction/shadow_variant`（或 `analysis_shadow` 表），同轮两套口径各算一次，生产 `weighted_score/signal_direction` 切换前保持旧口径；每日差异报表（分歧 N 只、旧 buy→新 hold M 只，按已落库的 `original_score/dynamic_buy_threshold` 分档）；切换判据写死：分歧连续 5 个交易日 <15%，**或**影子口径在 Q6 新基线上的超额不劣于旧口径；配套落 `pool_size` / `nav_as_of_date` / `factor_coverage` 三个元数据列 |

**未单独提问但随批执行的默认（按本文建议档，如需改动请提出）**：
Q1 五档阈值只驱动 advice/equity 文本（方向仍由动态阈值决定）并修 `ai_service.py` 提示词来源；
Q3 动量族保留 short/mid、accel/trend 正交化 + 簇权重上限 35%（≈2.9）；Q4 阈值按有效权重折算 + 覆盖率 <60% 不评分；
Q5 显式标注"池内相对分"并落池规模；Q7 命中率改超额口径 + 固定 30 交易日窗口；Q8 恢复期未走完则暂不判定；
Q12/Q13/Q14 按 §2 建议（诊断口径、死配置清扫、CI 只补 pytest + build 两条 job）。

**开发顺序不变**：2A 度量（Q6 → Q7 → Q11）→ 2B 防御（Q8 → Q9 → Q10）→ 2C 评分结构（先 §3 影子，达标后 Q2+Q3+Q4+Q1 同批切）→ 2D 卫生（Q5/Q12/Q13/Q14）。
2A/2B 不改生产买卖信号，可先行开发；2C 必须等影子列与差异报表就位。

---

*取证方式：只读 SQL（`sqlite3 -readonly data/fund_quant.db`）+ 逐行读码；本文不含任何代码改动。*
