/**
 * 系统配置 API
 */

import apiClient from './client';
import type {
  ApiResponse,
  AIConfigUpdate,
  AIConfigOut,
  ScoringConfigOut,
  ScoringConfigUpdate,
  ConnectivityResult,
  QualityConfigOut,
  QualityConfigUpdate,
} from '../types';

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

  getFeatureFlags: () =>
    apiClient.get<ApiResponse<{ etf_hints_enabled: boolean; otc_hints_enabled: boolean }>>(`${BASE}/feature-flags`).then((r) => r.data),

  updateFeatureFlags: (data: { etf_hints_enabled?: boolean; otc_hints_enabled?: boolean }) =>
    apiClient.put<ApiResponse<{ etf_hints_enabled: boolean; otc_hints_enabled: boolean }>>(`${BASE}/feature-flags`, data).then((r) => r.data),

  getIndexValuations: () =>
    apiClient.get<ApiResponse<unknown>>('/api/system/index-valuations').then((r) => r.data),

  testConnectivity: () =>
    apiClient.get<ApiResponse<ConnectivityResult>>(`${BASE}/connectivity`).then((r) => r.data),

  getQualityConfig: () =>
    apiClient.get<ApiResponse<QualityConfigOut>>(`${BASE}/quality-config`).then((r) => r.data),

  updateQualityConfig: (data: QualityConfigUpdate) =>
    apiClient.put<ApiResponse<QualityConfigOut>>(`${BASE}/quality-config`, data).then((r) => r.data),
};
