/**
 * 因子 API
 */

import apiClient from './client';
import type { ApiResponse, FactorCreate, FactorUpdate, FactorOut, FactorImportResult, FactorExportPayload } from '../types';

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

  /** 导出全部因子为 JSON（返回 Blob） */
  exportFactors: () =>
    apiClient.get(`${BASE}/export`, { responseType: 'blob' }).then((r) => r.data as Blob),

  /** 导入因子配置 */
  importFactors: (payload: FactorExportPayload, overwrite = false) =>
    apiClient
      .post<ApiResponse<FactorImportResult>>(`${BASE}/import`, payload, {
        params: { overwrite },
      })
      .then((r) => r.data),
};
