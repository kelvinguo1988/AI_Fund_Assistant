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
| Q12 | factor_audit：重叠前瞻收益 + IR 未年化 + 无多重比较校正 + 无样本外（§3.7 P2） | **非重叠采样 + IR 年化标注 + Bonferroni 提示** | 否（只影响诊断结论） | 是（诊断表加口径列） | ✅ |
| Q13 | P3 死配置与口径不一致打包（§3.7 P3） | **一次清扫**：signal_rules 死配置、MACD 文档、两种分位定义、size_stability 量纲、`pb` 字段 | 否 | 是（因子页禁编辑死字段） | ✅ |
| Q14 | 工程闸门：CI 无一条 `run:`、无 ruff/mypy/eslint/pre-commit（§体检基线） | **先补"测试+构建"两条 job**，再考虑 lint | 否 | 否 | ✅ |

推荐实施顺序：**Q6 → Q7 → Q8 → Q9 → Q10 → Q11 → Q4 → Q1 → Q2 → Q3 → Q5 → Q12 → Q13 → Q14**。
理由：Q6–Q11 属于"验证口径自身有问题"，先把尺子校准，再调因子（Q1–Q4），否则调参结论
建立在被污染的指标上；Q5/Q12–Q14 是标注与卫生项，随时可做。

### 0.1 实施进度（开发时同步）

