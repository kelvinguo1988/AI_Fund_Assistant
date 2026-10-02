# Changelog

## [Unreleased]

- [新功能] 历史报告导出备份与导入恢复（JSON 格式，含全部因子评分及信号）
- [新功能] 基金池 JSON 一键导出
- [改进] 基金数据获取按 `fund_type` 路由：导入时确定的场内/场外类型直接决定数据接口，消除对所有 OTC 基金先试 ETF 再降级的无效轮询和报错日志
- [修复] 飞书推送前清空 MarketService 内存缓存，确保推送使用最新行情数据
- [改进] 飞书推送后同步更新仪表盘行情缓存（fund_cache_service），仪表盘无需手动刷新也可看到推送时的最新数据
- [修复] `POST /api/analysis/refresh-summary` 增加 `MarketService.clear_cache()`，避免手动刷新时命中 5 分钟内存缓存
- [修复] 仪表盘 + 基金详情页面移除页面加载时自动刷新调用，改为仅显示缓存 + 手动刷新，符合"只在手动或定时任务触发时刷新数据"的语义
- [改进] 仪表盘 + 基金详情页面"数据更新"时间戳显式标注 `(北京时间)`，避免时区歧义
- [改进] 飞书推送前增加行情数据新鲜度校验日志，缺失字段单独告警
- [新功能] 基金详情独立页面：阶段涨幅排序展示、季度持仓明细（可展开）、基金经理信息
- [新功能] 持仓调仓检测：自动对比最新两个季度持仓股票，标注新增/移除股票明细
- [新功能] 基金经理变更检测：追踪基金经理变更历史并标注
- [新功能] 天天基金相关主题自动抓取：新增基金时自动匹配主题标签并着色
- [改进] 仓位/经理变更标签不再写入基金池标签，仅在基金详情页展示
- [改进] 数据刷新增加反爬间隔（每只基金 3-6 秒），避免被东方财富封禁
- [改进] 基金经理全量数据缓存（10s 查询仅首次触发）
- [新功能] 仪表盘"先展示缓存，后台刷新"模式：行情数据缓存到 fund_data_caches 表，页面加载立即展示缓存数据 + 时间戳，后台刷新完成自动更新
- [新功能] POST /api/analysis/refresh-summary：独立后台刷新行情数据缓存端点
- [改进] 阶段涨幅改用 pingzhongdata 批量并发获取，统一数据源
- [修复] Axios 请求超时从 30s 提升至 120s（refresh-details 单独设为 300s），避免长时间任务超时断连
- [修复] Nginx proxy_read_timeout 从 120s 提升至 600s，匹配后端长时间任务
- [修复] 基金详情"深度分析"Tab 恒空：默认选中项取 `Object.keys(funds)[0]`，而 JS 会把纯数字代码键按数值升序前置，于是缓存里已删除的测试代码（123456，四项全 null）永远抢占默认位 → 四张图齐刷刷"暂无"。改为选项按基金池顺序排列且过滤掉四项全空的条目；`/api/funds/extended-detail` 同步按活跃基金过滤缓存残留（缓存整批覆盖写入，但删除代码的旧条目会残留到下次刷新）
- [修复] 基金经理"共同在任"被误报成"经理变更"：`fund_manager_records` 按 (fund_id, name) 唯一且只增不改，旧口径把插入顺序最后一个名字当现任，同批写入的共同在任经理全成"前任"（实测 004011 郑青为"现任"、董辰/闫泽君为"前任"）。新增 `last_seen_at` 在任快照列（启动迁移 + 存量行回填 created_at），现任 = 最近一轮刷新确认在任的全部经理；本地 13 条假变更清零，真实离任从下一次刷新起被识别
- [改进] 在任经理的从业天数/规模/最佳回报随每轮刷新更新（旧口径命中已有记录即 continue，统计长期冻结在首次抓取日）
- [改进] 基金经理 Tab 在全池无经理记录时显式提示数据源与"刷新数据"动作，不再静默显示一排 `--`
- [修复] 无净值基金不再参与评分：数据源拿到空壳 FundData（池中已失效代码，如 968049）时不抛异常，所有因子对空序列返回 0.0 并一起进截面标准化——既拉偏同池其他基金的标准化样本，又给自己留下一条永不变的"观望"记录。改为取数后先判空跳过（批量路径不落库、流式路径计入 SSE `complete.failed`），并以 `category=data` 埋点 `analysis.data_missing`（同代码 60s 频控），可在「系统错误告警」按"数据缺失"看到成因
- [新功能] `fund_quarterly` 落库链路补齐：「刷新数据」写 pingzhongdata 扩展缓存时同源解析资产配比/规模/持有人结构/申购赎回，按报告期 upsert（规模优先 `Data_fluctuationScale` 亿元→元，内部人份额 = 内部持有比例 × 总份额），生效日期按披露截止（季报 +2 月、半年报/年报 +3 月）取当月首个工作日。此前该表恒空，第零层质量过滤的清盘否决/规模冲击/仓位漂移/机构认可度四项长期按中性处理。零额外网络请求，本地回灌实测 56 只基金 / 361 个报告期
- [新功能] 场外实时估值改为仓位感知：持仓加权只覆盖股票仓位的一部分，未覆盖的 `R_stock - 覆盖率` 份额按当日指数涨跌补齐（`est_model=position_aware`，R_stock 取最新已生效季报），替代原先"覆盖率<50% 才混合指数"的粗口径；缺季报仓位时回退旧归一法/指数混合，仪表盘 tooltip 同步标注

### 2026-10-02 外部审查第一批整改（#53–#61：取数正确性、静默错数据与防封禁预算）

**数据源与降级链**
- [修复] 调休自动同步其实从未成功过：`fetch_holiday_json` 把 httpx 独有的 `resolution_callback` 传给了 `requests.get`，每次调用恒抛 TypeError，当年/次年安排一次都没落库（手动同步同样失败）。改为 requests 手动跟随重定向（上限 5 跳，每一跳落地前重新做 SSRF 校验，公网域名 302 → `169.254.169.254` 依旧打不通），`backend/requirements.txt` 的注释同步更正（`backend/services/holiday_sync_service.py:92-120`）
- [修复] 自动同步"成功后把 enabled 置 false（只同步一次）"→ 保留开关、按 `holiday_last_sync_at` 做 7 天节流：次年安排要到当年才公布，一次性同步必然拿不到，跨年后交易日闸门只能回退 chinese_calendar（年份覆盖到期即退化为"周一至周五"，法定节假日照跑整轮全量分析）。手动 `POST /api/holiday/sync` 不受开关与节流限制
- [新功能] `NoDataError` 把"这个代码在本源确实没有记录"从"源坏了"里摘出来：降级链过去把 `get_fund_data` 抛出的任何异常都记成源故障，池里一只已清盘代码就能让 AKShare 整源降级 5 分钟，之后的基金全改打备源并推"数据源故障"告警。管理器现在见到该异常不降级、也不轮询下一级（两家源覆盖同一个公募基金全集，主源说没有时备源基本也没有，为死代码逐个请求只是徒增上游压力），直接上抛交调用方按无数据跳过；`BaseDataSource.get_fund_data` 把这对合约写进文档
- [改进] 分析侧配套：批量与流式两条取数路径各加 `NoDataError` 分支，归入既有 `analysis.data_missing` 埋点，日志由 error + 完整 traceback 降为 info —— 旧行为是每轮分析为几只失效代码刷 N 条"获取基金数据失败"，把真实断供埋在了噪声里

