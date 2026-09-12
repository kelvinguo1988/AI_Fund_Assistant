/**
 * 错误日志 API — 系统错误告警（铃铛）+ 设置页日志管理
 */

import apiClient from './client';
import type { ApiResponse } from '../types';

export interface ErrorLogItem {
  id: number;
  ts: string;
  module: string;
  category: string;   // rate_limit/timeout/network/data/other/frontend
  severity: string;   // warning/error/critical
  message: string;
  detail?: string | null;
}

const BASE = '/api/system/error-logs';

export const errorLogApi = {
  list: (limit = 100, category?: string, since?: string) =>
    apiClient
      .get<ApiResponse<ErrorLogItem[]>>(BASE, {
        params: { limit, ...(category ? { category } : {}), ...(since ? { since } : {}) },
      })
      .then((r) => r.data),

  count: (since?: string) =>
    apiClient
      .get<ApiResponse<{ count: number }>>(`${BASE}/count`, { params: since ? { since } : {} })
      .then((r) => r.data),

  /** 下载日志文本文件 */
  download: (category?: string) =>
    apiClient
      .get(`${BASE}/download`, {
        params: category ? { category } : {},
        responseType: 'blob',
        timeout: 60000,
      })
      .then((r) => {
        const url = window.URL.createObjectURL(new Blob([r.data], { type: 'text/plain;charset=utf-8' }));
        const a = document.createElement('a');
        a.href = url;
        a.download = `error_logs_${new Date().toISOString().slice(0, 10)}.txt`;
        a.click();
        window.URL.revokeObjectURL(url);
      }),

  clear: () =>
    apiClient.delete<ApiResponse<number>>(BASE).then((r) => r.data),

  /** 前端错误上报 */
  report: (payload: { module: string; message: string; category?: string; detail?: string }) =>
    apiClient.post<ApiResponse<null>>(BASE, { severity: 'error', ...payload }).then((r) => r.data),
};

/** 分类显示配置 */
export const CATEGORY_META: Record<string, { label: string; color: 'error' | 'warning' | 'info' | 'default' }> = {
  rate_limit: { label: '限流/封禁', color: 'error' },
  timeout: { label: '超时', color: 'warning' },
  network: { label: '网络', color: 'warning' },
  data: { label: '数据缺失', color: 'info' },
  frontend: { label: '前端', color: 'default' },
  other: { label: '其他', color: 'default' },
};
