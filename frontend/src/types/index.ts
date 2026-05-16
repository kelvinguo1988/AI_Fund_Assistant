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
  weight: number;
  direction: 'positive' | 'negative';
  params?: Record<string, unknown> | null;
  sort_order: number;
}

export interface FactorUpdate {
  name?: string | null;
  weight?: number | null;
  direction?: 'positive' | 'negative' | null;
  params?: Record<string, unknown> | null;
  status?: 'active' | 'disabled' | null;
  sort_order?: number | null;
}

export interface FactorOut {
  id: number;
  name: string;
  code: string;
  data_field: string | null;
  weight: number;
  direction: string;
  params: Record<string, unknown> | null;
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