**量化口径与因子链**
- [修复] 场外基金净值改为分红复权口径：因子链此前直接吃裸 `单位净值(DWJZ)`，而除息日分红在 DWJZ 上表现为一次性下跌、同源「日增长率」却已按分红调整 → 有分红的基金在除息日被记成一天暴跌（实测 004815 真实 +0.74% 记成 -3.80%），动量/回撤/波动/MACD/截面分位五类因子同时被污染。新增 `build_forward_adjusted_nav`（纯 Python，自末值回推的前复权；缺日增长率的相邻日退回净值比，整段无增长率则不调整）喂给 `FundData.close_history`，展示字段 `close/date` 仍是官方单位净值且与复权序列末值同源。**注意信号漂移：分红型场外基金的因子值与总分在升级后会变，历史 `analysis_results` 与新记录不同口径**。遗留：复盘与建议回填链路仍取单位净值（`review_service.py:261-299`），本次未动，统一口径的方案见 `docs/QUANT_DECISIONS_2026-10.md` Q11
- [修复] 停牌日 DWJZ 为空串时 `astype(float)` 会让整只基金抛 ValueError（再被上层当成数据源故障降级整源）→ 逐列 `to_numeric(errors="coerce")`，缺失交给复权链；整段无有效净值时保持 `close=None` 交给 data_missing，不再伪造最新值
- [修复] 大盘估值分位真实生效：主源换成乐咕 `stock_index_pe_lg("沪深300")` 月频滚动市盈率（2005 至今），csindex 日频序列降为降级源。旧实现只有 csindex 一路，且 `_MIN_PE_POINTS=250` 的门槛 + `valid[-1215:]` 固定条数切片（假定日频 1215 条≈5 年）在月频序列上只剩 1 个点，分位直接失真。窗口改为近 10 个日历年日历跨度，新增 `valuation_sample_points` 贯穿 schema / 仪表盘卡片 / 飞书推送 / AI 简报（月频≈120、日频≈2400，数量级差异必须暴露给消费方，否则两个 0.4 不是同一个置信度）；本地实测分位 0.5833、当前 PE 12.48、120 样本，卡片标题由"近5年"更正为"近10年"
- [新功能] PE 历史序列 6 小时缓存 + 双源全挂 60s 负缓存：序列本身是月频/日频长表，而快照 TTL 只有 1 小时，旧写法每天 24 次快照就拉 24 遍同一段全量历史 —— 与防封禁目标相反；`clear_cache()` 一并清除两层缓存
- [改进] `data_valid` 全链路透传：因子"数据不足"标记原先只活在内存里的 `FactorScoreResult`，落库 JSON 时被丢掉 → 从 DB 重建的报告、AI 上下文与前端因子表永远看不到"该因子缺数据"，而 `score=0` 的两种含义（真实中性 / 缺数据占位）区分不开。现由 `analysis_service` 写入 `factor_scores`，`FactorScore` schema、`/api/analysis` 重建、Agent 的 `get_factor_history`、Skill 基金详情、飞书推送一致透传；`quality_filter` 修正波动率倒数时不再把重建出的因子洗回 `data_valid=True`
- [修复] AI 提示词里的因子明细从未出现过：`ai_service` 对当前落库形态 `{code: {name,score,...}}` 直接 `float(value)` 恒抛 TypeError，被上层 `except` 静默吞掉 → "因子="那一段一直是空的。抽出 `top_factor_lines_for_prompt`，兼容三种历史形态（对象/`{code: score}`/列表），按 |score| 取前 5 并给缺数据因子标注"（数据不足）"

**静默错数据与请求预算**
- [新功能] 失败负缓存 + 按 key 部分合并：`IndexValuationService` / `OtcTradeStatusService` 取数异常**或接口通了一行没有**时置 120s 冷却（旧实现失败不推进时间戳，前端 5s 轮询 + 仪表盘多个区块会把一次风控抖动放大成请求风暴）；指数估值按指数名合并，本轮缺的指数沿用旧条目（最久 6 小时）而不是"忽隐忽现"；阶段涨幅与扩展详情按代码合并，60 只里挂 3 只不再把那 3 只的缓存清空。两个冷却窗口在 `/api/system/data-source-health` 可见
- [修复] 行情五板块全空时不再落库：旧实现无条件 `set_cached_json`，五路同时被限的那一次会留下"全 None + 刚刚更新"的行，而读取路径只判断行是否存在 → 仪表盘此后永远空白、时间戳照旧推进，真实故障被伪装成"数据已最新"。新增 `store_market_summary`（空帧保留旧缓存与旧时间戳并告警）与 `market_cache_has_data`，三处写入点（`/summary`、`/refresh-summary`、推送回填）改用共用的 `build_market_summary_payload` 防口径漂移
- [改进] `MarketService.clear_cache(include_failures=)`：失败冷却是"刚刚被限过、别再打"的唯一记忆，过去定时推送路径无条件清空 → 每天两轮推送各自把已耗尽的降级链再撞一遍。现默认保留冷却，只有用户手动点"刷新数据"（`POST /api/analysis/refresh-summary`）才连带解除

**AI 预算与展示口径**
- [修复] AI token 预算护栏此前形同虚设：流式请求不带 `stream_options` 时 usage 恒为 0，Agent 的 `token_spent += (prompt+completion) or 1` 于是只等于轮数（≤15），12 万预算与 `budget_exhausted` 事件永远不可达。现默认索要 `stream_options={"include_usage": true}`，端点不认识该参数时记标记并在后续轮次去掉；仍拿不到 usage 则按 `estimate_tokens`（CJK 1 字≈1 token，其余 4 字符≈1，宁可偏高）估算 prompt 与 completion
- [修复] 亏损基金在 PK 对比表里没有年化收益：`annualized_return_pct` 的 `total <= 0 → None` 恰好对**亏钱的基金**留空，而夏普/回撤照常显示 → 对比表被动乐观。改为只对净值归零/为负判无定义

**前端**
- [修复] 回测页空闲期不再每 60s 打一次全表：自动回测配置 / 调仓费率 / 批量结果三项一次性加载与"仅在运行期间开轮询"拆成两个 effect（旧实现共用 `[batchRunning]`，每轮"运行中→完成"翻转就把三项整体重跑，空闲时也常驻定时器）；服务端回报 `running === false` 时收尾再拉一次结果，表格不留 60 秒前的中间态。浏览器实测空闲 80s 窗口零请求

**测试与文档**
- [新功能] 新增 `tests/test_no_data_error.py`（无记录不降级、不换源）、`tests/test_cache_merge_and_negative_cache.py`（负缓存 / 按 key 合并 / 空帧不落库）；conftest 补 `real_valuation_services` fixture —— 驱动这两个服务的内部逻辑时必须解除 autouse 网络补丁，网络层仍由用例自行 stub，红线"测试可以慢但不要触发限流"不变。回归：`pytest -q` 499 passed，`npm run build` 通过
- [文档] `docs/QUANT_DECISIONS_2026-10.md`：第二批 14 项量化口径变更（市场三因子去重、回测基线与建议命中率、调仓权重与赎回费阶梯、复权口径统一等）**只做设计与取证**，全部涉及生产信号漂移，需确认方向后再开发；外部审查报告原文归档 `docs/CODE_REVIEW_2026-10-01.md`

### 2026-09-29 全量代码审查修复（P0→P3，框架/逻辑/功能模块/代码质量）