| 条目 | 状态 | 落点 | 回滚键 |
|---|---|---|---|
| Q6 回测口径（2A-1） | ✅ 已开发并浏览器实测 | `backtest_service` 仓位状态机 + 三基线 + `_sample_gate`；`/backtest` 页三条曲线与「样本不足」Chip；`GET|PUT /api/backtest/config/measurement` | `backtest_carry_position` / `backtest_min_signals` / `backtest_min_coverage_pct`（置 0 即回旧口径） |
| Q7 命中率口径（2A-2） | ✅ 已开发并浏览器实测 | `advice_learning_service` 双口径 + 固定 30 净值日窗口 + `run_advice_backfill`；工单入账（`presets` 投递路径）；每周六 01:00 调度；持仓页「建议命中率与阈值校准」卡 | `system_config.advice_hit_mode='abs'`（整链回旧判定，`hit_abs/hit_excess` 两列历史都在） |
| Q11 复盘统一复权（2A-3） | ✅ 已开发并浏览器实测 | `caliber_service`（唯一口径源：读/写/股息累乘/三行口径头）；`review_service.otc_nav_pairs` 复权 + `_signal_hit_stats` 双口径；`fund_compare_service` 与 `advice_learning_service.run_advice_backfill` 同源消费；`GET|PUT /api/analysis/caliber`；复盘页三行口径 + 「口径设置」开关、PK 页与回测页口径头、AI 简报 payload `caliber` + 照抄约束 | `system_config.review_nav_adjusted='0'`（回裸单位净值）/ `benchmark_dividend_yield_pct='0'`（回纯价格指数）；两键都可从 UI 改，互不耦合 |
| Q8 棺材钉恢复窗口（2B-1） | ✅ 已开发并 UI 实测 | `quality_filter._scan_coffin_nail()` 一次扫描返回 `(vetoed, pending)`；恢复期未观测满 `recovery_days` 则不判定，只经 `coffin_nail_pending_warning` → `build_result.warnings` → `signal.quality_warnings` 落库并在观望位/AI 调仓清单展示 | `quality_filter_config.coffin_nail_require_full_recovery_window=0`（回"昨天暴跌也否决"；int 键，自动出现在质量过滤页 ⚰️ 分组） |
| Q9 净值新鲜度（2B-2） | ✅ 已开发并 UI/真实跑实测 | `trading_calendar.count_missing_trading_days` + `load_off_day_dates`（库内日历，零上游）；`quality_filter.eval_nav_staleness` 三档判定；`analysis_service._nav_freshness` 接 `_score_and_store`（veto→`analysis.data_missing` 埋点并跳过评分，warn→`quality_warnings`）；`analysis_results.nav_as_of_date` 新列 + 透出；「质量过滤」页 🕒 净值新鲜度 分组 | `quality_filter_config.nav_staleness_max_trading_days=0`（整个门槛关闭，as-of 列仍照常落库） |
| Q10 权重与持有期（2B-3） | ✅ 已开发并浏览器实测 | `ai/holding_fee.py`（阶梯解析/费率档/下一档天数/宽松日期，纯函数零请求）；`rebalance._position_valuations()` 市值→成本→份额三档 + 逐只 `weight_basis` + `_data_gaps` 说话；`_fee_gate()` 惩罚档降级观望；`FundRealtimeService.peek_cached_nav()`（只读缓存窥探）；`user_positions.first_buy_date` 新列 + 建仓/编辑表单 + CSV 日期列别名；工单/前端/`get_positions` 工具透出持有期与费率；「质量过滤」页 💸 持有期与赎回费 分组（42→44 参数、8→9 组） | `quality_filter_config.redemption_fee_enabled=0`（整段撤掉，工单回到只有评分+恶化佐证的旧样子）；`redemption_fee_penalize_pct` 调惩罚档松紧（0=只标注不拦）；`redemption_fee_ladder` 是 list 值、配置页不渲染，只能经 `quality_filter_config` JSON 覆盖（脏值回落默认阶梯）|
| §3 影子评分层 | ✅ 已开发并浏览器实测 | `analysis_results` 6 个可空新列（`shadow_score/shadow_direction/shadow_variant/shadow_detail` + `pool_size/factor_coverage`，启动迁移、不回填历史）；`backend/engines/shadow_scoring.py` 变体注册表 + `compute_shadow`（三类失败收敛成 `(None,None)`）；`analysis_service._score_and_store` 用质量过滤修正后的同一份输入算影子；`backend/services/shadow_report_service.py` 分歧报表（2 条本地 SQL）；`GET|PUT /api/analysis/shadow-config`、`GET /api/analysis/shadow-divergence`；「评分配置」页影子口径卡 | `system_config.shadow_scoring_enabled=0`（关闭时影子列显式写 NULL，不留旧对照）；换口径只改 `shadow_variant`，生产列不受影响 |
| 2C 评分结构新口径（Q2+Q3+Q4+Q1，**只入影子**） | ✅ 已开发并浏览器实测（注册为 `caliber_2c`，生产未切） | `backend/engines/shadow_variants.py`：市场三因子退出加权和（Q2）+ 动量簇权重上限与趋势乘性位（Q3）+ 覆盖率折算门槛（Q4）+ 五档只渲染文案（Q1）；`register_variant("caliber_2c")` 挂进 §3 注册表，分析链零改动；4 个口径参数进 `QUALITY_CONFIG`（44→48 参数、9→10 组「影子口径2C」），经 `GET|PUT /api/system/quality-config` 读写、越界钳位；`GET /api/analysis/shadow-config` 新增 `variant_descriptions` / `caliber_params`；分歧报表与卡片新增 `skip_rows`（低覆盖率不出记录）| 影子层整体回滚 = `shadow_scoring_enabled=0`（生产信号从未依赖本口径）；单项回滚 = 把对应参数调回中性（`shadow_2c_cluster_cap_pct=0` 关簇上限、`shadow_2c_trend_disagree_factor=1` 关乘性位、`shadow_2c_min_coverage=0` 关覆盖率折算）；**生产 `threshold_ref_total_weight` 仍是 8.3，未跟着改成 6.0** |
| Q5 池内相对分标注（2D） | ✅ 已开发并浏览器实测 | 单一文案源 `scoring_engine.score_caliber_note(pool_size)`（`scoring_engine.py:285-299`）+ 前端同值常量 `utils/format.ts::scoreCaliberNote` / `THIN_POOL_SIZE=20`（由 `tests/test_score_caliber.py` 断言两边同值）；证据是 `analysis_results.pool_size`（截面标准化后回填，批量与流式两条路径都记；**旧行 NULL → 文案改为"样本数未记录，不能跨期/跨池比较"**，`<20` 追加"池子偏薄"）；消费面全覆盖：仪表盘评分表头 tooltip 与详情行、历史报告表头与详情、报告引擎总分行与市场段（`report_engine.py:113/330`）、AI 简报 payload 与逐基金明细、Skill 上下文、Agent 工具描述与 `signal_overview` 预设任务 caveat、`AnalysisResultOut.pool_size` 透出；持仓页「分差」列头 tooltip 写明"适合给候选配对排序，不适合当绝对刻度" | 无配置键（纯文案 + 只增一个可空列，该列与 §3 影子元数据共用）；要去掉标注即 revert 这一组调用点 |
| Q12 因子诊断 IC 口径（2D） | ✅ 已开发并回归实测 | `backend/ai/factor_audit.py`：`daily_ic(..., overlapping=False)` 按 horizon 每 h 个有效交易日取一个**非重叠**样本（重叠时 `IR=mean/std` 被自相关放大 ≈√h）；`days`（独立周期数）与 `n_days_valid`（重叠口径原始长度）并存、`sampling` 标注口径；`rank_ic_ir_annualized = IR × √(252/h)`；`t_two_sided_p` / `ic_p_value` 用正则化不完全贝塔函数算双侧 Student-t；`benjamini_hochberg` 对同窗口全因子做多重比较校正 → `rank_ic_q_bh` + `significant`（**实现选 BH 而非 §0 建议的 Bonferroni**：一次审计要同时看 11 个因子 × 多个 horizon，Bonferroni 在真信号弱时会把结论全压成"不显著"，等于没有结论）；`MIN_IC_PERIODS_FOR_CONCLUSION=8` 以下只出过程数字、不出"哪个因子更好"的结论并写进 caveats；`summary_md` 扩到 11 列且口径行随采样模式变化；诊断 Agent 预设任务提示词同步要求引用非重叠周期 / BH q / 年化 IR；**全部在已落库样本上做纯 Python 统计，零上游请求** | `FactorAuditService.audit(overlapping_ic=True)` 回退逐日重叠旧口径（报表会明确标"逐日重叠周期"）；IC 序列零方差时 IR/p/q/significant 一律 None，绝不显示"✓" |
| Q13 死配置与口径清扫（2D） | ✅ 已开发 | `factor_engine.SIGNAL_RULES_INERT_FACTORS`（10 个计算函数从不读 DB `signal_rules` 的因子，判据可机检：源码不含 `rules_from_params`）+ `signal_rules_effective()`；`calculate_all` 注入规则前先查该表，死配置不再无谓注入；`database._clear_inert_signal_rules()` 启动幂等迁移清掉存量规则数组 + 5 个种子因子的 seed 置 `[]`；`FactorOut.signal_rules_effective` 经 `/api/factors` 透出，因子页对无效因子打「规则不适用」chip 并说明得分来源；`backend/utils/stats.py::percentile_rank_inclusive` 统一分位定义（含当前值；此前 `index_valuation_service` 用严格 `<`、`market_regime_service` 用 `<=`，同一指数两处差 1 个点）；仪表盘分位线与策略极端档**改名并注释为两套不同分区**（`DASHBOARD_PE_LOW/HIGH_PERCENTILE` 30/70 vs `extreme_*_valuation_pct` 0.15/0.85），改一边不影响另一边；MACD 文档删掉从未实现的"放量"档；`size_stability` 文档写明量纲（深交所"基金份额"是份，需 × 最新净值换成元才匹配 2 亿~50 亿档）与**当前未启用**，适配器改为落元序列；删除无人消费且标注错误的 `FundData.pb`（实为 csindex 市盈率2）与 `volume_history` | 纯文档 / 死字段 / 未启用因子，**生产信号零变化**；迁移幂等且只清死配置因子，4 个真读规则的因子（`drawdown_recovery` + 3 个 market）有反向用例保护；回滚 = revert |
| Q14 CI 工程闸门（2D） | ✅ 已开发 | 新增 `.github/workflows/ci.yml`：`backend-tests`（python 3.9 对齐后端 Dockerfile，`pip install -r backend/requirements.txt`，`PYTHONPATH=.` 跑 `pytest -q`）+ `frontend-build`（node 20 对齐前端 Dockerfile，`npm ci && npm run build`）；此前 `.github/workflows/` 只有 `docker-publish.yml` 且**0 条 `run:`** —— 从未拦过任何东西；测试本身零上游请求、无需 .env，所以 CI 不会撞限流红线；lint / type-check（ruff、mypy、eslint）有意留到 **Q14b**，不与本次功能改动混在一个闸门里 | 删掉该文件即回滚；`tests/test_dead_config.py::TestCiActuallyGates` 断言两条 job 的 `run:` 里确实有 pytest 与 `npm run build`、工具链版本与 Dockerfile 一致，防止再次退化成"看起来有 CI"的纸面配置 |

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

