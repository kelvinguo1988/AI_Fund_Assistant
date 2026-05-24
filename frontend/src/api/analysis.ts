/**
 * 分析结果 API
 */

import apiClient from './client';
import type { ApiResponse, AnalysisResultOut, MarketSummaryOut } from '../types';

const BASE = '/api/analysis';

export const analysisApi = {
  query: (params?: { date?: string; fund_id?: number }) =>
    apiClient.get<ApiResponse<AnalysisResultOut[]>>(BASE, { params }).then((r) => r.data),

  trigger: (fundIds?: number[]) =>
    apiClient.post<ApiResponse<AnalysisResultOut[]>>(`${BASE}/trigger`, {
      fund_ids: fundIds,
    }).then((r) => r.data),

  latest: () =>
    apiClient.get<ApiResponse<AnalysisResultOut[]>>(`${BASE}/latest`).then((r) => r.data),

  summary: () =>
    apiClient.get<ApiResponse<MarketSummaryOut>>(`${BASE}/summary`).then((r) => r.data),

  refreshSummary: () =>
    apiClient.post<ApiResponse<{ updated_at: string }>>(`${BASE}/refresh-summary`, {}, { timeout: 180000 }).then((r) => r.data),

  /** 流式触发分析 — 使用 fetch ReadableStream 消费 SSE，逐块更新回调 */
  triggerStream: (
    fundIds: number[] | undefined,
    callbacks: {
      onProgress?: (current: number, total: number, fundCode: string) => void;
      onChunk?: (results: AnalysisResultOut[], progress: string) => void;
      onComplete?: (total: number, succeeded: number) => void;
      onError?: (error: string) => void;
    },
  ): { abort: () => void } => {
    const controller = new AbortController();

    (async () => {
      try {
        const response = await fetch(`${BASE}/trigger-stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ fund_ids: fundIds?.length ? fundIds : undefined }),
          signal: controller.signal,
        });

        if (!response.ok) {
          callbacks.onError?.(`HTTP ${response.status}`);
          return;
        }

        const reader = response.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6));
                switch (data.type) {
                  case 'progress':
                    callbacks.onProgress?.(data.current, data.total, data.fund_code);
                    break;
                  case 'chunk':
                    callbacks.onChunk?.(data.results, data.progress);
                    break;
                  case 'complete':
                    callbacks.onComplete?.(data.total, data.succeeded);
                    break;
                }
              } catch {
                // skip malformed JSON
              }
            }
          }
        }
      } catch (err: any) {
        if (err.name !== 'AbortError') {
          callbacks.onError?.(err.message || '流式分析请求失败');
        }
      }
    })();

    return { abort: () => controller.abort() };
  },
};