**P0 — 正确性与凭据安全**
- [修复] 东财补丁的目标域判定由 `domain in url` 子串匹配改为 `urlsplit().hostname` 精确主机名：仿冒主机 `eastmoney.com.attacker.net/fund.eastmoney.com` 原本会命中，把刚取的 NID 令牌 + Referer 一起发给第三方（凭据外泄）；顺带修正带端口/大写主机名的漏判（`backend/patch/eastmoney_patch.py`）
- [修复] JoinQuant 适配器在 `available` property 里做同步 token 校验，阻塞事件循环数秒 → property 只做纯状态判断，真实校验移到 `async probe()`；`BaseDataSource` 把这对合约写进文档
- [修复] 调度器一次任务共用一个 DB 会话（交易日闸门/分析/推送）：中途 commit/rollback 会把前一步的数据一起吞掉，改为各阶段独立会话
- [修复] 数据源可用性"自愈过快"：失败标记在下一次请求即被重置，降级源立刻复原、下一波请求原路撞封禁 → 冷却窗口内保持降级并只对恢复做节流探测
- [修复] `get_fund_data` 由"异常/空值二义"改为三态返回，数据源断供显式埋点，不再只表现为"基金被质量过滤"的静默结果
- [修复] 实时行情缓存时间戳口径：腾讯按需补缺不得给全市场快照的 `_*_ts` 续期，否则半截旧数据被当成"刚刷新"（ETF/OTC 两条路径同口径）
- [修复] Agent 工具把 `MarketRegimeSnapshot`（dataclass，非 pydantic）当模型序列化 → 工具结果抛错
- [修复] 全量回测按钮永久禁用：`triggerBatch` 只置本地 `batchRunning` 且永不复位，60s 轮询无终止条件 → 新增 `GET /api/backtest/batch/status`，复位以服务端为准
- [修复] 前端异常上报接口自身 5xx 时递归再上报，后端挂了就形成请求风暴
- [修复] `requirements.txt` 关键版本加 pin（实测 akshare 1.18.63），依赖只增不淘汰的净值缓存补上限

**P1 — 调度、时区、缓存与前端竞态**
- [新功能] 调度器任务当日熔断：小时级 cron 在数据源被风控时会一天补跑十几轮全量分析（正是封禁成因），同一计划当日连续失败 3 次即停跑并告警，跨日自动清零（`TaskScheduler._daily_fail`）
- [改进] 启动顺序改为"市场缓存预热先行、调度器后至"（最多等 45s，`asyncio.shield` 超时放行）：APScheduler 一启动就按 `misfire_grace_time` 补跑整轮分析，与预热并发即同时段两拨行情源；45s 预算留足容器 healthcheck（start_period 15s + 3×30s）余量
- [修复] `utils/timezone.py` 自然日基准与北京时间错位；基金缓存脏数据被写成"刚刚刷新过"，陈旧值长期当新鲜缓存展示
- [修复] 调仓建议阈值自学习用全量累计样本且门槛仅 5 条 → 改近窗口口径，5 条样本的统计噪声不再直接改阈值
- [修复] 实时估值 hints 逐轮累积且与缓存共享引用（上一轮提示/交易时点漏进本轮）
- [新功能] 全局 `ErrorBoundary`：React 18 渲染期抛错原本卸载整棵树→白屏，现兜底为可读面板（错误信息 + 重试渲染 + 刷新页面）
- [修复] 基金详情持仓 Tab 请求竞态：筛选/刷新/切 Tab 会重新发起 N 只基金的持仓请求，旧实现既不取消也不判存活，慢的旧响应会后到覆盖新结果、卸载后仍 setState → 整批 `AbortController` 可取消
- [修复] AI 对话 SSE 断流不再静默停在半截回答：既无 `done` 也无 `error` 判为连接被中途切断并显式提示；协议破口原来被 catch 吞掉，现输出可排查信息；`budget_exhausted` 收尾轮补提示

**P2 — 口径收敛、可维护性与前端一致性**
- [改进] 基金经理缓存加 6 小时 TTL + 陈旧快照回退（模块级常量此前被 import 快照，改动在本进程永远不可见）；合法的 0 值（新任经理从业 0 天）不再被 `新值 or 旧值` 当假值丢弃
- [修复] 大盘资金流降级源的"主力净流入-净占比"自造公式（净额/三档成交额）实测算出 21%，而真实量级 0.1~0.5%，与另两条源不可比却同栏展示 → 按四档流入+流出≈成交总额估算，对齐东财口径；顺带实测证伪了主源解析"单位分裂"的怀疑（三条源除数均正确，未动代码）
- [改进] `log_source_failure` 的 `category` 默认值由 `rate_limit` 改为 `other`，与 `ErrorLogStore.log` 一致，避免新增调用方漏传参就把任意失败报成风控
- [修复] 回测信号对齐：起点前信号只容忍 ≤4 自然日，国庆/春节 8 天长假跑出的信号在回测里凭空消失 → 上限放宽为 10 自然日常量（继续用日历近似，不引入交易日历依赖）
- [修复] 分析提示冷却基于进程内时间戳，NAS 数周不重启时只在第一天说话后彻底静默
- [改进] UA 池收敛到 `backend/utils/concurrency.random_ua` 单点（此前补丁与 service 各持一份）；`market_service` 死导入清理
- [改进] 破坏性操作统一走 `ConfirmDialog`（系统设置的清空错误日志/清空缓存/删除渠道、持仓页删除、回测页清空结果），不再用原生 `window.confirm`；图标按钮与开关补 `aria-label`
- [改进] 红涨绿跌色号收敛到 `utils/format.ts`（此前 `#f44336`/`#E74C3C`、`#4caf50`/`#27AE60` 四处各写一份），仪表盘/回测/详情/评分仪表盘共用
- [修复] 浏览器实测抓到的连带问题：Axios 拦截器把主动取消（AbortController）也当故障 `console.error`，基金详情切一次 Tab 就刷出 30+ 条 `status=undefined 网络异常`，真故障被噪声埋掉 → 取消类请求不再打日志/不上报（仍照常 reject，调用方的存活判断不变）
- [测试] 去掉 `tests/test_fund_tags.py` 与 conftest 重复的 `db_session` fixture；补数据源健康、长假信号对齐、URL 主机名判定、无净值跳过等回归用例

**P3 — 可观测性与文档**
- [新功能] `GET /api/system/data-source-health`：只读聚合进程内的冷却/降级/调度器当日熔断/缓存新鲜度，零网络请求 —— 排查"仪表盘一片空"看这里，不要用探测接口去捅正在冷却的数据源
- [新功能] 系统设置页「数据源运行时健康」卡片：冷却剩余秒数、熔断计划、缓存新鲜度 chips
- [改进] README / ARCHITECTURE / CHANGELOG 与当前实现同步（此前 API 表缺 20+ 端点、文件树缺半数模块、CHANGELOG 全是无日期平铺条目）

