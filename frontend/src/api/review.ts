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

/**
 * 收益口径（Q11）—— 后端 caliber_service 的三行口径头随报告一起返回，
 * 前端不再自己写死口径文案，避免前后端口径分叉。
 */
export interface CaliberInfo {
  nav_adjusted?: boolean;
  bench_div_yield_pct?: number;
  lines?: string[];
}

export interface SignalStats {
  buy_total?: number;
  buy_hits?: number;
  sell_total?: number;
  sell_hits?: number;
  hit_rate?: number | null;
  // 超额口径（跑赢基准的同向率）—— 绝对口径在上涨市里近乎恒真，看这一行才反映选基能力
  excess?: {
    buy_total?: number;
    buy_hits?: number;
    sell_total?: number;
    sell_hits?: number;
    hit_rate?: number | null;
    benchmark_growth_pct?: number | null;
  };
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
  signal_stats: SignalStats;
  summary_md: string;
  caliber?: CaliberInfo;
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

/** 口径开关（回滚键）：GET 读当前生效值与默认值，PUT 改 */
export interface CaliberState {
  nav_adjusted: boolean;
  bench_div_yield_pct: number;
  keys: { nav_adjusted: string; benchmark_dividend_yield_pct: string };
  defaults: { nav_adjusted: boolean; bench_div_yield_pct: number };
  bench_div_yield_max: number;
}

export const caliberApi = {
  get: () =>
    apiClient.get<ApiResponse<CaliberState>>('/api/analysis/caliber').then((r) => r.data),
  update: (body: { review_nav_adjusted?: boolean; benchmark_dividend_yield_pct?: number }) =>
    apiClient.put<ApiResponse<CaliberState>>('/api/analysis/caliber', body).then((r) => r.data),
};
