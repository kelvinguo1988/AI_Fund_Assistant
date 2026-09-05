/**
 * 信号回测 API
 */

import apiClient from './client';
import type { ApiResponse, BacktestSummary } from '../types';

const BASE = '/api/backtest';

export const backtestApi = {
  /** 运行信号回测 */
  run: (fundId: number, period = 365, effectivenessWindow = 5) =>
    apiClient
      .get<ApiResponse<BacktestSummary>>(`${BASE}/${fundId}`, {
        params: { period, effectiveness_window: effectivenessWindow },
        timeout: 180000,
      })
      .then((r) => r.data),
};

/* ── 自动全量回测 ── */

export interface BacktestBatchItem {
  fund_id: number;
  fund_code: string;
  fund_name: string;
  period: number;
  effectiveness_window: number;
  total_nav_return?: number | null;
  total_strategy_return?: number | null;
  excess_return?: number | null;
  max_drawdown?: number | null;
  signal_count?: number | null;
  avg_effectiveness?: number | null;
  buy_effectiveness?: number | null;
  sell_effectiveness?: number | null;
  effectiveness_rate?: number | null;
  finished_at?: string | null;
  error?: string | null;
  ok: boolean;
}

export interface AutoBacktestConfig {
  enabled: boolean;
  min_interval: number;
  max_interval: number;
}

export const backtestBatchApi = {
  listResults: () =>
    apiClient.get<ApiResponse<BacktestBatchItem[]>>(`${BASE}/batch/results`).then((r) => r.data),

  clearResults: () =>
    apiClient.delete<ApiResponse<number>>(`${BASE}/batch/results`).then((r) => r.data),

  getConfig: () =>
    apiClient.get<ApiResponse<AutoBacktestConfig>>(`${BASE}/batch/config`).then((r) => r.data),

  updateConfig: (data: Partial<AutoBacktestConfig>) =>
    apiClient.put<ApiResponse<AutoBacktestConfig>>(`${BASE}/batch/config`, data).then((r) => r.data),

  /** 手动触发一轮全量回测（后台执行，逐只落库） */
  trigger: () =>
    apiClient.post<ApiResponse<{ accepted: boolean }>>(`${BASE}/batch/run`).then((r) => r.data),
};
