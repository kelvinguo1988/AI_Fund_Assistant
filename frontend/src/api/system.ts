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
  DataSourceHealth,
  QualityConfigOut,
  QualityConfigUpdate,
  MarketTemperature,
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

  /** 中证800 市场温度计：后端为 null 表示乐咕当日取数失败（前端按「暂无数据」处理） */
  getMarketTemperature: () =>
    apiClient.get<ApiResponse<MarketTemperature | null>>(`${BASE}/market-temperature`, { timeout: 60000 }).then((r) => r.data),

  testConnectivity: () =>
    apiClient.get<ApiResponse<ConnectivityResult>>(`${BASE}/connectivity`).then((r) => r.data),

  /** 数据源运行时健康（冷却/降级/缓存新鲜度）：后端零网络请求，可随时刷新 */
  getDataSourceHealth: () =>
    apiClient.get<ApiResponse<DataSourceHealth>>(`${BASE}/data-source-health`).then((r) => r.data),

  getQualityConfig: () =>
    apiClient.get<ApiResponse<QualityConfigOut>>(`${BASE}/quality-config`).then((r) => r.data),

  updateQualityConfig: (data: QualityConfigUpdate) =>
    apiClient.put<ApiResponse<QualityConfigOut>>(`${BASE}/quality-config`, data).then((r) => r.data),
};
