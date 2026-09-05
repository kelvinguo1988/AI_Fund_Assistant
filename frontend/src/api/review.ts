/**
 * 投资复盘 API
 */

import apiClient from './client';
import type { ApiResponse } from '../types';

export interface FundReviewItem {
  fund_code: string;
  fund_name: string;
  nav_start?: number | null;
  nav_end?: number | null;
  growth_pct?: number | null;
  score_start?: number | null;
  score_end?: number | null;
  signal_start?: string | null;
  signal_end?: string | null;
  contribution_pct?: number | null;
  error?: string | null;
}

export interface ReviewReport {
  start_date: string;
  end_date: string;
  fund_count: number;
  portfolio_growth_pct?: number | null;
  benchmark_growth_pct?: number | null;
  excess_pct?: number | null;
  best?: FundReviewItem | null;
  worst?: FundReviewItem | null;
  items: FundReviewItem[];
  signal_stats: {
    buy_total?: number;
    buy_hits?: number;
    sell_total?: number;
    sell_hits?: number;
    hit_rate?: number | null;
  };
  summary_md: string;
}

const BASE = '/api/analysis/review';

export const reviewApi = {
  run: (startDate: string, endDate: string, fundIds?: number[]) =>
    apiClient
      .get<ApiResponse<ReviewReport>>(BASE, {
        params: {
          start_date: startDate,
          end_date: endDate,
          ...(fundIds?.length ? { fund_ids: fundIds.join(',') } : {}),
        },
        timeout: 180000,
      })
      .then((r) => r.data),
};
