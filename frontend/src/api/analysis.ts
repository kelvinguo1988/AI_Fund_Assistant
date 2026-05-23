/**
 * 分析结果 API
 */

import apiClient from './client';
import type { ApiResponse, AnalysisResultOut, MarketSummaryOut } from '../types';

const BASE = '/api/analysis';

export const analysisApi = {
  query: (params?: { date?: string; fund_id?: number }) =>
    apiClient.get<ApiResponse<AnalysisResultOut[]>>(BASE, { params }).then((r) => r.data),

  trigger: (fundIds?: number[]) =>
    apiClient.post<ApiResponse<AnalysisResultOut[]>>(`${BASE}/trigger`, {
      fund_ids: fundIds,
    }).then((r) => r.data),

  latest: () =>
    apiClient.get<ApiResponse<AnalysisResultOut[]>>(`${BASE}/latest`).then((r) => r.data),

  summary: () =>
    apiClient.get<ApiResponse<MarketSummaryOut>>(`${BASE}/summary`).then((r) => r.data),
};
