/**
 * Axios 实例 + 拦截器
 */

import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';
import type { ApiResponse } from '../types';

const apiClient = axios.create({
  baseURL: '',
  timeout: 120000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// ── 请求拦截器 ──────────────────────────────────────────────────────
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    // 后续可在此处添加 token 等认证信息
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// ── 响应拦截器 ──────────────────────────────────────────────────────
// 生产构建不打印原始错误（错误已进告警铃铛），排障时把 VITE_DEBUG=1 打开
const DEBUG = import.meta.env.DEV || import.meta.env.VITE_DEBUG === '1';
const logErr = (msg: string) => {
  if (DEBUG) console.error(msg);
};

apiClient.interceptors.response.use(
  (response) => {
    const data = response.data as ApiResponse<unknown>;
    // 业务错误码非 0 时，统一抛出
    if (data && data.code !== undefined && data.code !== 0) {
      const errMsg = data.message || '请求失败';
      logErr(`[API Error] code=${data.code}, message=${errMsg}`);
      return Promise.reject(new Error(errMsg));
    }
    return response;
  },
  (error: AxiosError) => {
    const status = error.response?.status;
    let message = '网络异常，请稍后重试';
    if (status === 400) {
      message = '请求参数错误';
    } else if (status === 401) {
      message = '未授权，请重新登录';
    } else if (status === 403) {
      message = '拒绝访问';
    } else if (status === 404) {
      message = '请求资源不存在';
    } else if (status && status >= 500) {
      message = '服务器内部错误';
    }
    // 主动取消（AbortController）不是故障：切一次 Tab / 改一次筛选会重发整批
    // 请求（基金详情 34 只 = 34 个），旧请求全部命中本拦截器 → 一次操作刷出
    // 30+ 条 "status=undefined 网络异常"，真故障被噪声埋掉。仍照常 reject，
    // 只跳过日志与上报。
    const isCanceled = axios.isCancel(error);
    if (!isCanceled) {
      logErr(`[HTTP Error] status=${status}, message=${message}`);
    }
    // 2026-09-12 修复：保留 response 使 10+ 处 err.response.data.detail
    // 读取生效（后端 400 详情如"复盘区间最长 2 年"直达用户）；
    // displayMessage 为统一展示口径
    const detail =
      (error.response?.data as { detail?: string } | undefined)?.detail || message;
    const err = new Error(detail) as Error & {
      response?: AxiosError['response'];
      displayMessage?: string;
    };
    err.response = error.response;
    err.displayMessage = detail;
    // 5xx 自动上报错误铃铛（fire-and-forget，杜绝二次异常）
    // 2026-09-29 审查 P0：上报接口自身 5xx（后端挂了/网络分区）时，
    // 这条上报会被同一个拦截器再上报一次 → 自我放大成请求风暴。
    // 上报端点自身的失败一律不再上报。
    const reqUrl = error.config?.url ?? '';
    const isSelfReport = reqUrl.includes('/system/error-logs');
    if (status && status >= 500 && !isSelfReport) {
      import('./errorLog').then(({ errorLogApi }) => {
        errorLogApi
          .report({
            module: 'frontend.http',
            message: `${error.config?.method?.toUpperCase() ?? 'GET'} ${reqUrl} → ${status}`,
            category: 'other',
            detail,
          })
          .catch(() => {});
      });
    }
    return Promise.reject(err);
  }
);

export default apiClient;