**落地（2B-1，2026-10-02 已开发并 UI 实测）**

- 判据按 **A**：`_scan_coffin_nail()` 里 `observed = n - recovery_start`，`observed < recovery_days`
  且开关为 1 时 `continue` 不参与判定；标注按 **B**：同一次扫描记 `pending`，
  文案 `棺材钉形态待确认：最近一次回撤 {x%} 之后仅观测 {observed} 个交易日（判定需 60 日恢复期），本轮暂不否决`。
- pending 取"最近一次"（循环里 `start` 越大越新，直接覆盖），不会把一年内每个未走完的窗口都念一遍。
- `check_coffin_nail_pattern()` / `coffin_nail_pending_warning()` 是内核的两个薄包装，
  `pre_filter()` 保持 2 元组合约（`vetoed, reason = qf.pre_filter(...)` 的既有调用与测试不破）。
  标注挂在 `build_result()` Step 1 的非否决分支，经 `signal.quality_warnings` 落库并上仪表盘/AI 清单。
- 上游预算：**0 新增请求**（纯本地序列判定）。回滚键是 int，`GET|PUT /api/system/quality-config`
  与「质量过滤」页 ⚰️ 分组自动出现，无需前端改动；UI 保存走加法合并，不会抹掉该列既有的其他覆盖键（实测）。
- 原计划"上线前跑一遍只读统计给出命中清单"**未能执行**：被否决基金不落 `analysis_results`（无 veto 列），
  `fund_data_caches` 只有阶段涨幅没有净值序列 → 复现判定必须为 57 只池重取净值，与防封禁红线冲突。
  改为随下一轮正常分析自然观察：`select * from analysis_results where quality_warnings like '%棺材钉形态待确认%'`。
  2026-10-02 单只真实跑（007491 南方信息创新混合C，1 次上游请求）已命中并落库：
  `棺材钉形态待确认：最近一次回撤 20% 之后仅观测 30 个交易日（判定需 60 日恢复期），本轮暂不否决`
  —— 旧口径下这一只会被直接否决。
- 测试：`TestCoffinNailPendingWindow` 6 例（观测 10 日不否决 / 观测 80 日仍否决 / 文案含实际天数 /
  干净基金无标注 / 回滚键复现旧行为 / `build_result` 带上标注）；全量 578 passed。

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

**落地（2B-2，2026-10-02 已开发，UI + 真实单只跑实测）**

- 缺口按**交易日**且**两端都不计**：`count_missing_trading_days(nav_date, today, off_days)` 数的是
  `(nav_date, today)` 开区间内的开市日，所以"最新净值 = 上一交易日"恒为 0，不会把正常披露节奏当滞后；
  周末一律休市（调休补班的周六股市不开市，与 `is_a_share_trading_day_async` 同口径），
  法定节假日从库内 `holiday_calendar`（`is_off_day=True`）读，**一轮一次 DB 查询、零上游请求**。
  表为空时退化为"周一至周五"，只会把缺口算多（偏保守），不会放过停披基金。
- 判档 `eval_nav_staleness(missing_days, cfg, slow_disclosure, nav_date)` → `off/ok/warn/veto`：
  `>5` 标注、`>10` 否决，QDII/跨境/96 开头互认基金两档各 +5（`nav_staleness_slow_disclosure_extra_days`）。
  `missing_days is None` 或净值日期解析失败 → `off`（不判）：取数格式变化应当表现为"防线缺失"，
  而不是把整池当陈旧否决。
- 接线在 `_score_and_store`（批量与流式共用）：veto 走既有 `_log_missing_nav` →
  `analysis.data_missing` 埋点 + 跳过评分（流式路径自动进 `complete.failed`），warn 追加到
  `qf_result.warnings` → `signal.quality_warnings` → 落库与仪表盘/AI 清单同一出口。
- `analysis_results.nav_as_of_date`（YYYY-MM-DD，可空，启动迁移）：与 `analysis_date` 的差就是披露缺口，
  §3 影子层的 `nav_as_of_date` 依赖同一列；回滚开关只关判定，不影响这一列继续落库。
- 前端：「质量过滤」页新增 🕒 净值新鲜度 分组（3 个 int 键自动进 `GET|PUT /api/system/quality-config`，
  42 参数 / 8 组），`AnalysisResult` 类型补 `nav_as_of_date`。
- 实测：单只真实跑（007491）落库 `nav_as_of_date=2026-09-30`、`analysis_date=2026-10-02`（10-01 国庆休市，
  缺口 0 → 不标注，符合预期）；否决/标注两档由单测覆盖真实 `_score_and_store` 路径。
  `tests/test_nav_freshness.py` 25 例（缺口计数含长假与补班周六、库内休市日读取、三档判定与回滚键、
  QDII 放宽、慢披露识别、跳过评分 + 埋点、标注与 as-of 落库、门槛关闭时不查日历）；全量 603 passed。
- **信号漂移影响**：`analysis_results` 行数在披露停摆期下降（QDII/停牌基金不再产出建议），
  与 Q6-C 样本下限叠加会让"样本不足"更常见 —— 这是预期收益，不是回归。

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

**落地（2B-3，2026-10-02 已开发，B+A+C 同批，C 的两步合成一次上线）**

- **B（去哨兵）**：`_position_valuations()` 三档链 **实时净值市值 → 成本市值 → 份额×组合平均单位价值**。
  末档不直接用 `shares/Σshares`：那样量纲会从元掉成比例，与其余持仓不同源；用
  `shares × avg_unit`（`avg_unit = Σ已知市值 / Σ已知份额`）既保留裁定 B 的份额比例，
  又不把整只持仓挤出归一结果。缺档逐只 `weight_basis` 标注 + `_data_gaps()` 列代码说话（旧实现只在
  **全部**缺成本时才提示）。
- **A（市值口径）红线兑现**：净值**只读** `FundRealtimeService.peek_cached_nav(codes, 7天)`
  （本批新增的只读窥探：命中 `_estimate_cache` 才返回，未命中**不回退拉取**），
  `_position_valuations` 外层 try/except 包住 —— 缓存不可用只让口径退到成本/份额档，不该炸整张工单。
  实测（开发库，两只真实持仓、无缓存命中）：`weight_basis=cost` × 2，权重 35.6% / 64.4%
  = 8960:16200，与旧口径相同；差别在漏填成本的那只不再掉到 0.006%，以及仪表盘刚看过的基金会自动升到 market 档。
