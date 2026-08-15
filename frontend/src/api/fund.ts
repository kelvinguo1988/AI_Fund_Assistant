/**
 * 基金 API
 */

import apiClient from './client';
import type { ApiResponse, FundCreate, FundUpdate, FundOut, FundHoldingOut, FundManagerOut, FundChangeSummary, FundDetailResponse, FundDetailStatus, ExtendedDetailResponse } from '../types';

const BASE = '/api/funds';

export const fundApi = {
  list: (status?: string, order?: string) => {
    const params: Record<string, string> = {};
    if (status) params.status = status;
    if (order) params.order = order;
    return apiClient
      .get<ApiResponse<FundOut[]>>(BASE, { params })
      .then((r) => r.data);
  },

  lookupName: (code: string) =>
    apiClient
      .get<ApiResponse<{ code: string; name: string | null; fund_type: string | null }>>(
        `${BASE}/lookup-name`,
        { params: { code } },
      )
      .then((r) => r.data),

  exportFunds: () =>
    apiClient.get(`${BASE}/export`, { responseType: 'blob' }).then((r) => {
      const url = window.URL.createObjectURL(new Blob([r.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `funds_export_${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      window.URL.revokeObjectURL(url);
    }),

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
    apiClient.post<ApiResponse<{ accepted: boolean; already_running: boolean; status: string; total?: number; done?: number }>>(`${BASE}/refresh-details`).then((r) => r.data),

  refreshDetailsStatus: () =>
    apiClient.get<ApiResponse<{
      status: string;
      total: number;
      done: number;
      current: string;
      message: string;
      error: string | null;
      updated_at: string | null;
      progress: number;
    }>>(`${BASE}/refresh-details/status`).then((r) => r.data),

  refreshThemes: (id: number) =>
    apiClient.post<ApiResponse<FundOut>>(`${BASE}/${id}/refresh-themes`).then((r) => r.data),

  getChangeSummary: () =>
    apiClient.get<ApiResponse<FundChangeSummary[]>>(`${BASE}/change-summary`).then((r) => r.data),

  getExtendedDetail: () =>
    apiClient.get<ApiResponse<ExtendedDetailResponse>>(`${BASE}/extended-detail`).then((r) => r.data),
};
