/**
 * 报告配置 API
 */

import apiClient from './client';
import type { ApiResponse, ReportConfigOut, ReportConfigUpdate } from '../types';

const BASE = '/api/report-config';

export const reportApi = {
  list: () =>
    apiClient.get<ApiResponse<ReportConfigOut[]>>(BASE).then((r) => r.data),

  batchUpdate: (items: ReportConfigUpdate[]) =>
    apiClient.put<ApiResponse<ReportConfigOut[]>>(BASE, items).then((r) => r.data),
};
