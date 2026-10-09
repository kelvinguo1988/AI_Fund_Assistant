/**
 * TypeScript 类型定义 — 对应所有后端 API Schema
 */

/* ── 通用 ────────────────────────────────────────────────────────── */
export interface ApiResponse<T> {
  code: number;
  data: T | null;
  message: string;
}

export interface PaginatedData<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export type PaginatedResponse<T> = ApiResponse<PaginatedData<T>>;

/* ── 基金 ────────────────────────────────────────────────────────── */
export interface FundCreate {
  code: string;
  name: string;
  fund_type: 'etf' | 'otc';
  tags?: string | null;
}

export interface FundUpdate {
  name?: string | null;
  fund_type?: 'etf' | 'otc' | null;
  tags?: string | null;
  starred?: boolean | null;
  status?: 'active' | 'disabled' | null;
}

export interface FundOut {
  id: number;
  code: string;
  name: string;
  fund_type: string;
  tags: string | null;
  fund_type_official?: string | null;
  benchmark_text?: string | null;
  exposure_tags?: string | null;
  starred: boolean;
  status: string;
  created_at: string;
  updated_at: string;
}

/* ── 因子 ────────────────────────────────────────────────────────── */
export interface FactorCreate {
  name: string;
  code: string;
  data_field?: string | null;
  data_fields?: string[] | null;
  weight: number;
  direction: 'positive' | 'negative';
  params?: Record<string, unknown> | null;
  formula?: string | null;
  window?: number | null;
  window_unit?: 'day' | 'quarter' | null;
  signal_rules?: Array<{ condition: string; score: number }> | null;
  normalization?: string | null;
  normalization_config?: Record<string, unknown> | null;
  sort_order: number;
}

export interface FactorUpdate {
  name?: string | null;
  data_field?: string | null;
  data_fields?: string[] | null;
  weight?: number | null;
  direction?: 'positive' | 'negative' | null;
  params?: Record<string, unknown> | null;
  formula?: string | null;
  window?: number | null;
  window_unit?: 'day' | 'quarter' | null;
  signal_rules?: Array<{ condition: string; score: number }> | null;
  normalization?: string | null;
  normalization_config?: Record<string, unknown> | null;
  status?: 'active' | 'disabled' | null;
  sort_order?: number | null;
}

export interface FactorOut {
  id: number;
  name: string;
  code: string;
  data_field: string | null;
  data_fields: string[] | null;
  weight: number;
  direction: string;
  params: Record<string, unknown> | null;
  formula: string | null;
  window: number | null;
  window_unit: string | null;
  signal_rules: Array<{ condition: string; score: number }> | null;
  normalization: string;
  normalization_config: Record<string, unknown> | null;
  status: string;
  sort_order: number;
  weight_percentage: number;
  /** false = 该因子的计算函数不读 signal_rules（得分由 z 分档/内嵌逻辑决定），规则编辑无效 */
  signal_rules_effective?: boolean;
}

/** 因子导出载体 */
export interface FactorExportPayload {
  version: string;
  exported_at: string;
  factors: Array<Omit<FactorOut, 'id' | 'status' | 'weight_percentage'>>;
}

/** 因子导入结果 */
export interface FactorImportResult {
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
}

/* ── 信号回测 ─────────────────────────────────────────────────── */
export interface BacktestPoint {
  date: string;
  nav: number;
  nav_return: number;
  strategy_return: number;
  signal_direction: 'buy' | 'sell' | 'hold' | null;
  signal_strength: string | null;
  weighted_score: number | null;
  signal_effectiveness: number | null;
  // Q6 口径可视化：当日实际生效仓位 + 静态半仓基线
  position_applied?: number | null;
  baseline_static_half?: number | null;
}

export interface BacktestSummary {
  fund_code: string;
  fund_name: string;
  period: number;
  total_nav_return: number;
  total_strategy_return: number;
  /** 策略 − 满仓买入持有（上涨市里主要由半仓敞口决定，不是信号能力） */
  excess_return: number;
  max_drawdown: number;
  signal_count: number;
  total_days: number;
  effectiveness_window: number;
  avg_effectiveness: number | null;
  buy_effectiveness: number | null;
  sell_effectiveness: number | null;
  effectiveness_rate: number | null;
  // ── Q6：三条基线 / 样本下限 / 选择偏差标注 ──
  baseline_buy_hold?: number;
  baseline_static_half?: number;
  /** 头号指标：策略 − 静态半仓（"什么都不做"基准） */
  excess_vs_static_half?: number;
  signal_count_non_hold?: number;
  signal_coverage_ratio?: number;
  low_sample?: boolean;
  caveat?: string | null;
  coverage_start_date?: string | null;
  coverage_days?: number;
  pool_size_at?: number | null;
  carry_position?: boolean;
  points: BacktestPoint[];
}

