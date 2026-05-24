/**
 * 基金 API
 */

import apiClient from './client';
import type { ApiResponse, FundCreate, FundUpdate, FundOut, FundHoldingOut, FundManagerOut, FundChangeSummary, FundDetailResponse, FundDetailStatus } from '../types';

const BASE = '/api/funds';

export const fundApi = {
  list: (status?: string) =>
    apiClient.get<ApiResponse<FundOut[]>>(BASE, { params: { status } }).then((r) => r.data),

  create: (data: FundCreate) =>
    apiClient.post<ApiResponse<FundOut>>(BASE, data).then((r) => r.data),

  update: (id: number, data: FundUpdate) =>
    apiClient.put<ApiResponse<FundOut>>(`${BASE}/${id}`, data).then((r) => r.data),

  delete: (id: number) =>
    apiClient.delete<ApiResponse<null>>(`${BASE}/${id}`).then((r) => r.data),

  batchUpdate: (ids: number[], action: 'active' | 'disabled') =>
    apiClient.patch<ApiResponse<null>>(`${BASE}/batch`, { ids, action }).then((r) => r.data),

  batchImport: (items: { code: string; name: string; tags?: string }[]) =>
    apiClient.post<ApiResponse<{ total: number; created: number; skipped: string[]; errors: string[] }>>(`${BASE}/import`, { items }).then((r) => r.data),

  detail: () =>
    apiClient.get<ApiResponse<FundDetailResponse>>(`${BASE}/detail`).then((r) => r.data),

  detailStatus: () =>
    apiClient.get<ApiResponse<FundDetailStatus>>(`${BASE}/detail/status`).then((r) => r.data),

  getHoldings: (id: number) =>
    apiClient.get<ApiResponse<FundHoldingOut[]>>(`${BASE}/${id}/holdings`).then((r) => r.data),

  getManager: (id: number) =>
    apiClient.get<ApiResponse<FundManagerOut[]>>(`${BASE}/${id}/manager`).then((r) => r.data),

  refreshDetails: () =>
    apiClient.post<ApiResponse<{ total: number; results: any[] }>>(`${BASE}/refresh-details`, {}, { timeout: 300000 }).then((r) => r.data),

  refreshThemes: (id: number) =>
    apiClient.post<ApiResponse<FundOut>>(`${BASE}/${id}/refresh-themes`).then((r) => r.data),

  getChangeSummary: () =>
    apiClient.get<ApiResponse<FundChangeSummary[]>>(`${BASE}/change-summary`).then((r) => r.data),
};
