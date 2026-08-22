# 代码审计报告 — 逻辑/性能/策略

> 审计日期：2026-08-22 ｜ 范围：backend 引擎层 + 服务编排 + 回测 + 数据源
> 上轮体检（b185490）已修：N+1、静默吞异常、env 命名、索引、废弃 API。本次不重复。

## 结论
最严重问题集中在**策略层**（回测前视偏差 + 加法累计非复利 + 评分双阈值路径割裂），影响信号可信度；性能与数据源以 P2 为主。建议优先修 P1。

---

## P1 — 重要（影响正确性/可信度）

### 1. 回测前视偏差
`backend/services/backtest_service.py:169-182`
当日信号 `sig = signal_map.get(date_key)` 直接乘当日收益 `strategy_daily = daily_return * position`。但分析在收盘后运行（默认 15:10 后），信号反映 T 日收盘，却作用于 T 日涨跌 → 等于用收盘信息交易当日。
**影响**：回测收益虚高，信号有效性评估失真。
**修复（最小）**：信号 shift 一格——`position` 用 `i-1` 的信号作用于 `i` 的收益（next-bar execution）。

### 2. 回测收益用加法累计而非复利
`backtest_service.py:162, 182` — `nav_cum_return += daily_return`（daily_return 已×100 为百分比）。连续 +10%/-10%：复利≈-1%，加法=0%；365 天回测偏差显著。
**修复**：改几何复利 `(1+r1)(1+r2)...-1`，或用 nav 比值直接算区间收益。

### 3. 评分双阈值路径割裂，五档配置在质量过滤路径形同虚设
`scoring_engine.py:208-228` — `compute_with_quality_filter` 先调 `compute()` 拿 base_signal，但最终 `direction/strength/advice/equity` 全被 `quality_filter.determine_signal`（动态阈值）覆盖；`DEFAULT_THRESHOLDS` 五档（3.0/1.5/-1.5/-3.0/-6.4）在走质量过滤路径时完全不起作用，仅 `original_score` 钳位取值用了 base compute。
**影响**：前端调"五档阈值"对实际信号无影响，易误判已调参。
**修复**：统一决策路径——要么 `determine_signal` 也走五档，要么文档明确"五档仅用于无质量过滤的旧路径"，并移除 `compute_with_quality_filter` 里 base_signal 的无效计算。

### 4. trend_consistency 权重硬编码 0.8 覆盖用户配置
`quality_filter.py:639` — `corrected_weights[trend_idx] = cfg["trend_consistency_boost_weight"]`（固定 0.8）。若用户在因子配置里把 trend_consistency 权重设为 1.5，超额持续性触发时反而被降到 0.8。
**修复**：改为 `corrected_weights[trend_idx] = max(original_weight, cfg[...])` 或乘性提升 `original * (1+boost)`。

### 5. merge_quality_config 只接受 int/float 覆盖
`quality_filter.py:963` — `if k in merged and isinstance(v, (int, float))`。但 `excess_windows_days`(list)、`vol_adjust_formula`(str) 无法从 DB 覆盖；前端改了不生效且无提示。
**修复**：放宽类型校验为 `k in merged` 并按 merged[k] 原类型做轻量转换，或对 list/str 单独白名单。

### 6. pe_percentile 用收盘价/净值代理"估值"
`factor_engine.py:200` — `current_close = fund_data.close or fund_data.pe`，实际用 `close_history` 算价格百分位，非 PE 百分位。基金场景"净值低位≈便宜"勉强成立，但名实不符，且牛市中净值新高≠高估。
**影响**：估值因子语义偏差。
**修复**：若能取到基金 PE/PB 历史则用真实 PE；否则改名为 `price_percentile` 消除歧义。

---

## P2 — 建议

### 策略
- `factor_engine.py:235` fed_model 默认 `bond_yield=2.5%` 偏高（2026 年中国 10Y≈1.6-2.0%），系统性低估性价比；应接 `data_source.get_bond_yield()` 实际值。
- `quality_filter.py:887` `check_allocation_drift` 返回 `is_high_purity` 但 `drift, _ = ...` 丢弃，死逻辑。
- `quality_filter.py:317-323` 清盘风险仅两条季度记录时 `pass` 永不否决，可能漏早期风险。
- `scoring_engine.py:103-104` `compute()` 的 `buy_threshold/sell_threshold` 参数函数体内未使用（死参数）。
- `scoring_engine.py:92` `_load_thresholds` 只校验 `len>=3`，不校验每项含 `min_score/signal_direction`，配置缺键会 KeyError。
- `scoring_engine.py:231-235` `equity_map` 与 `DEFAULT_THRESHOLDS.equity_ratio` 重复维护，易漂移。
- `factor_engine.py:433` `size_stability` 的 `1/size_cv` 量纲未规范（可远超 1.0），截面 Z-score 被极端值主导。