/* ── 推送渠道 ────────────────────────────────────────────────────── */
export interface PushChannelCreate {
  name: string;
  channel_type: 'feishu' | 'qq';
  webhook_url?: string | null;
  token?: string | null;
  config?: Record<string, unknown> | null;
  enabled: boolean;
}

export interface PushChannelUpdate {
  name?: string | null;
  channel_type?: 'feishu' | 'qq' | null;
  webhook_url?: string | null;
  token?: string | null;
  config?: Record<string, unknown> | null;
  enabled?: boolean | null;
}

export interface PushChannelOut {
  id: number;
  name: string;
  channel_type: string;
  webhook_url: string | null;
  token: string | null;
  config: Record<string, unknown> | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

/* ── 调度计划 ────────────────────────────────────────────────────── */
export type ScheduleTaskType = 'analysis_push' | 'ai_daily_brief';

export interface ScheduleCreate {
  name: string;
  cron_expr?: string | null;
  time_point?: string | null;
  task_type: ScheduleTaskType;
  channel_id?: number | null;
  enabled: boolean;
}

export interface ScheduleUpdate {
  name?: string | null;
  cron_expr?: string | null;
  time_point?: string | null;
  task_type?: ScheduleTaskType | null;
  channel_id?: number | null;
  enabled?: boolean | null;
}

export interface ScheduleOut {
  id: number;
  name: string;
  cron_expr: string | null;
  time_point: string | null;
  task_type: string;
  channel_id: number | null;
  enabled: boolean;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

/* ── 报告配置 ────────────────────────────────────────────────────── */
export interface ReportConfigOut {
  id: number;
  name: string;
  item_key: string;
  enabled: boolean;
  sort_order: number;
  created_at: string;
}

export interface ReportConfigUpdate {
  id: number;
  enabled?: boolean | null;
  sort_order?: number | null;
}

/* ── 分析结果 ────────────────────────────────────────────────────── */
export interface FactorScore {
  factor_code: string;
  factor_name: string;
  raw_value: number;
  score: number;
  direction: string;
}

export interface AnalysisResultOut {
  id: number;
  fund_id: number;
  fund_code: string;
  fund_name: string;
  analysis_date: string;
  weighted_score: number;
  signal_direction: 'buy' | 'sell' | 'hold';
  signal_strength: string;
  operation_advice: string;
  factor_scores: FactorScore[];
  created_at: string;
  equity_ratio: number;
  // ── 第零层扩展字段（可选）──
  original_score?: number | null;
  dynamic_buy_threshold?: number | null;
  quality_warnings?: string[] | null;
  /** Q9：本次评分依据的最新净值日期（与 analysis_date 的差 = 披露缺口；旧数据 null） */
  nav_as_of_date?: string | null;
  /** Q5：本轮参与截面标准化的基金数 —— weighted_score 是池内相对分，旧数据 null */
  pool_size?: number | null;
}

/* ── AI 对话 ─────────────────────────────────────────────────────── */
export interface ChatMessage {
  content: string;
  conversation_id?: string | null;
  context_type?: 'single_fund' | 'pool' | 'market' | null;
  fund_id?: number | null;
}

export interface ChatResponse {
  conversation_id: string;
  role: string;
  content: string;
  model_name: string;
}

/* ── 系统配置 ────────────────────────────────────────────────────── */
export interface AIConfigUpdate {
  ai_enabled?: boolean | null;
  ai_model?: string | null;
  ai_api_key?: string | null;
  ai_base_url?: string | null;
  ai_model_id?: string | null;
}

export interface AIModelPreset {
  key: string;
  label: string;
  base_url: string;
  model_name: string;
}

export interface AIConfigOut {
  ai_enabled: boolean;
  ai_model: string;
  ai_base_url: string;
  ai_model_id?: string | null;
  presets: AIModelPreset[];
}

/* ── AI Skills ─────────────────────────────────────────────────── */
export interface AISkill {
  id: number;
  name: string;
  description?: string | null;
  system_prompt: string;
  tool_spec?: string | null;
  enabled: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AISkillImportResult {
  created: number;
  updated: number;
  errors: string[];
}

/* ── 评分阈值配置 ───────────────────────────────────────────────── */
export interface ScoringTier {
  min_score: number;
  label: string;
  signal_direction: 'buy' | 'hold' | 'sell';
  signal_strength: string;
  operation_advice: string;
  equity_ratio: number;
}

export interface ScoringConfigOut {
  score_range_min: number;
  score_range_max: number;
  thresholds: ScoringTier[];
}

export interface ScoringConfigUpdate {
  thresholds: ScoringTier[];
}

/* ── 信号方向类型 ────────────────────────────────────────────────── */
export type SignalDirection = 'buy' | 'sell' | 'hold';

export type SignalStrength =
  | 'light_buy'
  | 'moderate_buy'
  | 'heavy_buy'
  | 'light_sell'
  | 'moderate_sell'
  | 'heavy_sell'
  | 'hold';

/* ── 市场概况 ────────────────────────────────────────────────────── */
export interface CapitalFlow {
  net_amount: number;
  net_ratio: number;
  super_large_net: number;
  large_net: number;
  medium_net: number;
  small_net: number;
}

export interface MarketCapitalFlow {
  date: string;
  sh_index: number | null;
  sh_change: number | null;
  sz_index: number | null;
  sz_change: number | null;
  main_flow: CapitalFlow;
}

export interface SectorFlowItem {
  sector_name: string;
  change_pct: number;
  main_net_inflow: number;
  main_net_ratio: number;
  top_stock: string;
}

export interface SectorFlowRanking {
  timeframe: string;
  by_inflow: SectorFlowItem[];
  by_outflow: SectorFlowItem[];
}

export interface HSGTFlow {
  north_net_buy: number;
  south_net_buy: number;
  date: string;
}

export interface SignalSummary {
  total: number;
  buy_count: number;
  sell_count: number;
  hold_count: number;
  top_buy: AnalysisResultOut[];
  top_sell: AnalysisResultOut[];
}

export interface MarketAdvDecline {
  up_count: number;
  down_count: number;
  total_count: number;
}

export interface MarketTurnover {
  sse_amount: number;
  szse_amount: number;
  total_amount: number;
  prev_total_amount: number;
  change_pct: number;
}

export interface MarketSummaryOut {
  date: string;
  signals: SignalSummary;
  market_flow: MarketCapitalFlow | null;
  sector_flow: SectorFlowRanking[];
  hsgt_flow: HSGTFlow | null;
  adv_decline: MarketAdvDecline | null;
  turnover: MarketTurnover | null;
  updated_at?: string | null;
}

/* ── 市场环境快照（估值分位/情绪/资金面） ─────────────────────────── */
export interface MarketRegimeOut {
  fetched_at: string;
  valuation_percentile: number | null;   // 沪深300 PE 近10年分位 0~1
  valuation_date: string | null;
  valuation_current_pe: number | null;
  valuation_sample_points: number | null;  // 参与分位的历史月点数
  adv_decline_ratio: number | null;     // 涨跌家数比 -1~1
  up_count: number | null;
  down_count: number | null;
  margin_balance: number | null;        // 上交所两融余额（元）
  margin_change_pct_7d: number | null;  // 7 日变化率
  margin_date: string | null;
}

/* ── 基金阶段涨幅 ──────────────────────────────────────────────────── */
export interface FundPeriodReturn {
  code: string;
  name: string;
  return_1m: string | null;
  return_3m: string | null;
  return_6m: string | null;
  return_1y: string | null;
}

/* ── 基金持仓 ──────────────────────────────────────────────────────── */
export interface FundHoldingOut {
  stock_code: string;
  stock_name: string;
  ratio: number | null;
  shares: number | null;
  market_value: number | null;
  quarter_label: string;
}

/* ── 基金经理 ──────────────────────────────────────────────────────── */
export interface FundManagerOut {
  manager_name: string;
  company: string | null;
  tenure_days: number | null;
  asset_scale: number | null;
  best_return: number | null;
}

/* ── 基金变更摘要 ─────────────────────────────────────────────────── */
export interface HoldingChangeItem {
  stock_code: string;
  stock_name: string;
  ratio: number | null;
}

export interface HoldingChanges {
  latest_quarter: string;
  previous_quarter: string;
  added: HoldingChangeItem[];
  removed: HoldingChangeItem[];
}

export interface ManagerChangeInfo {
  manager_name: string;
  company: string | null;
  tenure_days: number | null;
  asset_scale: number | null;
  best_return: number | null;
}

export interface ManagerChanges {
  current: ManagerChangeInfo[];
  history: ManagerChangeInfo[];
  changed: boolean;
}

/* ── 基金详情缓存 ───────────────────────────────────────────────── */
export interface FundDetailResponse {
  funds: FundPeriodReturn[];
  updated_at: string | null;
}

export interface FundDetailStatus {
  has_cache: boolean;
  updated_at: string | null;
  refreshing: boolean;
}

export interface FundChangeSummary {
  fund_id: number;
  fund_code: string;
  fund_name: string;
  holding_changes: HoldingChanges | null;
  manager_changes: ManagerChanges | null;
  tags: string[];
}

/* ── 连通性 ────────────────────────────────────────────────────────── */
export interface ConnectivityItem {
  name: string;
  reachable: boolean;
  latency_ms: number | null;
  error: string | null;
}

export interface ConnectivityResult {
  status: 'ok' | 'partial' | 'fail';
  results: ConnectivityItem[];
  summary: { total: number; reachable: number; unreachable: number };
}

/* ── 数据源运行时健康（进程内状态快照，零网络请求） ─────────────────── */
export interface DataSourceCooldown {
  name: string;
  remaining_seconds: number;
  note: string;
}

export interface DataSourceCache {
  name: string;
  age_seconds: number | null;
  ttl_seconds: number;
  entries: number;
  stale: boolean;
}

export interface DataSourceHealth {
  generated_at: string;
  cooldowns: DataSourceCooldown[];
  caches: DataSourceCache[];
  scheduler: {
    daily_fail_limit: number;
    tripped_today: { schedule_id: number; consecutive_failures: number }[];
  };
  summary: { cooldown_count: number; stale_cache_count: number };
}

/* ── 基金扩展详情 ──────────────────────────────────────────── */
export interface GrandTotalSeries {
  name: string;
  data: [number, number][];
}

export interface FluctuationScale {
  categories: string[];
  series: Array<{ y: number; mom: string }>;
}

export interface HolderStructure {
  categories: string[];
  series: Array<{ name: string; data: number[] }>;
}

export interface AssetAllocation {
  series: Array<{ name: string; data: number[] }>;
}

export interface FundExtendedData {
  name: string;
  grand_total: GrandTotalSeries[] | null;
  fluctuation_scale: FluctuationScale | null;
  holder_structure: HolderStructure | null;
  asset_allocation: AssetAllocation | null;
}

export interface ExtendedDetailResponse {
  funds: Record<string, FundExtendedData>;
  updated_at: string | null;
}

/* ── 质量过滤配置 ──────────────────────────────────────────────────── */
export interface QualityConfigParam {
  key: string;
  value: number;
  default_value: number;
  description: string;
  category: string;
}

export interface QualityConfigOut {
  parameters: QualityConfigParam[];
  updated_at: string | null;
}

export interface QualityConfigUpdate {
  parameters: Array<{ key: string; value: number }>;
}

/* ── 指数估值 ── */
export interface IndexValuation {
  index: string;
  pe: number;
  /** 该指数 PE 在乐咕全历史序列（2007 起·月频）里的分位，0~100 */
  pe_percentile: number;
  /** 分位用的样本月点数 + 序列起点 —— 月频/日频的置信度差别必须可见 */
  sample_points?: number;
  series_start?: string;
  zone: '低估' | '合理' | '高估' | string;
  advice: string;
  updated: string;
}

/* ── 市场温度计（中证800，仅市场层行动参考）── */
export interface TemperatureZoneEvidence {
  zone: string;
  range: string;
  /** 重叠样本数：相邻月的未来区间互相重叠，不能当独立观测看 */
  n: number;
  /** 非重叠（独立）样本数：本序列全期只有十几个，这是温度计不进生产信号的直接原因 */
  n_independent: number;
  avg_forward_12m_pct: number | null;
  median_forward_12m_pct: number | null;
  win_rate_pct: number | null;
  worst_forward_12m_pct: number | null;
}

export interface MarketTemperature {
  index: string;
  date: string;
  close: number;
  pe: number;
  pb: number;
  pe_percentile: number;
  pb_percentile: number;
  temperature: number;
  /** 扩展窗口口径的参照值：与主口径差十几度，只有一致的窗口才有可比性 */
  temperature_expanding: number | null;
  zone: string;
  action: string;
  suggested_equity_pct: number;
  delta_vs_prev_month: number | null;
  rebalance_hint: string;
  history_by_zone: TemperatureZoneEvidence[];
  current_zone_evidence: TemperatureZoneEvidence | null;
  caliber: string;
  /** 历史样本范围：读数日期之外的"起算/预热"信息，挂在分档证据旁，避免被当成读数时效 */
  sample_note: string;
  note: string;
  updated: string;
}

/* ── 影子评分（§3 新旧口径分歧报表）─────────────────────────────────── */
export interface ShadowConfig {
  enabled: boolean;
  /** 生效的变体名（空串 = 注册表为空，即当前没有可对比的口径） */
  variant: string;
  requested_variant: string;
  registered: string[];
  /** 变体名 → 一句话口径说明（后端注册表带的，读分歧数字时要知道这列是哪套口径） */
  variant_descriptions?: Record<string, string>;
  /** 2C 口径自身的参数（生效值 + 默认值 + 越界标记），只影响影子列 */
  caliber_params?: ShadowCaliberParam[];
  divergence_threshold_pct: number;
  stable_days_required: number;
}

export interface ShadowCaliberParam {
  key: string;
  label: string;
  value: number;
  default: number;
  min: number;
  max: number;
  governs: string;
  note?: string;
  out_of_range: boolean;
}

export interface ShadowDailyRow {
  date: string;
  rows: number;
  shadow_rows: number;
  divergent: number;
  divergence_pct: number;
  /** 新口径判"覆盖率不足、本轮不落库"的行数（含义是记录消失，不是观望） */
  skip_rows: number;
  avg_delta: number | null;
  max_abs_delta: number | null;
  big_delta: number;
  migration: Record<string, number>;
  variants: string[];
  pool_size: number | null;
  coverage: number | null;
  low_sample: boolean;
  /** 该轮是否落在 A 股交易日：休市轮次仍展示，但不进判据一的窗口（读数字时要分得清） */
  trading_day: boolean;
}

export interface ShadowDivergence {
  window_days: number;
  divergence_threshold_pct: number;
  stable_days_required: number;
  shadow_enabled: boolean;
  active_variant: string;
  registered_variants: string[];
  daily: ShadowDailyRow[];
  migration: Record<string, number>;
  buckets: Record<string, {
    label: string;
    rows: number;
    divergent: number;
    divergence_pct: number;
    avg_delta: number | null;
    migration: Record<string, number>;
  }>;
  variants: Record<string, { rows: number; first_date: string; last_date: string }>;
  summary: {
    days_with_shadow?: number;
    shadow_rows?: number;
    divergent?: number;
    divergence_pct?: number;
    skip_rows?: number;
    avg_divergence_pct?: number;
    /** 判据一窗口：末尾连续有对照的交易日里，最后 stable_days_required 天 */
    criterion_days?: number;
    criterion_dates?: string[];
    /** 窗口内"有影子对照的行"总数（判据的分母，不是全表行数） */
    criterion_rows?: number;
    criterion_divergent?: number;
    criterion_pct?: number | null;
    /** 合并分歧率的 Wilson 单侧 95% 置信上界：判据一比的是这个数，不是点估 */
    criterion_upper_pct?: number | null;
    /** 末尾连续有影子对照的交易日数（漏跑一个真实交易日即重新攒） */
    consecutive_days?: number;
    criterion_window_complete?: boolean;
    /** 当前分母下还能压进判据线的最多分歧只数 */
    /** 该分母能容忍的最多分歧只数；-1 = 零分歧也压不进 15% 线（薄池无解），此时读 note */
    criterion_tolerance?: number;
    /** 后端拼好的一句话，前端不再各自翻译 -1 */
    criterion_tolerance_note?: string;
    non_trading_rounds?: number;
    migration?: Record<string, number>;
    buy_to_other?: number;
    sell_to_other?: number;
  };
  caveats: string[];
  meets_ratio_criterion: boolean;
  conclusion: string;
}