- **C（持有期与赎回费）**：`holding_days` 按**自然日**（基金合同"持续持有期少于 7 日"量的就是自然日），
  唯一输入是 `user_positions.first_buy_date`（可空新列，启动迁移且**不从 `created_at` 回填**）。
  新纯函数模块 `backend/ai/holding_fee.py`：`parse_fee_ladder` 逐行校验（上界≤0、费率越界、形状错都丢行），
  全脏回落 `DEFAULT_FEE_LADDER` 并 `logger.warning` —— 配置写错可以关掉约束，但不能表现为"免赎回费"。
- **惩罚档的诚实映射**：裁定原文的"`fee_pct > 预期收益改善`"在本仓没有可信数值来源
  （校准只动阈值，`weighted_score` 不是收益率，换算等于编一个系数），故实现为可配的
  `redemption_fee_penalize_pct`（默认 1.0 ⇒ 只有 7 日 1.5% 这档拦人，0.5%/0.25% 两档只标注），
  落惩罚档的卖出**降级为观望**且 `confirm_days = days_to_next_fee_tier()`（再持有几天出档），
  理由里保留恶化佐证 —— "该卖 vs 值得卖"分开，但分数恶化不能因费率被抹掉。
- **未填首买日 = 未知**：`holding_days=None` → 不做费用约束、卖出理由写"持有期未知（未填首次买入日），
  未做赎回费约束"，caveat 列出代码并点名"不默认 0 天"。等权近似模式（无真实持仓）压根不产持有期字段。
- **回滚保真**：`redemption_fee_enabled=0` 时 `_fee_gate()` 提前返回全空结果（无 `holding_days`、
  无 notes、不降级），工单文本与改动前逐字一致（`test_rollback_switch_removes_fee_from_worklist` 断言
  理由里不出现"赎回费"）。阶梯是 list 值 ⇒ 配置页只渲染数值参数，故 💸 分组只出现 2 个 int 键，
  阶梯经 `quality_filter_config` JSON 覆盖（`merge_quality_config` 自 2026-08-22 起接受 list/str）。
- **输入面**：建仓/编辑对话框 `type=date`；PUT 用 `model_fields_set` 区分"键未出现=不动"与
  "显式 null=清除"；未来日期 400（否则持有期被 clamp 成 0 天 → 全池建议按惩罚档拦掉）；
  `upsert_position(first_buy_date=None)` 语义是**不改**，所以不带日期列的 CSV 再导入不会抹掉手工补录；
  CSV 认「首次买入日期/买入日期/买入时间/确认日期/交易日期」，`2026/9/3`、`2026年9月3日`、`20260903` 都吃，
  解析不出或晚于今天 → 当未填 + 逐行报错，不拦整份导入。
- **前端**：Tab1「持有期」列（日期 + `持有 N 天` / `持有 —`）与缺首买日补录提示条；Tab2 阶梯文案行
  （`赎回费阶梯 <7天 1.5% / <365天 0.5% / ≥365天 0.25%，≥1% 降级观望`）、卖出行费率 chips、
  同赛道换仓表「卖侧持有/费率」列、持仓 chips tooltip `权重口径 成本市值（净值未命中）｜持有 34 天`。
- **测试污染修复**：调仓 Agent 任务的 SSE 测试带 `record_advice=True`，而 `AdviceLearningStore`
  用同步 sqlite 直连 `settings` 的库路径 ⇒ 测试基金（004011/011452/006751）写进了真实命中率样本表。
  conftest 增 autouse `_isolate_advice_store`（与 `_isolate_error_log_store` 同预案：改 settings 路径 +
  重建单例），存量 3 条测试行已删（`advice_outcomes` 为空，无孤儿）。
- 实测：持仓页补录 2026-06-18 → `持有 106 天` + 提示条计数下降 → 编辑清除回 NULL（往返后开发库复原）；
  Tab2 用 `window_days=120`（开发库最新分析停在 2026-07-22）看到阶梯文案/两条口径 caveat/权重 chips；
  质量过滤页 💸 分组 2 参数（44 参数 / 9 组）；控制台无新增报错。
  `tests/test_positions_rebalance.py` 16→37 例；全量 624 passed，`tsc --noEmit` + `vite build` 通过。
- **待你补录**：013149 / 162719 两只真实持仓的 `first_buy_date` 目前为空（工单显示"持有 —"）——
  补录后赎回费约束与惩罚档降级才生效；没有它，这一半防线按设计是"未知即不约束"。

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

**落地（2A-3，2026-10-02 已开发并浏览器实测）**

- 新增 `backend/services/caliber_service.py` 为唯一口径源：`load_caliber/save_caliber`
  （一轮只读一次，脏值回落默认，股息率夹在 0~10）、`with_dividend_carry`
  （价格指数点位 → 含息序列：逐交易日累乘 `1+y/252` 的**前缀因子**，任意子区间求比值时
  公共前缀自动约掉，剩下的正好是"区间交易日数 × 单日股息"，因此调用方无需再传日期）、
  `caliber_head_lines`（三行口径头）。
- A：`review_service.otc_nav_pairs` 用因子链同一个 `build_forward_adjusted_nav` 做前复权
  （日增长率缺失日退回净值比、整段缺失原样返回，故 f10/lsjz 兜底源不会"复权后更差"；
  停牌/NaN/≤0 整行剔除）；`_fetch_nav_series(..., adjusted=)` 由 `review_nav_adjusted` 驱动，
  ETF 分支保持 qfq。PK（`fund_compare_service`）与建议回填（`run_advice_backfill`）共用同一条链。
- B②：基准股息率默认 2.7，三处消费方（复盘/ PK / 回填）都从 `get_benchmark_series()` 的
  类级 1h 缓存序列上做变换 → **上游预算 0 新增请求**。
- C：`report.caliber.lines` 随复盘与 PK 报告返回，前端只渲染不再硬编码；AI 每日简报 payload
  增加 `caliber` 并在提示词里要求"引用区间收益/超额/回测数字必须单独一行照抄三行口径"；
  回测页头部改为净值口径/基线口径/现金不计息三行。
- 双口径同向率（复盘侧的 Q7 对应物）：`signal_stats.excess` 为主、绝对口径降为对照。
  实测 2026-09-02→10-02（基准 -3.99%）：绝对 **27.3%**（buy 1/9, sell 2/2）
  vs 超额 **63.6%**（buy 6/9, sell 1/2）—— 印证"绝对口径量的是市场方向 beta"。
