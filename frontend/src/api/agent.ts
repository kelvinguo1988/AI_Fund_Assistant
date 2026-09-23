/**
 * AI Agent API — SSE 流式运行（POST + fetch ReadableStream）+ 会话列表 + 工具清单
 *
 * 事件协议（backend/ai/agent_runner.py）：
 * start / round / delta / delta_reset / tool_call / tool_result / context / final / error / done
 */

import apiClient from './client';

const BASE = '/api/ai/agent';

export interface AgentToolTrace {
  round: number;
  tool: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  ms: number;
  chars: number;
}

export interface AgentEvent {
  type: string;
  conversation_id?: string;
  model?: string;
  tools?: number;
  round?: number;
  text?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  note?: string;
  message?: string;
  content?: string;
  rounds?: number;
  tool_calls?: number;
  tokens?: number;
  // tool_result 展开字段
  tool?: string;
  ok?: boolean;
  ms?: number;
  chars?: number;
}

export interface AgentRunCallbacks {
  onEvent: (ev: AgentEvent) => void;
  onError?: (error: string) => void;
  onFinish?: () => void;
}

export interface AgentRunRequest {
  prompt?: string;
  conversation_id?: string | null;
  task?: string | null;
  max_rounds?: number | null;
  params?: Record<string, unknown>;
}

export const agentApi = {
  /** 运行 Agent 任务，逐事件回调；返回 abort 句柄 */
  run: (body: AgentRunRequest, cb: AgentRunCallbacks): { abort: () => void } => {
    const controller = new AbortController();
    (async () => {
      try {
        const response = await fetch(`${BASE}/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        if (!response.ok) {
          let detail = `HTTP ${response.status}`;
          try {
            const j = await response.json();
            detail = j?.detail || j?.message || detail;
          } catch { /* ignore */ }
          cb.onError?.(detail);
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
            if (!line.startsWith('data: ')) continue;
            try {
              cb.onEvent(JSON.parse(line.slice(6)));
            } catch { /* skip malformed */ }
          }
        }
      } catch (e: any) {
        if (e?.name !== 'AbortError') cb.onError?.(e?.message || 'Agent 运行失败');
      } finally {
        cb.onFinish?.();
      }
    })();
    return controller;
  },

  listConversations: (limit = 30) =>
    apiClient.get(`${BASE}/conversations`, { params: { limit } }).then((r) => r.data),

  listTools: () => apiClient.get(`${BASE}/tools`).then((r) => r.data),
};