**第二批 2A — 度量先准（Q6 / Q7，2026-10-02）**
- [改进] Q6 回测口径重写：无信号日不再强制回到 50% 仓位（`hold` 延续最近一次信号对应的仓位，回滚键 `backtest_carry_position`）；新增**三条基线**（满仓买入持有=净值曲线 / 静态半仓恒 50% 全程不调仓不计费 / 策略），头条指标改为 `excess_vs_static_half`，旧的 `excess_return`（vs 满仓）保留并标注它主要衡量"有没有跑赢一次梭哈"；样本下限 `_sample_gate`（非 hold 信号 <8 或信号覆盖率 <30% 判「样本不足」，曲线只作过程展示），并落 `coverage_start_date/coverage_days/pool_size_at`（全部零网络推导）；口径头三行明示净值口径/基准口径/现金**不计息**
- [改进] Q7 建议命中率双口径：命中判定改为与沪深300 比同区间超额（`hit_excess`，卖出后跑输=命中），绝对口径（`hit_abs`）同时落库仅作对照，`hit` 列按 `system_config.advice_hit_mode` 落值（默认 excess，改回 abs 即整链回滚，无需改代码或重算历史）；评估窗口由"建议日→最新净值日"（同批样本窗口 30~90 天不等）固定为"建议日→其后第 30 个**净值日**"，未走完的样本本轮跳过、下周再取；`stats().by_mode` 双口径分母（改造前 NULL 行计 evaluated 不计分母），`calibrate()` 只按当前口径调阈值
- [修复] 自进化闭环空转：`AdviceLearningStore.log_advice` 零调用方、`/advice-eval` 从未进调度 → 账本永远 0 条，命中率与校准形同虚设。现由投递路径入账（Agent「调仓分析」任务与「AI 每日简报」，同基金同日同方向去重，`GET /api/ai/agent/rebalance` 保持只读不从 GET 写库），并注册每周六 01:00 自动回填任务（与周日 00:00 全量回测错开）
- [改进] 回填链路的上游预算：无到期样本 0 请求；基准走 `get_benchmark_series()` 类级 1h 缓存；净值按基金去重后每只取一次（同基金多条建议共用一条序列），只与只之间随机等待 10~30s（防"连续无间隔请求"这一限流典型形态）；基准取不到时按 0 处理并计数 `benchmark_missing`，不中断本轮
- [新功能] `GET|PUT /api/analysis/advice-hit-mode` 读写口径；持仓页「调仓建议」Tab 新增「建议命中率与阈值校准」卡：两个口径的 sell/buy 命中数同时显示、标出校准实际使用哪个、可一键切换与手动回填
- [改进] 原生 Skill「调仓建议自进化」提示词升级到双口径口径（excess 为准、abs 只作对照、样本 <30 明确不下结论），存量库仅在用户从未改过内置文案时替换；`_run_advice_stats` 返回值附口径说明，避免 AI 把牛市里近乎恒真的绝对命中当成选基能力汇报
- [改进] Q11 三条链路收益口径统一（第一批 #56 遗留）：新增 `backend/services/caliber_service.py` 作为唯一口径源 —— 复盘/PK/建议回填的**场外净值改分红复权**（`otc_nav_pairs` 复用因子链的 `build_forward_adjusted_nav`，日增长率缺失日按净值比、整段缺失则原样返回，ETF 分支保持 qfq），除息日不再被记成一天真实下跌；**基准加回股息**（`benchmark_dividend_yield_pct` 默认 2.7%/年，按 252 交易日累乘前缀因子缩放点位序列，任意区间比值自动约掉公共前缀 = 按区间天数折算），消除"基金侧含分红、基准不含"造成的约 2.7pp/年虚高超额
- [新功能] 复盘信号同向率改双口径：绝对口径（`hit_rate`，语义不变）+ 超额口径（`signal_stats.excess`，buy 跑赢基准=命中、sell 跑输=命中），报告与卡片都以超额为主、绝对为对照 —— 实测 2026-09-02→10-02 下跌市里绝对 27.3% / 超额 63.6%，绝对量的是市场方向（beta），不是选基能力
- [新功能] `GET|PUT /api/analysis/caliber` 读写生效口径（回滚键 `review_nav_adjusted=0` / `benchmark_dividend_yield_pct=0`，脏值回落默认、股息率夹在 0~10）；复盘页「口径设置」可直接切换并保存
- [改进] 口径头三行（净值口径 / 基准口径 / 计息口径）由后端生成并随 `report.caliber.lines` 返回，复盘报告、基金 PK 报告、AI 每日简报 payload（含"引用区间收益/超额/回测数字必须照抄这三行"的提示词约束）、回测页与 PK 页前端文案共用同一来源；回滚后文案自动跟随变化，不再硬编码
- [测试] 新增 `tests/test_return_caliber.py`（20 例：分红日复权不再记成下跌、日增长率缺失/NaN 行处理、股息累乘前缀在等长窗口口径一致、0 股息恒等与不改动缓存列表、口径配置读写与脏值回落、基准含息涨幅、报告口径头）；`test_review.py` 补超额口径分桶与"上涨市绝对口径恒真"两例、`test_advice_hit_mode.py` 补基准侧含息能翻掉超额判定一例；全量 572 passed，前端 `tsc --noEmit` + `vite build` 通过，浏览器实测复盘/PK/口径开关往返并复原开发库
- [测试] 新增 `tests/test_backtest_measurement.py`（13 例：仓位状态机、三基线、样本下限与覆盖度、回滚键）与 `tests/test_advice_hit_mode.py`（33 例：双口径判定四象限、窗口成熟度与基准对齐、口径开关往返与脏值回落、`by_mode` 分母、校准随口径移动、工单入账去重、回填的按基金去重与"间隔只在基金之间"防封不变量）；全量 548 passed

**第二批 2B — 防线补齐（Q8 / Q9 / Q10，2026-10-02）**
- [修复] 棺材钉否决的证据不足也照样开枪：判定用 `end_idx = min(recovery_start + 60, n)`，`start` 接近序列末尾时恢复期实际只观测到 1~几天，却仍按"60 日未恢复"落否决（日志理由写着"回撤 30%"，看起来证据充分）。影响方向恰好最糟——昨天刚暴跌、可能正是最便宜的候选被整体剔除，而"是否已恢复"本来就该由那 60 日说了算。现改为**恢复窗口未完整观测则不参与判定**（`observed = n - recovery_start < recovery_days → continue`），并按 Q8 拍板加"B 为展示"：这类形态写入 `quality_warnings`（"棺材钉形态待确认：最近一次回撤 30% 之后仅观测 10 个交易日（判定需 60 日恢复期），本轮暂不否决"），经 `signal.quality_warnings` 落到仪表盘观望位、AI 调仓清单与 `ai_tools`，观望的理由看得见
- [改进] 判定内核收敛成一次扫描：`_scan_coffin_nail()` 同时返回 `(vetoed, pending_warning)`，`check_coffin_nail_pattern()`（供 `pre_filter` 与既有测试使用的 2 元组合约不变）与 `coffin_nail_pending_warning()` 只是两个薄包装；pending 取"最近一次"（`start` 越大越新，直接覆盖），不会把一年里每个未走完的窗口都念一遍
- [新功能] 回滚键 `quality_filter_config.coffin_nail_require_full_recovery_window`（默认 1，置 0 回到"昨天暴跌也否决"），因为是 int 键，自动出现在 `GET|PUT /api/system/quality-config` 与「质量过滤」页的 ⚰️ 前置否决-棺材钉 分组，无需前端改动（浏览器实测：新参数渲染、UI 保存→`system_config` 追加写入且不动该列既有的 3 个覆盖键、恢复原值）
- [测试] `tests/test_quality_filter.py` 新增 `TestCoffinNailPendingWindow`（6 例：恢复期只观测 10 日不否决、观测满 80 日仍否决、待确认文案含实际观测天数、干净基金无标注、回滚键回到旧行为且不产标注、`build_result` 把标注带进 `warnings`）；全量 578 passed。**信号漂移**：纯放开方向，只影响"近 80 个交易日内暴跌 ≥20%"且历史 ≥252 日的基金，原先被误否决者重新进入评分池 → buy/sell 数量可能上升
- [遗留] 设计文档原计划"上线前先跑一遍只读统计给出命中清单"**做不到**：被否决基金根本不落 `analysis_results`（该表无 veto 列），`fund_data_caches` 存的是阶段涨幅而非净值序列，要复现判定就得为 57 只池子重取净值——与防封禁红线冲突（且 ETF k 线端点当前正在对上游断连）。改为随下一轮正常分析自然观察：待确认标注会写进 `analysis_results.quality_warnings`，`select fund_id, analysis_date from analysis_results where quality_warnings like '%棺材钉形态待确认%'` 即得影响面清单，零额外请求。2026-10-02 借 Q9 的单只真实跑（007491 南方信息创新混合C）已观察到第一例命中：`最近一次回撤 20% 之后仅观测 30 个交易日` —— 旧口径下这一只会被直接否决
- [修复] Q9 陈旧净值可以拿到今天的信号：`_no_nav_reason` 只判"空序列"，全仓没有任何地方把净值 as-of 日期（`fund_data.date`）与今天比较。上游静默回旧缓存、或基金暂停披露时，会以 `analysis_date=今天` 落一条陈旧信号，回测再把这段尾巴当真实交易日重放。现按**交易日**计量披露缺口：落后 >5 只在 `quality_warnings` 标注"以下信号基于旧净值"，>10 直接跳过评分并复用 `analysis.data_missing` 埋点（流式路径同步进 `complete.failed`），QDII/跨境/96 开头互认基金 T+2 才披露是常态，两档各放宽 5 个交易日
- [改进] 缺口口径两处容易做错的地方：① **开区间两端都不计**（净值=上一交易日 → 缺口 0，不把正常披露节奏当滞后；今天当天的净值通常晚间才发，也不计）；② 周末一律休市、调休补班的周六不计（与 `is_a_share_trading_day_async` 同口径），法定节假日读库内 `holiday_calendar` —— 一轮一次 DB 查询、**零上游请求**，春节连休 9 天不会被算成 9 日滞后；表为空时退化为"周一至周五"，方向偏保守（只会多计不会放过停披基金）；净值日期缺失或解析失败按"不判"处理，取数字段格式变化不该表现为整池否决
- [新功能] `analysis_results.nav_as_of_date` 列（YYYY-MM-DD，启动迁移，旧行 NULL）随每次评分落库并经 `AnalysisResultOut` 透出 —— 与 `analysis_date` 的差就是披露缺口，§3 影子评分层的 `nav_as_of_date` 依赖同一列；回滚键 `nav_staleness_max_trading_days=0` 只关判定，不影响这一列继续写。「质量过滤」页新增 🕒 净值新鲜度 分组（3 个 int 参数，42 参数 / 8 组），浏览器实测渲染与读写正常
- [测试] 新增 `tests/test_nav_freshness.py`（25 例：缺口计数含跨周末/长假/补班周六与同日和未来日期归零、库内休市日按窗口读取、`>5`/`>10`/QDII 放宽/`None` 不判/回滚键关闭 四档判定、慢披露识别（名称/标签/96 代码）、`_score_and_store` 真实路径的否决跳过 + 埋点、标注与 as-of 落库、门槛关闭时不查日历）；全量 603 passed，前端 `tsc --noEmit` + `vite build` 通过，并用单只真实跑验证 `nav_as_of_date=2026-09-30` 落库（跑完清理该验证行，开发库复原）