- 实测附带发现（**非本条改动引入**）：复盘页 7 只场内 ETF 全部取数失败
  `ak.fund_etf_hist_em` → `ConnectionError/RemoteDisconnected`，冷却 20s 后单只复现同样失败，
  说明是东财 K 线接口对本机 IP 的连通性问题（错误日志按 `rate_limit` 归类，同波只记 1 条）。
  场外 49 只正常。属既有 `_get_etf_data` 的上游依赖，不新增请求，等冷却即恢复；
  若要加固可让 ETF 分支优先读库内净值/行情缓存，留给 2B 之后的数据源专项。

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
   - 判据一：末尾**连续 5 个交易日**的合并分歧样本，其分歧比例的 **Wilson 单侧 95% 置信上界 < 15%**
     （2026-10-02 定案，见下方"判据一口径定案"；原写法"逐日都 <15%"在 14 只/日的薄池上不成立）；
   - 或影子口径在 Q6 新基线（vs 静态 50%）上的超额不劣于旧口径；
   满足后再把开关切到生产。
4. **成本**：影子评分只多算一次纯 Python 加权（无上游请求，符合红线）。

配套需要落库的元数据（Q5/Q6/Q9/Q12 都要用到）：`pool_size`、`nav_as_of_date`、
`factor_coverage`（有效权重和 / 总权重）。属**新增列**，向后兼容。

**落地（§3，2026-10-02 已开发，浏览器实测 + 真实单只跑验证）**

- 字段按**新增列**方案（不建 `analysis_shadow` 表）：`analysis_results.shadow_score /
  shadow_direction / shadow_variant / shadow_detail` 四列 + 元数据 `pool_size / factor_coverage`，
  全部可空、启动迁移、**不回填历史**（旧轮没跑过影子，回填出来的数会伪装成对照样本）。
  `nav_as_of_date` 已由 2B-2 落库，本批复用。
- 生产隔离是**测出来不是写出来的**：同一组评分输入分别在影子关/开下落库，
  `weighted_score / signal_direction / operation_advice / quality_warnings` 逐字段相等，
  只有影子列不同；变体内部抛异常时生产行照常写入（`compute_shadow` 三类失败模式
  —— 开关关闭 / 注册表空 / 变体抛错 —— 一律收敛成 `(None, None)` + `logger.warning`）。
- 口径接线在 `_score_and_store` 末尾、`_save_result` 之前，拿到的是**质量过滤修正后**的
  `corrected_scores / corrected_weights`，与生产同一份输入，因此分差只反映加权口径差异而不是取数差异。
  `factor_coverage` 必须按 `active_factors` 导出的 `corrected_weights` 算（权重来自因子配置，
  不是评分项个数），这是 2C-Q4 的分子分母。
- **每轮写全六个键**，无影子时显式 NULL：否则关掉开关或换变体后重跑，页面上的影子还是上一轮的旧对照。
- 变体以注册表挂载（`backend/engines/shadow_scoring.py::register_variant`），2C 只需新增一个
  `caliber_2c` 变体文件并注册，不必再改分析链 —— 这就是"先影子，达标再切"里"切"的成本控制。
  开关与变体名落在 `system_config.shadow_scoring_enabled / shadow_variant`，回滚只需把 enabled 置 0。
- 分歧报表（`backend/services/shadow_report_service.py`，纯本地 3 条 SQL，**零上游请求**）：
  只统计 `shadow_direction` 非 NULL 的行；判据一的样本窗口由 `_criterion_window()` 给出 ——
  末尾**连续有影子对照的 A 股交易日**序列（2026-10-02 加固：读库内 `holiday_calendar`，与 Q9 同表同口径 ——
  周末/节假日手点一轮也会落一行，按"有数据的日期"倒序数可以把判据一刷成五个周六；反之漏跑一个真实交易日必须打断连续。
  休市轮次仍在日表展示并标 `trading_day=false`，汇总给 `non_trading_rounds`，连续窗口顶到 `days` 边界时出"可能被截断"caveat）；
  报表取窗口最后 `STABLE_DAYS_REQUIRED` 天做**合并**分子分母，输出 `criterion_rows / criterion_divergent /
  criterion_pct / criterion_upper_pct / criterion_tolerance / consecutive_days / criterion_window_complete`；
  迁移矩阵 + `buy_to_other/sell_to_other`；分档用修正前 `original_score` 对 `dynamic_buy/sell_threshold`（与仪表盘五档同口径）；
  按变体分行（变体切换打断可比性）。
- **判据二本表不自动判定**：影子口径没有独立回测曲线，要拿它跟 Q6 新基线比就得为影子重跑一遍回测，
  那是 2C 之后的独立决策；报表里只写"需人工评估"，不给编出来的数字。
- 上游预算：**0 新增请求**。影子配置一轮读一次 DB，加权纯 Python，报表只读本地表。
- UI：「评分配置」页「影子评分口径」卡（开关/变体选择/窗口 5·10·20·40/日分歧表/分档表/结论 Alert/解读注意）；
  注册表为空时明确显示"内置的 2C 新口径没被注册进来 —— 这不是故障而是还没有可对比的口径"，不出全零表。
- 开关默认 **开**（`DEFAULT_SHADOW_ENABLED=True`）：2C 已注册，判据一要连续 5 个交易日的对照样本，
  默认关就等于把取证排到"记得去点一下"那一天；而开着是零上游请求、零生产信号影响，
  代价只有每轮多一次纯 Python 加权和 6 个可空列。不想要就 `PUT /api/analysis/shadow-config {"enabled": false}`。
- 判据一常量 `DIVERGENCE_THRESHOLD_PCT=15.0` / `STABLE_DAYS_REQUIRED=5` 由
  `GET /api/analysis/shadow-config` 返回给前端，文案与判定同源，不在前端另写一份。

**判据一口径定案（2026-10-02，选 C：合并分歧率的 Wilson 单侧 95% 置信上界）**

改口径的实证依据（NAS 库 215 行可比对照，脚本 `/tmp/replay_q2.py` 可复算）：换代后 15 个交易日、
每日只 **14 只**有影子对照 ⇒ 单日分歧比例的最小粒度是 **7.1%/只**，一只翻脸 7.1%、两只 14.3%（不过线）、
三只 21.4%（越线）；而 287 行生产历史里有 **61 行（21%）**落在阈值 ±0.5 的边际带。也就是说旧写法
"每个单日都 <15%" 实际比的是**当天有几只基金踩在门槛上**，随机性远大于口径差异，且单日 2 只与 3 只
在结论上是天壤之别。三个候选口径：

