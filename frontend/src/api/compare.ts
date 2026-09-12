/**
 * 基金 PK API — 多基金业绩/风险/基准归因对比
 */

import apiClient from './client';
import type { ApiResponse } from '../types';

export interface FundCompareMetrics {
  window_label: string;
  days: number;
  annual_return_pct?: number | null;
  max_drawdown_pct?: number | null;
  sharpe?: number | null;
  beta?: number | null;
  alpha_annual_pct?: number | null;
  info_ratio?: number | null;
}

export interface FundCompareItem {
  fund_code: string;
  fund_name: string;
  scale_growth?: number | null;
  institution_pct?: number | null;
  windows: FundCompareMetrics[];
  error?: string | null;
}

export interface CompareReport {
  baseline: string;
  items: FundCompareItem[];
  summary_md: string;
}

export const compareApi = {
  run: (fundIds: number[], years = 2) =>
    apiClient
      .get<ApiResponse<CompareReport>>('/api/analysis/compare', {
        params: { fund_ids: fundIds.join(','), years },
        timeout: 180000,
      })
      .then((r) => r.data),
};