**第二批 2B-3 — 调仓工单的权重口径与执行成本（Q10，2026-10-02）**
- [修复] 组合权重里的 `1.0` 哨兵（Q10-B，纯 bug 立即改）：旧口径 `shares * cost_nav if cost_nav else 1.0` 把**漏填成本价**的那只持仓折算成 1 元，归一后真实规模 16200 元的持仓只剩 ≈0.006% 权重 —— 集中度告警、QDII 权重、换仓配对全按这个假数走，且只在"全部缺成本"时才提示，混合场景静默失真。现改为 `shares/Σshares` 份额比例兜底（量纲仍是元），并逐只标注口径来源
- [改进] 权重按**市值**而非成本（Q10-A）：`_position_valuations()` 三档链 实时净值市值 → 成本市值 → 份额×组合平均单位价值，每只附 `weight_basis`（market/cost/shares），缺档时把代码列进 caveat 并说明"浮盈浮亏未计入的那部分会让集中度/QDII 权重偏乐观"。**红线**：净值只读 `FundRealtimeService.peek_cached_nav()`（新增的只读缓存窥探，`max_age` 7 天，未命中不回退拉取），为权重逐只请求净值与防封禁预算直接冲突，因此本改动**零新增上游请求**；缓存读取失败只降级口径、不炸整张工单
- [新功能] 持有期与阶梯赎回费（Q10-C，分两步的第一、二步同批落地）：`user_positions.first_buy_date` 可空列（启动迁移，**不从 `created_at` 回填** —— 入系统时间不是实际买入日）；新纯函数模块 `backend/ai/holding_fee.py`（`parse_fee_ladder` 逐行校验、全脏回落默认阶梯 `[[7,1.5],[365,0.5],[null,0.25]]`；持有期按**自然日**，与基金合同"持续持有期少于 7 日"同口径）；卖出/换仓项附 `holding_days / estimated_fee_pct`，理由尾追"持有 N 天，适用赎回费 X%"
- [改进] 执行成本闸门：适用费率 ≥ `redemption_fee_penalize_pct`（默认 1.0 → 只有 7 日 1.5% 这档落惩罚，0.5%/0.25% 两档只标注不拦）的卖出建议**降级为观望**，`confirm_days` 取"再持有几日落到下一档"，理由里保留恶化佐证不隐瞒 —— "该卖"与"值得卖"是两件事，但分数恶化这件事不能因为费率就被抹掉。未填首买日一律按「未知」处理（`holding_days=None`，不做费用约束），文案显式点名"持有期未知（未填首次买入日），未做赎回费约束"：默认成 0 天等于给每只持仓套 1.5% 惩罚，整张工单会全被拦掉
- [新功能] 三个键进 `QUALITY_CONFIG`：`redemption_fee_enabled`（=0 即整段撤掉，工单回到旧样子，连费率字段与降级都不出现）/ `redemption_fee_penalize_pct` / `redemption_fee_ladder`（list 值，配置页只渲染数值参数，故经 `quality_filter_config` JSON 覆盖，`PARAM_META` 描述里已注明）；「质量过滤」页新增 💸 持有期与赎回费 分组，参数总数 42→44、分组 8→9
- [新功能] 首买日录入：建仓对话框 `type=date` 字段（说明它是持有期唯一输入、可补录历史）；PUT 用 `model_fields_set` 区分"字段没出现=不动"与"显式 null=清除"，未来日期 400（与 POST/CSV 同口径，否则持有期算成 0 天会把全池建议按惩罚档拦掉）；`upsert_position(first_buy_date=None)` 语义是**不改**，所以不带日期列的 CSV 再导入不会抹掉手工补录；CSV 认「首次买入日期/买入日期/买入时间/确认日期/交易日期」等别名，`2026/9/3`、`2026年9月3日`、`20260903` 都吃，解析不出或晚于今天当未填并逐行报错而不拦整份导入
- [改进] 前端「我的持仓」：Tab1 增「持有期」列（日期 + 持有 N 天 / 持有 —）与缺首买日的补录提示条，Tab2 显示生效阶梯文案、卖出行的费率 chips、同赛道换仓表新增「卖侧持有/费率」列、持仓 chips 的 tooltip 标注权重口径与持有天数；Agent 的 `get_positions` 工具输出同步带 `first_buy_date/holding_days`，AI 解读工单时看得见成本闸门
- [修复] 测试污染开发库 `advice_log`：调仓 Agent 任务的 SSE 测试带 `record_advice=True`，`AdviceLearningStore` 用同步 sqlite 直连 `settings` 里的库路径，把 004011/011452/006751 这些**测试基金**写进了真实命中率样本（Q7 的校准输入）→ conftest 新增 autouse `_isolate_advice_store`（同 `_isolate_error_log_store` 预案：改 settings 路径 + 重建单例），存量 3 条测试行已清理，重跑全量确认零落库
- [测试] `tests/test_positions_rebalance.py` 16→37 例：阶梯纯函数 6 例（排序与 None 收尾、脏行丢弃与全脏回落、6/7/365 边界、下一档天数、阶梯文案、宽松日期）、权重口径 4 例（无缓存→成本市值 50/50 且哨兵不再出现、缓存命中 60/40 与 `weight_basis`、超 7 天缓存忽略、双缺→份额比例 66.7/33.3）、费用闸门 5 例（3 天 1.5% 惩罚档降级为观望且 `confirm_days=4`、100 天 0.5% 正常卖出并随换仓带 `sell_fee_pct`、未填首买日"持有期未知"不拦、`redemption_fee_enabled=0` 工单回到旧样子、等权近似模式不声称持有期）、首买日输入与 PUT 校验 6 例；全量 624 passed，`tsc --noEmit` + `vite build` 通过；浏览器实测持仓页补录/清除往返、Tab2 阶梯文案与权重 tooltip（`权重口径 成本市值（净值未命中）｜持有 34 天`）、质量过滤页 💸 分组，验证期写入的 `first_buy_date` 与 `quality_filter_config` 均已复原

