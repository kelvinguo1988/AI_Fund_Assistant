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
  status?: 'active' | 'disabled' | null;
}

export interface FundOut {
  id: number;
  code: string;
  name: string;
  fund_type: string;
  tags: string | null;
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
export interface ScheduleCreate {
  name: string;
  cron_expr?: string | null;
  time_point?: string | null;
  task_type: 'analysis_push';
  channel_id?: number | null;
  enabled: boolean;
}

export interface ScheduleUpdate {
  name?: string | null;
  cron_expr?: string | null;
  time_point?: string | null;
  task_type?: 'analysis_push' | null;
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
}

export interface AIConfigOut {
  ai_enabled: boolean;
  ai_model: string;
  ai_base_url: string;
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
