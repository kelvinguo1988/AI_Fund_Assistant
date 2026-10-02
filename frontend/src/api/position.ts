/**
 * 我的持仓 + 调仓分析 API
 */

import apiClient from './client';
import type { ApiResponse } from '../types';

export interface PositionItem {
  id: number;
  fund_id: number;
  fund_code: string;
  fund_name: string;
  shares: number;
  cost_nav?: number | null;
  /** 首次买入日 YYYY-MM-DD；null = 未填，持有期/赎回费约束对该只不生效 */
  first_buy_date?: string | null;
  holding_days?: number | null;
  source: string;
  fund_type: string;
  status: string;
  latest_score?: number | null;
  latest_signal?: string | null;
  updated_at?: string | null;
}

export interface ImportResult {
  imported: number;
  updated: number;
  skipped: number;
  errors: string[];
}

/**
 * 调仓引擎的行：后端按清单类型附加不同字段（见 backend/ai/rebalance.py），
 * 原来靠 `[k: string]: any` 兜住全部，前端读什么都不报错 —— 字段改名即静默失效。
 */
export interface RebalanceRow {
  code: string;
  name: string;
  /** 持仓总览行可能无当期评分 → null */
  score: number | null;
  direction: string | null;
  theme: string;
  weight_pct: number;
  // 卖出清单
  sell_threshold?: number;
  reasons?: string[];
  // 观望 / 买入受阻清单
  reason?: string;
  confirm_days?: number;
  // 买入清单
  buy_threshold?: number;
  otc_status?: string;
  overlap_note?: string;
  // 持仓总览行
  qdii?: boolean;
  /** 权重怎么来的（Q10 市值口径）：market=实时净值市值 / cost=成本市值 / shares=份额估算 */
  weight_basis?: 'market' | 'cost' | 'shares' | null;
  // 持有期与赎回费（Q10-C）：null/undefined = 未填首买日或非真实持仓，未做费用约束
  holding_days?: number | null;
  estimated_fee_pct?: number | null;
}

/** 换仓配对：卖侧的持有期/费率一并带出，分差大但要付 1.5% 短期费时看得见 */
export interface RebalanceSwap {
  sell_code: string;
  sell_name: string;
  buy_code: string;
  buy_name: string;
  theme: string;
  score_gap: number;
  sell_holding_days?: number | null;
  sell_fee_pct?: number | null;
}

/** 生效中的赎回费口径（backend/ai/rebalance.py::_fee_policy） */
export interface RebalanceFeePolicy {
  enabled: boolean;
  penalize_pct: number;
  ladder: [number | null, number][];
  ladder_text: string;
}

export interface RebalanceData {
  as_of: string;
  window_days: number;
  holdings_mode: string;
  holdings: RebalanceRow[];
  sells: RebalanceRow[];
  buys: RebalanceRow[];
  buy_blocked: RebalanceRow[];
  swaps: RebalanceSwap[];
  watch: RebalanceRow[];
  constraints: {
    theme_concentration?: { theme: string; weight_pct: number }[];
    qdii_weight_pct?: number | null;
    twin_pairs?: { a_code: string; a_name: string; b_code: string; b_name: string; common: number }[];
  };
  caveats: string[];
  fee_policy?: RebalanceFeePolicy;
}

/** PUT /api/positions/{id} 用「字段是否出现」决定改不改，所以传对象而不是位置参数 */
export interface PositionPatch {
  shares?: number | null;
  cost_nav?: number | null;
  first_buy_date?: string | null;
}

export const positionApi = {
  list: () =>
    apiClient.get<ApiResponse<{ count: number; items: PositionItem[] }>>('/api/positions'),
  create: (fund_code: string, shares: number, cost_nav?: number | null,
           first_buy_date?: string | null) =>
    apiClient.post<ApiResponse<{ fund_id: number; fund_code: string; created: boolean }>>(
      '/api/positions', { fund_code, shares, cost_nav, first_buy_date }),
  update: (id: number, patch: PositionPatch) =>
    apiClient.put<ApiResponse<{ id: number }>>(`/api/positions/${id}`, patch),
  remove: (id: number) =>
    apiClient.delete<ApiResponse<{ deleted: number }>>(`/api/positions/${id}`),
  importCsv: (text: string, mode: 'merge' | 'replace') =>
    apiClient.post<ApiResponse<ImportResult>>('/api/positions/import-csv', { text, mode }),
};

/** GET /api/ai/agent/rebalance 原样返回 {ok, data, summary_md}（无 ApiResponse 信封） */
export interface RebalanceResponse {
  ok: boolean;
  data: RebalanceData;
  summary_md: string;
}

export const rebalanceApi = {
  run: (window_days: number) =>
    apiClient.get<RebalanceResponse>('/api/ai/agent/rebalance', { params: { window_days } }),
};

// ── 调仓建议自进化闭环（Q7 双口径命中率，端点在 /api/analysis 下）────────

export interface AdviceSide { total: number; hits: number }

/** stats.by_mode：abs=绝对涨跌（含 beta）/ excess=相对沪深300 超额（选基能力） */
export interface AdviceStats {
  evaluated: number;
  window_days: number | null;
  hit_mode: 'abs' | 'excess';
  sell_total: number;
  sell_hits: number;
  buy_total: number;
  buy_hits: number;
  by_mode: {
    abs: { sell: AdviceSide; buy: AdviceSide };
    excess: { sell: AdviceSide; buy: AdviceSide };
  };
  params: Record<string, number>;
}

export interface AdviceHitMode { hit_mode: 'abs' | 'excess'; options: string[]; default: string }

export interface AdviceEvalResult {
  evaluated: number;
  skipped_window_incomplete?: number;
  benchmark_missing?: number;
  pending: number;
  funds_fetched?: number;
  calibration?: { adjusted: boolean; changes: { key: string; from: number; to: number }[] };
}

const ADVICE_BASE = '/api/analysis';

export const adviceApi = {
  stats: () =>
    apiClient.get<ApiResponse<AdviceStats>>(`${ADVICE_BASE}/advice-stats`).then((r) => r.data),
  hitMode: () =>
    apiClient.get<ApiResponse<AdviceHitMode>>(`${ADVICE_BASE}/advice-hit-mode`).then((r) => r.data),
  setHitMode: (mode: string) =>
    apiClient.put<ApiResponse<{ hit_mode: string }>>(`${ADVICE_BASE}/advice-hit-mode`, { hit_mode: mode })
      .then((r) => r.data),
  /** 手动回填到期建议：逐只取净值且基金之间随机防封间隔，慢是设计如此 */
  runEval: () =>
    apiClient.post<ApiResponse<AdviceEvalResult>>(`${ADVICE_BASE}/advice-eval`, {}, { timeout: 600000 })
      .then((r) => r.data),
};
