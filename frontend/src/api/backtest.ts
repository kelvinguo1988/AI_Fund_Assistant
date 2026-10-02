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

  /** 回测调仓费率配置 */
  getFeeConfig: () =>
    apiClient
      .get<ApiResponse<{ fee_pct: number; default: number; min: number; max: number }>>(
        `${BASE}/config/fee`,
      )
      .then((r) => r.data),

  updateFeeConfig: (feePct: number) =>
    apiClient
      .put<ApiResponse<{ fee_pct: number }>>(`${BASE}/config/fee`, { fee_pct: feePct })
      .then((r) => r.data),

  /** 回测度量口径（仓位延续 / 样本下限）——同时是 Q6 的回滚开关 */
  getMeasurementConfig: () =>
    apiClient
      .get<ApiResponse<BacktestMeasurementConfig & {
        defaults: { carry_position: boolean; min_signals: number; min_coverage_pct: number };
      }>>(`${BASE}/config/measurement`)
      .then((r) => r.data),

  updateMeasurementConfig: (patch: Partial<BacktestMeasurementConfig>) =>
    apiClient
      .put<ApiResponse<BacktestMeasurementConfig>>(`${BASE}/config/measurement`, patch)
      .then((r) => r.data),
};

export interface BacktestMeasurementConfig {
  carry_position: boolean;
  min_signals: number;
  min_coverage_pct: number;
}

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
  // ── Q6 新口径（旧行 NULL → 前端不显示）──
  baseline_buy_hold?: number | null;
  baseline_static_half?: number | null;
  excess_vs_static_half?: number | null;
  signal_count_non_hold?: number | null;
  signal_coverage_ratio?: number | null;
  low_sample?: boolean | null;
  caveat?: string | null;
  coverage_start_date?: string | null;
  coverage_days?: number | null;
  pool_size_at?: number | null;
  carry_position?: boolean | null;
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

  /** 服务端是否在跑全量回测（前端"运行中"状态的唯一真相源） */
  status: () =>
    apiClient.get<ApiResponse<{ running: boolean }>>(`${BASE}/batch/status`).then((r) => r.data),

  /** 手动触发一轮全量回测（后台执行，逐只落库） */
  trigger: () =>
    apiClient.post<ApiResponse<{ accepted: boolean }>>(`${BASE}/batch/run`).then((r) => r.data),
};