**第二批 §3 — 影子评分层与每日口径分歧报表（2026-10-02）**
- [新功能] 2C 的取证地基先就位：同一轮分析用两套口径各算一次，**新口径只写影子列**，生产 `weighted_score/signal_direction/operation_advice` 一字不动。`analysis_results` 新增 6 个可空列（启动迁移，**不回填历史** —— 旧轮没跑过影子，补出来的数会伪装成对照样本）：`shadow_score / shadow_direction / shadow_variant / shadow_detail` + 元数据 `pool_size`（本轮截面样本基金数，Q5）/ `factor_coverage`（有效权重和/总权重，Q4）。回滚键 `system_config.shadow_scoring_enabled`（关闭时影子列显式写 NULL；2C 注册后默认改为**开**，见下方 2C 条目）
- [新功能] `backend/engines/shadow_scoring.py`：变体注册表（`register_variant/available_variants/resolve_variant`，2C 的新口径按名注册进来即可，不必再动分析链）+ `ShadowContext`/`ShadowSignal` + `compute_shadow`。三种失败模式（开关关闭 / 注册表为空 / 变体自己抛异常）一律收敛成 `(None, None)` 并 `logger.warning`，**影子崩了绝不能带崩生产那一行** —— 实测崩变体后生产列照常落库、`pool_size` 照常记
- [改进] 每轮都写全六个影子/元数据键（无影子时显式 NULL）：关开关或换变体后重跑，不会留着上一轮的影子冒充本轮对照。影子配置一轮读一次 DB，影子算法纯 Python，分歧报表只有 2 条本地 SQL —— **零新增上游请求**，符合防封禁红线
- [新功能] `backend/services/shadow_report_service.py` 每日口径分歧报表：窗口 1~60 个交易日，**只统计 `shadow_direction` 非 NULL 的行**（分母掺入无对照的行会把分歧比例假性压低）；`stable_days` 取"最近连续达标天数"（判据一是"连续 5 日 <15%"，整窗平均会掩盖刚破线的日子）；方向迁移矩阵按 `old->new` 计，`buy_to_other/sell_to_other` 单独给数；分档用**修正前** `original_score` 对 `dynamic_buy/sell_threshold`（`above_buy/middle/below_sell/unknown`），与仪表盘五档同口径；按变体分行给出各自日期区间，变体切换天然打断可比性
- [改进] 报表的三条诚实约束写进文案而非藏在代码里：① 注册表为空（内置 2C 变体没被导入链走到）时明确说"这不是故障，是还没有可对比的口径"，不出全零表；② **判据二（影子在 Q6 新基线上的超额不劣于旧口径）本表不自动判定** —— 影子口径没有独立回测曲线，硬算就是编数字；③ 解读注意清单覆盖 小样本日（<10 行/日）、多变体混窗、`pool_size<20`、`factor_coverage<85%`（截面标准化样本本身偏薄）、零分歧、判据二 六类易误读情形
- [新功能] `GET|PUT /api/analysis/shadow-config`（读注册表与判据常量、写非法变体名 400）与 `GET /api/analysis/shadow-divergence?days=`（越界 clamp 1~60，非数字回落 10）；「评分配置」页新增「影子评分口径」卡：开关与变体切换、窗口选择、日分歧表（影子/总行、分歧比例按阈值着色、平均分差、最大绝对分差、方向迁移 chips、池规模/覆盖率）、分档表、变体 chips、结论 Alert 与「解读注意」清单
- [测试] 新增 `tests/test_shadow_scoring.py`（42 例：注册表顺序与重名覆盖、`factor_coverage` 的 1.0/0.6/None vs 0.0/负权重/脏权重/缺分项、`compute_shadow` 四类失败模式、配置读写与非法变体不落库、**生产隔离**（同输入影子关/开两次落库的生产列逐字段相等而影子列不同）、崩变体仍保生产行、关开关重跑清掉旧影子、批量与流式两条路径的 `pool_size`（含流式失败基金不计入）、报表的无数据成因/NULL 行不稀释/连续达标 3→5 跨判据/窗口裁剪/分档与多变体 caveat/聚合迁移/零分歧/天数上下限、三个端点）；全量 666 passed，`tsc --noEmit` + `vite build` 通过；真实单只跑验证影子列与元数据落库，分歧卡的空态与开关往返在浏览器实测，满表截图取自在 `/tmp` 的**开发库副本**（不污染真实库），验证行与两个配置键全部清理、开发库复原为 123 行 / 最近 2026-07-22

**第二批 2C — 评分结构新口径注册为影子变体 `caliber_2c`（Q2+Q3+Q4+Q1，生产未切，2026-10-02）**