### 性能/编排
- `analysis_service.py:182` 因子计算（numpy CPU 密集）在事件循环串行执行 60 只基金，阻塞 IO；可 `asyncio.to_thread` 并行批次。
- `analysis_service.py:215-217` 否决基金直接 `continue`，否决原因仅日志，用户不可见；建议落一条 vetoed 记录或汇总到报告。
- `analysis_service.py:176` fetch 失败基金静默跳过（仅日志），结果数可能少于输入无提示。
- `analysis_service.py:228` `analysis_date=today` 而净值数据可能是昨日 → 报告头/数据日期错配（与"18号推17号资金流"同类）。

### 数据源
- `data_source_manager.py:176` `get_market_indices` 全失败返回空 `MarketIndices()`，下游用 0 可能产生假信号；应让调用方感知失败。
- `data_source_manager.py:113` `_try_recovery` 用 `adapter.available` 属性乐观恢复，可能反复抖动。
- 单次失败即降级、5 分钟冷却，无退避/circuit breaker，对偶发抖动略激进。

---

## 记忆更正（已与代码不符）
项目记忆里数据源优先级写的是 `AKShare→TuShare Pro→BaoStock→TickFlow`，但 commit `59f84de`「数据源精简」已删除 TuShare/BaoStock/TickFlow，**实际只剩 AKShare + JoinQuant**。本次同步更新记忆。

---

## 建议修复顺序
1. 回测前视偏差 + 复利（P1 #1#2）——回测可信度根基
2. 评分双阈值路径统一（P1 #3）——避免"调参无效"陷阱
3. trend 权重覆盖 + merge_config 类型（P1 #4#5）——配置生效
4. pe_percentile 命名/数据源（P1 #6）——因子语义
5. 其余 P2 按需

---

## 数据源与基础设施补充（Explore-3）

### P0 — 阻断性
- **patch 遗漏 `api.fund.eastmoney.com` 子域名** — `backend/patch/eastmoney_patch.py:36-43` `_TARGET_DOMAINS` 仅 6 域，缺该域；而 `backend/data_sources/akshare_adapter.py:296` `_get_otc_fund_nav_raw` 直接 `requests.get("http://api.fund.eastmoney.com/f10/lsjz")`，绕过 UA 轮换/NID 注入且用 http + http Referer（:298）。**场外净值降级路径反爬触发率高，东财侧降级链实际不可用**。修复：加入 `_TARGET_DOMAINS`，URL 改 https。
- **LLM 全部 provider 无 timeout** — `deepseek_provider.py:22`、`openai_provider.py:17`、`tongyi_provider.py:21`、`glm_provider.py:26` 四处 `AsyncOpenAI(...)` 未传 `timeout`/`max_retries`，SDK 默认 600s。AI 对话卡住 → FastAPI worker 挂 10 分钟，几个并发即服务不可用。修复：`AsyncOpenAI(..., timeout=30, max_retries=2)`。

### P1
- `config.py:41` `DEFAULT_AI_API_KEY` 死代码（不读 .env，实际 `ai_service.py:45` 从 system_config 表读），`.env.example:33` 误导用户配了不生效 → 删除或 init 时写 DB。
- `akshare_adapter.py:723-727` 国债收益率全失败回退硬编码 `2.7`（与 `factor_engine.py:235` 的 `2.5` 默认值叠加）→ 基于过时利率假设的信号 → 应返 None 由引擎降级。
- `data_source_manager.py:113-115` `_try_recovery` 仅访问 `adapter.available` 属性（`base.py:63-65` 默认返 True），不发探测请求即 `mark_recovered` → 假恢复、抖动 → 应调轻量探测接口。
- `requirements.txt:7` `akshare>=1.16.72` 未锁版本上限，breaking change（列名"收盘"→"收盘价"）会漂移且耦合 fund_name_cache → 建议 `~=` 或 `==`。
- `fund_theme_service.py:31` `timeout=30` 超 patch 20s 防线（patch 用 setdefault 不覆盖显式值）→ 重新引入线程池耗尽风险 → 改 20s。

### P2
- `connectivity_service.py:102` httpx 直访东财绕过 patch（HEAD 连通性测试无反爬头能 200，但实际数据请求仍被拦，结果误导）。
- `joinquant_adapter.py:65` `is_auth()` 每次 property 访问都发网络请求，`DataSourceManager` 循环多次访问浪费聚宽配额 → 加 60s 缓存。
- `main.py:60-68` shutdown 未 `await engine.dispose()`，SQLite 连接应显式清理。
- 根目录 `requirements.txt:2` `akshare>=1.12.0` 与 `backend/requirements.txt:7` 不一致 → 删除根目录或同步。
- `eastmoney_patch.py:150` 每请求 sleep 1-4s 在 16 线程池内阻塞，5 并发×4s 拖慢批量分析 → 考虑缩短上限至 2s。

### 已正确（无需改）
CronTrigger 时区 Asia/Shanghai（task_scheduler.py:26/86/99/172）；docker-compose.yml TZ=Asia/Shanghai；fund_name_cache.json 可写挂载；`_TARGET_DOMAINS` 已含 fundf10 且注入 Referer（:143-144）。

> 策略层、服务/性能层 agent 仍在跑，完成后追加最终汇总。
