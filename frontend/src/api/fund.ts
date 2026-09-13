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

  /** 实时净值预估（场外 fundgz/持仓自算，场内 ETF 行情） */
  realtime: (force = false) =>
    apiClient
      .get<ApiResponse<Record<string, FundRealtimeOut>>>(`${BASE}/realtime`, {
        params: force ? { force: true } : undefined,
      })
      .then((r) => r.data),

  /** 潜力 ETF 扫描（全市场量价/资金三榜单） */
  etfScan: () =>
    apiClient
      .get<ApiResponse<ETFScanResult>>('/api/funds/etf-scan', { timeout: 120000 })
      .then((r) => r.data),
};

export interface FundRealtimeOut {
  code: string;
  name: string;
  source: 'fundgz' | 'holdings_est' | 'etf_spot' | string;
  nav_date: string | null;
  nav: number | null;
  estimated_nav: number | null;
  growth_pct: number | null;
  quote_time: string | null;
  coverage: number | null;
  est_model: 'official' | 'normalized' | 'index_blend' | 'market_price' | null;
  hints?: { type: string; level: string; message: string }[];
}


/* ── ETF 扫描 ── */
export interface ETFScanItem {
  code: string;
  name: string;
  price?: number | null;
  pct?: number | null;
  turnover_rate?: number | null;
  volume_ratio?: number | null;
  main_inflow_pct?: number | null;
  amount?: number | null;
  in_pool: boolean;
}

export interface ETFScanResult {
  scanned: number;
  movers: ETFScanItem[];
  inflow: ETFScanItem[];
  unusual: ETFScanItem[];
  pool_codes: string[];
  enabled?: boolean;
  error?: string;
}

/* ── 概念映射（THS 渐进获取）── */
export interface ConceptMapProgress {
  concepts_mapped: number;
  concepts_total: number;
  stocks_mapped: number;
  latest_update?: string | null;
}

export const conceptMapApi = {
  progress: () =>
    apiClient.get<ApiResponse<ConceptMapProgress>>('/api/funds/concept-map/progress').then((r) => r.data),

  importJson: (payload: { concepts: Record<string, string[]> }) =>
    apiClient.post<ApiResponse<{ imported: number }>>('/api/funds/concept-map/import', payload, { timeout: 60000 }).then((r) => r.data),

  fetchBatch: () =>
    apiClient.post<ApiResponse<{ fetched: number; stocks: number; failed: number; remaining_hint: string }>>('/api/funds/concept-map/fetch', {}, { timeout: 300000 }).then((r) => r.data),

  clear: () =>
    apiClient.delete<ApiResponse<number>>('/api/funds/concept-map').then((r) => r.data),

  /** 导出映射 JSON（与导入格式兼容，备份/迁移用） */
  export: () =>
    apiClient.get('/api/funds/concept-map/export', { responseType: 'blob', timeout: 60000 }).then((r) => {
      const url = window.URL.createObjectURL(new Blob([r.data], { type: 'application/json' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `concept_map_${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      window.URL.revokeObjectURL(url);
    }),
};

export interface HoldingOverlap {
  funds_count: number;
  overlaps: { stock_code: string; stock_name: string; funds_count: number; total_ratio?: number | null }[];
}

export const overlapApi = {
  get: (fundIds?: number[]) =>
    apiClient
      .get<ApiResponse<HoldingOverlap>>('/api/funds/holding-overlap', {
        params: fundIds?.length ? { fund_ids: fundIds.join(',') } : {},
      })
      .then((r) => r.data),
};
