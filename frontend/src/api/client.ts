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
apiClient.interceptors.response.use(
  (response) => {
    const data = response.data as ApiResponse<unknown>;
    // 业务错误码非 0 时，统一抛出
    if (data && data.code !== undefined && data.code !== 0) {
      const errMsg = data.message || '请求失败';
      console.error(`[API Error] code=${data.code}, message=${errMsg}`);
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
    console.error(`[HTTP Error] status=${status}, message=${message}`);
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
    if (status && status >= 500) {
      import('./errorLog').then(({ errorLogApi }) => {
        errorLogApi
          .report({
            module: 'frontend.http',
            message: `${error.config?.method?.toUpperCase() ?? 'GET'} ${error.config?.url ?? ''} → ${status}`,
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
