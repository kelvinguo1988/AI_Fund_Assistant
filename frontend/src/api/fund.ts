/**
 * 基金 API
 */

import apiClient from './client';
import type { ApiResponse, FundCreate, FundUpdate, FundOut } from '../types';

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
};
