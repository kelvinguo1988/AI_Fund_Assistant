/**
 * 因子 API
 */

import apiClient from './client';
import type { ApiResponse, FactorCreate, FactorUpdate, FactorOut } from '../types';

const BASE = '/api/factors';

export const factorApi = {
  list: () =>
    apiClient.get<ApiResponse<FactorOut[]>>(BASE).then((r) => r.data),

  create: (data: FactorCreate) =>
    apiClient.post<ApiResponse<FactorOut>>(BASE, data).then((r) => r.data),

  update: (id: number, data: FactorUpdate) =>
    apiClient.put<ApiResponse<FactorOut>>(`${BASE}/${id}`, data).then((r) => r.data),

  delete: (id: number) =>
    apiClient.delete<ApiResponse<null>>(`${BASE}/${id}`).then((r) => r.data),
};