- [新功能] `backend/engines/shadow_variants.py`：2C 的四项裁定整体做成一个影子变体 `caliber_2c`，`@register_variant` 挂进 §3 注册表，分析链只多一行模块顶部导入 —— 这正是 §3 预留的"切"的成本控制路径。**生产 `weighted_score/signal_direction` 与 `threshold_ref_total_weight=8.3` 一字未动**，新口径只在每轮并排算一次写 `shadow_*`，输入全部来自生产已算好的东西（质量过滤修正后的因子分值与权重、动态阈值、机构偏置、申购状态），**零新增上游请求**
- [改进] Q2 市场三因子（`market_valuation/market_sentiment/market_fund_flow`，合计 1.8）退出加权和，择时职责完全交给动态阈值；同时**参考权重成对下移**（市场 1.8 + 趋势 0.5 ⇒ 影子侧 8.3 → 6.0）：只清权重不改 ref 会让 `scale=total/ref` 掉到 0.78，市场顺风就从"加分"变成"降门槛"，绕了个后门进来。阈值折算走 `compute_dynamic_thresholds` 对 `scale` 的**齐次性**（影子阈值 = 生产阈值 × 结构项 × 覆盖率项），因此不需要也拿不到市场环境快照，生产自身的 size_shock/drift/regime 调整被原样继承；结构项对"因子齐全、未触簇上限"的基金恰为 1.0，**影子门槛唯一会动的来源是 Q4 覆盖率**，分歧才归得清因
- [新功能] Q3 动量簇去重：`trend_consistency` 移出加权和改**乘性位**（`raw_value==0` 即 mom20/mom60 反号 → 只把 short/mid/accel 三项之和 × 0.8，波动率与绝对分不受牵连；且先查 `data_valid`，缺数据的新基金 raw 同为 0.0，不查就是惩罚它），另加**单簇权重上限** = `pct% × base_weight`（默认 35 × 8.3 ≈ 2.905，基准与 ref **故意解耦**，否则去掉市场因子会顺把动量上限压到 2.275）。压线事实如实记录：趋势移出后动量簇只剩 2.9，**默认参数下簇上限不触发**，它是留给后续重新配权的结构护栏而不是当前旋钮；正交化（accel 取截面回归残差）需要全池输入，`ShadowContext` 是单只基金上下文，按裁定留作第二步
- [新功能] Q4 覆盖率折算真实生效：`data_valid=False` 的权重不再白给（生产按满权重求和，理论满分只有 2.5 的基金要够 1.5 的门槛），阈值乘 `有效权重和 / 总权重`；覆盖率低于 `shadow_2c_min_coverage`（默认 0.6）时影子方向记 **`skip`** —— 语义是"新口径下这只基金本轮不落库"，既不洗成 hold（"没有记录"和"观望"是两件事，洗掉会系统性低估这批改动的影响面），也不留 NULL（NULL 表示当日没跑影子），判据一按分歧计（保守）
- [改进] Q1 五档在新口径里只提供措辞：方向由动态阈值决定，`detail.tier.conflict_with_dynamic` 记下"档位说加仓但动态阈值判观望"的冲突数 —— 这批就是切换时必须给五档标"仅供参考"的数量
- [改进] 影子侧复用 `apply_otc_trade_constraint`（暂停申购/封闭期 → 买入降观望，结果记 `detail.otc_downgraded`）：不套这一步会把"新口径也喊买但场外买不到"记成一致，把真实分歧留到明天
- [新功能] 4 个口径参数进 `QUALITY_CONFIG`（44→48 数值参数、9→10 分组，组名「影子口径2C」），因此 `GET|PUT /api/system/quality-config` 与「质量过滤」页自动渲染读写，无需新端点；越界在变体内部钳位并留 `detail.params_clamped` + `logger.warning`（那条 PUT 链只校验"是数字"，写成 500 会静默改掉整个口径，报表的全零分歧会被读成"两口径一致"，比崩溃更坏）。`GET /api/analysis/shadow-config` 新增 `variant_descriptions` / `caliber_params`（含 value/default/out_of_range），前端不再另写一份区间
- [新功能] 影子开关默认改为**开**（`DEFAULT_SHADOW_ENABLED=True`）：判据一要连续 5 个交易日的对照样本，默认关等于把取证排到"记得去点一下"那天；开着的代价只有每轮一次纯 Python 加权与 6 个可空列，零上游请求、零生产信号影响
- [改进] 分歧报表与「影子评分口径」卡新增 skip 口径：日表「其中 skip」列、汇总 chip、caveat 文案（明确"skip 计入分歧"）；变体下拉显示当前口径说明与 4 个参数 chips（tooltip 给出管哪一项、合法区间、默认值，越界标警告色，并注明"改这些数只影响影子列"）；方向迁移 chips 中文化（`buy->skip` → 加仓→不出记录）
- [测试] 新增 `tests/test_shadow_2c_variant.py`（42 例，期望值全部手算）：Q2 清零后总分与 `threshold_ref {prod:8.3, shadow:6.0}` 且 structure=1.0、移出量按**配置权重**口径、只剩市场+趋势时不写影子列；Q3 簇上限 50%×4.0 裁到 2.0（`applied` 留痕）与默认上限不触发、乘性位收分/缺数据不罚/参数 0·1·2 关闭；Q4 覆盖率分母不含被清零的权重、半缺时阈值 [0.75,−0.75]、覆盖率 0.4 出 skip、`min_coverage=0` 关折算、ref=0 分支 `factor==coverage`；Q1 档位文案与冲突位；OTC 暂停申购降 hold 而"限大额"不降、偏置、±8.5 钳位、**共享输入对象不被原地改**、`detail` 可 JSON 序列化、参数钳位留痕；**生产隔离**（真实 11 因子权重表挂真变体跑 `_score_and_store` 两次，10 个生产列逐字段相等 + 影子关闭行影子列为 NULL + 开启行 `shadow_score≈6.0`、`removed_weights={market:1.8, trend_consistency:0.5}`）；报表对真变体的 skip 计数与 caveat；端点透出 4 参数与空注册表文案。全量 **708 passed**，`tsc --noEmit` + `vite build` 通过。`tests/test_shadow_scoring.py` 的 autouse fixture 改为先清空注册表（内置变体在导入时即注册，否则"注册表为空"分支测不到）
- [改进] 红线取证按"不能靠读代码相信"的口径做了一遍真实的：在 `/tmp` 的**开发库副本**上另起 :8010 后端跑两只基金，把 12 个生产列快照下来 → `shadow_scoring_enabled=0` 重跑同一轮 → 逐字段比对**零差异**且影子列回到 NULL → 再开回来重跑，浏览器实测「影子评分口径」卡（变体 `caliber_2c`、口径说明、4 个参数 chip、skip 列、分档表、解读注意）与参数 tooltip 文案；副本与临时端口全部回收，开发库仍 123 行 / 0 影子行，上游请求数 0
- [遗留] 判据二（影子在 Q6 新基线上的超额不劣于旧口径）仍需人工另跑一套影子回测，报表不自动判定；库里样本 6 个信号日 / 123 行，判据一要连续 5 个交易日达标才有结论，池子只有两只时截面标准化本身不稳

**第二批 2D — 卫生（Q5 标注 / Q12 诊断口径 / Q13 死配置 / Q14 CI，2026-10-02）**

