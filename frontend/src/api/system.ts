/**
 * 系统配置 API
 */

import apiClient from './client';
import type { ApiResponse, AIConfigUpdate, AIConfigOut } from '../types';

const BASE = '/api/system/config';

export const systemApi = {
  getConfig: () =>
    apiClient.get<ApiResponse<AIConfigOut>>(BASE).then((r) => r.data),

  updateConfig: (data: AIConfigUpdate) =>
    apiClient.put<ApiResponse<AIConfigOut>>(BASE, data).then((r) => r.data),
};
