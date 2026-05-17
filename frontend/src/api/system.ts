/**
 * 系统配置 API
 */

import apiClient from './client';
import type { ApiResponse, AIConfigUpdate, AIConfigOut, ScoringConfigOut, ScoringConfigUpdate } from '../types';

const BASE = '/api/system';

export const systemApi = {
  getConfig: () =>
    apiClient.get<ApiResponse<AIConfigOut>>(`${BASE}`).then((r) => r.data),

  updateConfig: (data: AIConfigUpdate) =>
    apiClient.put<ApiResponse<AIConfigOut>>(`${BASE}`, data).then((r) => r.data),

  getScoringConfig: () =>
    apiClient.get<ApiResponse<ScoringConfigOut>>(`${BASE}/scoring-config`).then((r) => r.data),

  updateScoringConfig: (data: ScoringConfigUpdate) =>
    apiClient.put<ApiResponse<ScoringConfigOut>>(`${BASE}/scoring-config`, data).then((r) => r.data),
};