- [改进] Q5 评分口径标注：`weighted_score` 一直是**池内相对分**（6 个截面 z 因子加权，权重 5.2 / 总 8.3，当日池内均值 0），但所有展示面都把它当绝对刻度读——跨日 -1.8→+0.3 里混着"池子换了几只基金"。现由单一文案源 `scoring_engine.score_caliber_note(pool_size)` 与前端同值常量 `utils/format.ts::scoreCaliberNote` / `THIN_POOL_SIZE=20` 统一产出「池内相对分（当日 N 只参与截面标准化）」，`pool_size<20` 追加"池子偏薄、截面标准化本身不稳定"，**旧行 NULL 说成"样本数未记录，不能跨期/跨池比较"而不是补一个看起来可信的数**。覆盖仪表盘评分表头与详情行、历史报告表头与详情、报告引擎总分行与市场段、AI 简报 payload 与逐只明细、Skill 上下文、Agent 工具描述与 `signal_overview` 预设任务 caveat、`AnalysisResultOut.pool_size`；持仓页「分差」列头补 tooltip（可用于给候选配对排序，不是"多 3 分就值 3 分"的绝对刻度）
- [改进] Q12 因子诊断的 IC 从"看起来显著"改成"真的可比"：T+h 前瞻收益逐日取样会让相邻样本重叠 h−1 天，RankIC 序列自相关、`IR = mean/std` 被系统性放大 ≈√h（horizon=20 时约 4.5 倍）。`daily_ic` 默认改为每 h 个有效交易日取一个**非重叠**样本，`days`（独立周期数）与 `n_days_valid`（重叠口径原始长度）同时给出、`sampling` 标明口径；新增 `rank_ic_ir_annualized = IR × √(252/h)`；用正则化不完全贝塔函数算双侧 Student-t p，再经 `benjamini_hochberg` 对同窗口全因子做多重比较校正落 `rank_ic_q_bh` / `significant`（实现选 BH 而非设计表里的 Bonferroni：一次审计同时看 11 因子 × 多 horizon，Bonferroni 会把弱真信号全压成"不显著"，等于没有结论）；独立周期 < `MIN_IC_PERIODS_FOR_CONCLUSION=8` 时只出过程量、caveat 明写"不要据此调整因子权重"；IC 序列零方差时 IR/p/q/significant 一律 None，绝不显示"✓"；`summary_md` 扩到 11 列并把口径行写进表格本身（**没有前端诊断表，markdown 表就是唯一展示面**），诊断 Agent 预设提示词同步要求引用非重叠周期 / BH q / 年化 IR。统计全部跑在已落库样本上，**零上游请求**；回滚键 `FactorAuditService.audit(overlapping_ic=True)`
- [修复] Q13 死配置清扫（一）signal_rules：10 个因子的计算函数从不读 DB 里的 `signal_rules`（判据可机检——函数源码不含 `rules_from_params`），但因子页仍给它们开规则编辑器，用户改完得分纹丝不动。现由 `factor_engine.SIGNAL_RULES_INERT_FACTORS` 做唯一真相（与源码互验，`tests/test_dead_config.py` 用 `inspect.getsource` 双向断言，防清单与实现漂移）、`calculate_all` 注入前查表、`database._clear_inert_signal_rules()` 启动幂等迁移清掉存量规则数组并把 5 个种子因子的 seed 置空、`FactorOut.signal_rules_effective` 经 `/api/factors` 透出，前端对无效因子打「规则不适用」chip 并说明得分实际来源。真读规则的 4 个因子（`drawdown_recovery` + 3 个 market 因子）由反向用例保护，迁移不会误删
- [修复] Q13 死配置清扫（二）口径与死字段：`backend/utils/stats.py::percentile_rank_inclusive` 统一分位定义（含当前值的 `<=` 占比；此前 `index_valuation_service` 用严格 `<`、`market_regime_service` 用 `<=`，同一指数两处差 1 个点）；仪表盘展示分位线改名 `DASHBOARD_PE_LOW/HIGH_PERCENTILE`（30/70）并注释清楚它与策略极端档 `extreme_*_valuation_pct`（0.15/0.85）是两套不同用途的分区，改一边不影响另一边；MACD 文档删掉从未实现的"放量"档（场外基金没有可靠分钟级成交序列）；`size_stability` 文档写明量纲（深交所"基金份额"是份，须 × 最新净值换成元才匹配 2 亿~50 亿档）与**当前未启用**，适配器改为落元序列并去掉重复的钳位；删除无人消费且标注错误的 `FundData.pb`（实为 csindex 市盈率2）与从未被任何因子读取的 `volume_history`。**这批全部是文档 / 死字段 / 未启用因子，生产信号零变化**
- [新功能] Q14 CI 闸门：`.github/workflows/` 此前只有 `docker-publish.yml` 且**0 条 `run:`**（号称有 CI，实际从未拦过任何东西）。新增 `ci.yml` 两条 job：`backend-tests`（python 3.9 对齐后端 Dockerfile，`pip install -r backend/requirements.txt`，`PYTHONPATH=. python -m pytest -q`）与 `frontend-build`（node 20 对齐前端 Dockerfile，`npm ci && npm run build`），依赖缓存分别打在 `requirements.txt` / `package-lock.json` 上；测试本身零上游请求、无需 .env，因此 CI 不会撞"不要触发限流"的红线。lint / type-check（ruff、mypy、eslint）有意留到 Q14b，不与功能改动挤进同一个闸门；回滚 = 删该文件，而 `TestCiActuallyGates` 会断言两条 job 的 `run:` 里确实有 pytest 与 `npm run build`、版本与 Dockerfile 一致，防止再退回"纸面 CI"
- [测试] 新增 `tests/test_score_caliber.py`（Q5 文案与前后端同值、NULL/偏薄/边界三档、透出链）与 `tests/test_dead_config.py`（22 例，覆盖 Q13 五个子项 + Q14 CI 自检：清单与源码互验、seed 双向、`calculate_all` 金丝雀规则注入、迁移幂等与真规则因子保护、API 与前端表面、MACD 文档分区、`FundData` 死字段、分位单一定义与两套分区命名、`size_stability` 量纲、CI YAML 解析与版本对齐）；`tests/test_factor_audit.py` 补 Q12 用例块（双侧 t 单调性与 df=1 柯西退化、零方差判 None、BH 手算期望、非重叠采样长度/均值/年化与插入序无关、薄日跳过而非抽样、回滚开关、报表列与 caveat、预设提示词）；`test_logging_and_retry.py` 的份额→元转换用例补 `close` 依赖并新增"无净值不得落原始份额"。回归：`pytest -q` **770 passed**，`tsc --noEmit` 干净，`vite build` ✓ 8.77s；后端重启后 `/api/factors` 实测 7 个死配置因子规则数归 0 且 `signal_rules_effective=false`、4 个活因子保持 3/5/5/5；浏览器实测 http://localhost:5173/factors 「规则不适用」chip 恰好落在那 7 个代码上、tooltip 文案正常

**§3 加固 — 判据一的"连续 5 个交易日"改按 A 股交易日历数（2026-10-02，攒样本前置）**

- [修复] `shadow_report_service.build()` 原来按**"有影子数据的日期"倒序**数连续达标天数，而 `analysis_date=当天` 是手点一轮就落一行的：国庆/中秋连休里天天点，连续 5 天可以全是休市日，判据一被"手痒"刷满；反过来漏跑一个真实交易日（周五 + 下周一）却会被读成"连续两天"。现由 `_stable_trading_days()` 走库内 `holiday_calendar`（`load_off_day_dates` 一次查询，与 Q9 同一张表，**零上游请求**）判定交易日：周末一律休市（**调休补班的周六股市也不开**，与 `trading_calendar.is_a_share_trading_day_async` 同口径），连续序列必须是日历上的**相邻交易日**。日表新增 `trading_day` 标（休市轮次照样展示、只是不进连续），汇总新增 `non_trading_rounds`，两类新 caveat：非交易日轮次被剔除（列出日期）、连续达标顶到窗口边界时明说"真实天数可能被 days 截断"（`_load` 为此多取一个日期）。回滚 = 把 `stable_days` 改回按 `daily` 倒序计数
- [修复] 影子分歧端点自己触发内置变体注册：注册表是 import 时填充的，进程刚起、前端直接打开分歧卡（没先读 `/shadow-config`）时，`registered_variants` 会是空的，报表老实说"内置的 2C 新口径没被注册进来 —— 这不是故障而是当前没有可对比的口径"，但**读数字的人会以为 2C 没上线**。`GET /api/analysis/shadow-divergence` 现与 `/shadow-config` 一样带 `from backend.engines import shadow_variants`，`days` 参数描述同步改成"最近 N 个有影子数据的**日期**（休市轮次仍展示，不计入连续交易日）"；`test_both_shadow_routes_trigger_builtin_registration` 用 `inspect.getsource` 机检两个端点都有这行（同 Q13 判据的机检法）
- [测试] `tests/test_shadow_scoring.py` 的 `D1~D6` 从"9/21 起连续 6 个自然日"改成真工作日（9/21~9/25 + 9/28），新增 `TestStableDaysUseTradingCalendar` 6 例：周六轮次不进连续且带 caveat、**同一批数据插 `holiday_calendar` 行前后结论必须不同**（9/25 中秋休市 ⇒ 5→4）、缺 9/23 打断连续（1 天）补上后连成 4 天、五个轮次全是周六 ⇒ `stable_days=0` 且判据一不满足、窗口截断的 caveat 有/无对照、`is_a_share_trading_day` / `prev_trading_day` 单元（国庆后回到 9/30、补班周六仍休市、日历异常时按 `MAX_TRADING_BACKTRACK=40` 返回 None 不无限回溯）。回归：`pytest -q` **777 passed**（纯 DB 与内存库，上游请求 0）