| 方案 | 判据 | 结论 |
|---|---|---|
| A：把截面池做大（≥30 只/日）再谈逐日 | 需要为扩池新增取数代码：42 只 active 里 **22 只从未取到净值**，`get_fund_data` 无基金级负缓存，每轮已为它们各花 ~2 次注定失败的请求；扩到 20 只有效样本要新增代码，全池每轮上游请求 **+35%~60%** | **否**：撞防封禁红线，而且它解决的是"数据地基"而不是"判据怎么写"。反向可做的省预算项是把那 22 只置 inactive（每轮省 ~44 次请求），但那会缩池子，与 A 目标相反 |
| B：70 行（5×14）合并点估 <15%，不看置信区间 | 实现最省（一个除法） | **否**：薄池上点估骗人 —— 14 只/日时 0 分歧的点估是 0%，但真实分歧率的上界仍有 16.2%；反过来 09-08 那种单日 4 只（28.6%）会被摊成 5.7% 放行，判据从"从不坏"退化成"平均不坏" |
| **C（采纳）**：连续 5 个交易日的**合并**分歧率，其 **Wilson 单侧 95% 置信上界** < 15% | `meets = consecutive_days ≥ 5 且 wilson_upper_bound(k, n) < 15%` | 点估与上界在报表里并列显示；**样本不够时公式本身就拒绝下结论**（n=14、0 分歧 ⇒ 上界 16.2%），不需要再加"池子小于多少不算"的特判 |

- 实现落点：`wilson_upper_bound()` / `max_tolerated_divergences()` / `_criterion_window()` /
  `_criterion_stats()`（`backend/services/shadow_report_service.py`），`z = 1.645`（单侧 95%）。
  判定仍是纯 SQL + 纯 Python，**上游请求增量 0**。
- 关键刻度（`max_tolerated_divergences`，即该分母最多容忍几次分歧）：n=70 → **5** 次
  （5 次 13.96% 过、6 次 15.73% 出线）；n=60 → **4**；n=20 → 0（0 分歧也只有 11.9% 上界，1 次就 19.9%）；
  n=16 → 0；**n=15 时即使零分歧，上界 15.3% 也越线** ⇒ 池子不足 16 只/日 × 5 天，判据一永远不可能满足，
  这正是薄池该有的行为。
- **代价要写清**：单日分歧率高不再一票否决，改成窗口累计容忍次数上限（上面那张表就是上限本身）。
  改的是"哪天算达标"，**不是**"哪天算样本" —— 低样本日、单日越线日都仍按行数进合并分母，
  所以这个口径同时消掉了 `_stable_trading_days` 旧实现把 `low_sample` 轮次计入连续天数、
  却与自家 caveat"低样本日不进入切换结论"自相矛盾的问题（现在单日比例根本不参与结论）。
- 报表三个分支各自给数：满足（点估 + 上界 + 容忍次数）、窗口不整（只给攒到的天数，不下结论）、
  上界越线（写明"加大 `days` 不会让这个上界变小"，防止用扩窗刷指标）。
- 前端「评分配置」分歧卡同步：判据说明文案、`判据窗口 x/5 日 · 合并分歧 k/n（点估 …，95% 上界 …）` chip
  （Tooltip 带 `criterion_tolerance` 与 `consecutive_days`）、日表日期列的「窗口」/「休市」chip、
  「比例」列表头标注"只作观察"。浏览器实测三个分支各取一次证（夹具造在 09-21~09-29，
  含一个库内标休市的 09-25，实测确认它不进窗口）。

**落地（2C 注册为 `caliber_2c`，2026-10-02 已开发并浏览器实测；生产未切）**

- 新口径四项（Q2+Q3+Q4+Q1）整体落在 `backend/engines/shadow_variants.py` 一个文件里，
  以 `@register_variant("caliber_2c", DESCRIPTION)` 挂进 §3 注册表 —— 分析链只多一行
  `from backend.engines.shadow_variants import caliber_2c  # noqa: F401`（模块顶部导入即注册）。
  **"切"的成本就是这次验证过的**：换口径不改 `_score_and_store`、不改落库、不改报表。
- **成对原则（Q2 裁定的实现约束）**：每一分被移出加权和的权重，都必须同步从
  `threshold_ref_total_weight` 里扣掉。市场 1.8 + 趋势 0.5 ⇒ 影子侧参考权重 **8.3 → 6.0**。
  只清权重不改 ref 会让 `scale = total/ref` 掉到 0.78，等于把买卖门槛一起缩，
  市场顺风从"加分"变成"降门槛"，绕了个后门进来。本变体靠
  `threshold_factor = 结构项 × 覆盖率项` 实现：结构项对"因子齐全、未触簇上限"的基金恰为 **1.0**，
  于是**影子门槛唯一会动的来源是 Q4 覆盖率**，分歧报表里的分差才归得清因。
  推导用的是 `compute_dynamic_thresholds` 对 `scale` 的**齐次性**（base、各 increment、低估下限都乘 scale），
  所以影子阈值 = 生产阈值 × 比例即可，不需要也拿不到市场环境快照 —— 生产自己的
  size_shock / drift / regime 调整被原样继承。
- **簇上限与 ref 故意解耦**：上限 = `shadow_2c_cluster_cap_pct`% × `shadow_2c_cluster_cap_base_weight`
  （默认 35 × 8.3 ≈ **2.905**）。若基准跟着 ref 走，"去掉市场因子"会顺带把动量上限从 2.9 压到 2.275。
  压线事实：**趋势移出后动量簇只剩 2.9 ≤ 2.905，默认参数下簇上限不触发** —— 它是留给"以后把 accel
  加回动量簇/重新配权"的结构护栏，不是当前可调旋钮；报表里 `cluster_cap.applied` 为空即正常。
- **乘性位只收动量簇**：`trend_consistency.raw_value == 0`（mom20 与 mom60 反号）时只把
  short/mid/accel 三项之和 × `shadow_2c_trend_disagree_factor`，波动率与绝对分不受牵连；
  且**先查 `data_valid`** —— 缺数据的基金 raw 同样是 0.0，不查就等于惩罚新基金。
  `momentum_sum` 落进 `detail.trend_gate`，收了多少分看得见。
