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

export interface RebalanceRow {
  code: string;
  name: string;
  score: number;
  direction: string;
  theme: string;
  weight_pct: number;
  [k: string]: any;
}

export interface RebalanceData {
  as_of: string;
  window_days: number;
  holdings_mode: string;
  holdings: RebalanceRow[];
  sells: RebalanceRow[];
  buys: RebalanceRow[];
  buy_blocked: RebalanceRow[];
  swaps: { sell_code: string; sell_name: string; buy_code: string; buy_name: string; theme: string; score_gap: number }[];
  watch: RebalanceRow[];
  constraints: {
    theme_concentration?: { theme: string; weight_pct: number }[];
    qdii_weight_pct?: number | null;
    twin_pairs?: { a_code: string; a_name: string; b_code: string; b_name: string; common: number }[];
  };
  caveats: string[];
}

export const positionApi = {
  list: () =>
    apiClient.get<ApiResponse<{ count: number; items: PositionItem[] }>>('/api/positions'),
  create: (fund_code: string, shares: number, cost_nav?: number | null) =>
    apiClient.post<ApiResponse<{ fund_id: number; fund_code: string; created: boolean }>>(
      '/api/positions', { fund_code, shares, cost_nav }),
  update: (id: number, shares?: number | null, cost_nav?: number | null) =>
    apiClient.put<ApiResponse<{ id: number }>>(`/api/positions/${id}`, { shares, cost_nav }),
  remove: (id: number) =>
    apiClient.delete<ApiResponse<{ deleted: number }>>(`/api/positions/${id}`),
  importCsv: (text: string, mode: 'merge' | 'replace') =>
    apiClient.post<ApiResponse<ImportResult>>('/api/positions/import-csv', { text, mode }),
};

export const rebalanceApi = {
  run: (window_days: number) =>
    apiClient.get<ApiResponse<boolean> & { ok: boolean; data: RebalanceData; summary_md: string }>(
      '/api/ai/agent/rebalance', { params: { window_days } }),
};