- **skip 是一个方向而不是 NULL**：覆盖率 < `shadow_2c_min_coverage`（默认 0.6）时
  `shadow_direction = "skip"`，语义是"新口径下这只基金本轮不落库"。不洗成 hold，因为
  "没有记录"和"观望"是两件事，洗掉会系统性低估这批改动的影响面；也不留 NULL，NULL 在报表里
  表示"当日没跑影子"。判据一把 skip 计入分歧（保守：新口径要剔除的基金当然算差异）。
  报表与卡片单列 `skip_rows`（日表「其中 skip」列 + 汇总 chip + caveat）。
- **可执行性同口径**：影子侧复用 `apply_otc_trade_constraint`（暂停申购/封闭期 → 买入降观望），
  结果记 `detail.otc_downgraded`。不套这一步会把"新口径也喊买但场外买不到"记成一致，
  把真实分歧留到明天。
- **Q1 只出措辞**：方向以上面的动态阈值为准，五档只提供文案与权益仓位；
  `detail.tier.conflict_with_dynamic` 记录"档位说加仓但动态阈值判观望"这类冲突，
  这批就是切换时必须给五档标"仅供参考"的数量。
- 4 个参数进 `QUALITY_CONFIG`（44→48 数值参数、9→10 组，组名「影子口径2C」），因此
  `GET|PUT /api/system/quality-config` 与「质量过滤」页自动渲染，无需新端点；越界在变体内部
  钳位并留 `detail.params_clamped` + `logger.warning`（那条 PUT 链只校验"是数字"，
  写成 500 会静默把整个口径改掉 —— 影子报表的全零分歧会被读成"两口径一致"，比崩溃更坏）。
  `GET /api/analysis/shadow-config` 另透出 `variant_descriptions` 与 `caliber_params`
  （含 value/default/out_of_range），卡片直接转述，不在前端重复一份区间。
- 生产隔离的**实测**取证（不是读代码相信的）：在开发库副本上把真 2C 变体挂上跑两只基金，
  再把 `shadow_scoring_enabled` 置 0 重跑同一轮 —— 12 个生产列 × 2 行逐字段相等，
  影子列变回 NULL；`TestProductionIsolation` 用真实 11 因子权重表把这条锁进测试。
  上游请求数 **0**（净值/行情/估值全部复用生产本轮已取到的输入）。
- 已知遗留（不影响影子运行，影响"能不能据此切生产"）：判据二仍需人工另跑一套影子回测；
  库里 6 个信号日 / 123 行的样本量下，判据一要连续 5 个交易日达标才有结论，
  且默认参数下 2C 的分差主要来自 Q4 覆盖率与 Q2 的 1.8 分下移，池子只有 2 只时截面标准化本身不稳。

## 4. 批次划分（确认后按批开发，每批独立可回滚）

- **批次 2A（尺子先准）**：Q6 回测 + Q7 命中率 + Q11 口径统一（含 #56 遗留的复盘未复权）。
  纯"度量"改动，不动买卖信号 → 风险最低、收益最直接，且是后续所有调参的前提。
- **批次 2B（防线补齐）**：Q8 棺材钉 + Q9 净值新鲜度 + Q10 调仓权重与持有期。
  方向以"减少错误信号/减少失真权重"为主。
- **批次 2C（评分结构，需影子评分先行）**：Q2 市场因子 + Q3 动量簇 + Q4 覆盖率归一 + Q1 五档。
  **这一批会实质改变每日买卖数量**，必须走 §3 影子对比，且 Q2 与 Q3/Q4 需同批
  （`threshold_ref_total_weight` 与有效权重折算互相依赖）。
- **批次 2D（卫生）**：Q5 标注 + Q12 诊断口径 + Q13 死配置 + Q14 CI。

**开发状态（2026-10-02 收尾）**：2A / 2B / §3 / 2C / 2D 五批**全部开发完成**，逐条落点与回滚键见 §0.1。
唯一未闭环的是 **2C 尚未切生产**：新口径目前只写 `shadow_*` 列，生产 `weighted_score` 与
`threshold_ref_total_weight=8.3` 保持旧口径，等 §3 判据（连续 5 个交易日的合并分歧率 Wilson 单侧 95% 上界 <15%，或影子在 Q6
新基线上的超额不劣于旧口径）达标后再切。

**攒样本的现实起点（2026-10-02 核）**：判据一现在**一个样本都没有** —— 开发库最近的分析日是 2026-07-22、
`shadow_*` 全库 0 行，`schedules` 表也是空的（开发机不会自己攒）；生产镜像还没有 `shadow_*` 这段代码，
所以要攒就得先部署。日历上 10-01~10-07 国庆、09-25~09-27 中秋休市，**10-02 跑任何一轮都不产生有效对照**
（净值停在 9/30，那一轮与 9/30 那轮完全同质，且按新口径标 `trading_day=false` 不进连续）；
最早可攒的 5 个连续交易日是 **10-08 / 10-09 / 10-12 / 10-13 / 10-14**（10-10 是补班周六，股市不开），
判据一的最早可能结论因此落在 10-14 当天收盘后 —— 在此之前任何"分歧已经达标"的说法都不成立。
按 NAS 实测的 14 只/日池子，那天的窗口分母是 70 只次，**最多容忍 5 次分歧**（第 6 次上界 15.7% 出线）；
攒够 5 天后若中间漏跑任一真实交易日，窗口重新攒，但单日分歧率高不再把那天踢出分母。

## 5. 需要你明确回答的 6 个问题（2026-10-02 已全部裁定）

1. **Q2**：市场三因子是否退出加权和？（这会让全池分数下移最多 1.8，近期 4 个 buy 信号很可能归 0）
2. **Q6-C**：样本不足（<8 个非 hold 信号）时是否**拒绝输出回测结论**？当前库状态下这会让回测页短期"无结论"。
3. **Q11-B**：基准股息用可配常数（默认 2.7%/年）还是尝试接全收益指数（需新增一个上游接口）？
4. **Q9**：净值陈旧否决的天数 N（建议 >5 标注 / >10 否决，QDII 需要宽松档）。
5. **Q10-C**：`user_positions` 是否加 `first_buy_date` 并由你补录 2 条现有持仓？没有它，阶梯赎回费只能显示"未知"。
   → 列已加（2B-3 上线），**013149 / 162719 两只持仓的日期仍为空**，需你在「我的持仓」行尾编辑里补录，
   补录前赎回费约束与惩罚档降级对这两只按设计不生效（显示"持有 —"）。
6. **§3**：是否先做影子评分再切生产？（若否，2C 将直接改变推送与页面的信号，需你接受不可比区间）

### 5.1 裁定结果（用户 2026-10-02 逐项选定，均为推荐档）

| # | 裁定 | 落地要点（开发时照此执行，勿再解释成别的口径） |
|---|---|---|
| Q2 | **退出加权和，只调阈值** | 三只 market 因子 `weight=0`（DB，不改代码即可灰度），`raw_value/score` 继续落库供展示与诊断；**同时** `threshold_ref_total_weight` 8.3 → 6.5（两项必须成对改/成对回滚）；因子页与配置页标注"仅参与阈值/展示，不计入总分"；`extreme_high/low` 两个 increment 先保持 1.0/0.5，用 Q6 新基线复核后再提调整 |
| Q6 | **仓位延续 + 三基线 + 样本下限（A+B+C+E）** | `backtest_carry_position=1`；新增 `baseline_buy_hold` / `baseline_static_half` / `excess_vs_static_half`（头号指标改为 vs 静态半仓）；非 hold 信号 <8 或信号覆盖天数占比 <30% → 只出 `caveat` 不出结论；落 `coverage_start_date/coverage_days/pool_size_at` 并在前端写明"回测区间由入池时点决定"；现金利息默认 0，但文案必须写明"不计息" |
| Q9 | **>5 交易日标注 / >10 否决** | 按交易日历（优先用库内 `holiday_calendar`，**不新增上游请求**）；QDII/海外（含 96 开头互认）额外宽一档 +5；否决走既有 `_log_missing_nav` + `analysis.data_missing` 埋点；同时落 `analysis_results.nav_as_of_date`；回滚键 `nav_staleness_max_trading_days=0` |
| Q10 | **去哨兵 + 市值权重 + 持有期/赎回费（B+A+C）** | `ai/rebalance.py` 缺成本时改 `shares/Σshares` 等权并逐只加 caveat；市值权重 = `shares × 最新净值`，净值**只走已有 `fund_realtime_service` 缓存**，禁止为算权重逐只拉净值；`user_positions` 加可空列 `first_buy_date`（启动迁移 + 前端持仓表单加字段），阶梯费率 `redemption_fee_ladder=[[7,1.5],[365,0.5],[null,0.25]]`，费 > 预期改善则降级"观望"；未填首买日显示"未知，未做持有期约束"，**不得默认 0 天** |
| Q11 | **复盘统一复权 + 基准加回股息常数 2.7%（A+B②+C）** | `review_service._fetch_nav_series` 场外分支改消费复权序列（ETF 保持 qfq），回填链路 `routers/analysis.py:461` 同步；新增 `benchmark_dividend_yield_pct=2.7` 按区间天数折算，与 Q7 的 bench 同源一起改；复盘/对比/推送/回测头部固定三行口径（净值口径 / 基准口径 / 是否计息）；不接全收益指数（新增上游接口 riskier） |
| §3 | **先影子评分，达标再切生产** | 新增 `analysis_results.shadow_score/shadow_direction/shadow_variant`（或 `analysis_shadow` 表），同轮两套口径各算一次，生产 `weighted_score/signal_direction` 切换前保持旧口径；每日差异报表（分歧 N 只、旧 buy→新 hold M 只，按已落库的 `original_score/dynamic_buy_threshold` 分档）；切换判据写死：连续 5 个交易日的**合并**分歧率，其 Wilson 单侧 95% 置信上界 <15%（2026-10-02 定案，原写法"逐日 <15%"在 14 只/日的薄池上不成立，见 §3「判据一口径定案」），**或**影子口径在 Q6 新基线上的超额不劣于旧口径；配套落 `pool_size` / `nav_as_of_date` / `factor_coverage` 三个元数据列 |

**2C 落地后对裁定的两处数值更正（勿按原表数字"改回来"）**

1. Q2 行写的是 `threshold_ref_total_weight` 8.3 → **6.5**（只扣市场 1.8）。实际成对原则要求扣的是
   **全部移出加权和的配置权重**：Q3 把 `trend_consistency` 0.5 也移出去了，所以影子侧是 8.3 → **6.0**。
   按 6.5 算会让影子门槛紧 7.7%，分歧报表就分不清是口径变的还是门槛变的。
   **生产 `threshold_ref_total_weight` 仍是 8.3** —— 2C 只写在影子层，切换时才动生产配置。
2. Q3 的簇上限默认 35% × 8.3 ≈ 2.905，而趋势移出后动量簇只有 2.9 ⇒ **默认参数下簇上限不触发**。
   它按裁定原文"上限 35%（≈2.9）"实现为结构护栏，当前不是调参旋钮；报表里
   `shadow_detail.cluster_cap.applied` 为空属正常，不是没生效。

**未单独提问但随批执行的默认（按本文建议档，如需改动请提出）**：
Q1 五档阈值只驱动 advice/equity 文本（方向仍由动态阈值决定）并修 `ai_service.py` 提示词来源；
Q3 动量族保留 short/mid、accel/trend 正交化 + 簇权重上限 35%（≈2.9）；Q4 阈值按有效权重折算 + 覆盖率 <60% 不评分；
Q5 显式标注"池内相对分"并落池规模；Q7 命中率改超额口径 + 固定 30 交易日窗口；Q8 恢复期未走完则暂不判定；
Q12/Q13/Q14 按 §2 建议（诊断口径、死配置清扫、CI 只补 pytest + build 两条 job）。
其中 Q12 的多重比较校正**实现为 Benjamini–Hochberg 而不是 §0 表里写的 Bonferroni**，理由见 §0.1 Q12 行。

**开发顺序不变**：2A 度量（Q6 → Q7 → Q11）→ 2B 防御（Q8 → Q9 → Q10）→ 2C 评分结构（先 §3 影子，达标后 Q2+Q3+Q4+Q1 同批切）→ 2D 卫生（Q5/Q12/Q13/Q14）。
2A/2B 不改生产买卖信号，可先行开发；2C 必须等影子列与差异报表就位。

---

*取证方式：只读 SQL（`sqlite3 -readonly data/fund_quant.db`）+ 逐行读码。本文 §1–§5 为纯设计与取证，
不含代码改动；§0.1 与各批次"开发状态"是开发完成后回填的落点/回滚键索引，与代码一一对应。*
