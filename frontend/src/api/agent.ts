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
  // budget_exhausted 展开字段
  token_spent?: number;
  budget?: number;
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

/** GET /api/ai/agent/conversations 的单条会话摘要 */
export interface AgentConversationItem {
  conversation_id: string;
  last_role: string;
  preview: string;
  last_at: string;
  turns: number;
}

/** GET /api/ai/agent/tools 的能力清单项 */
export interface AgentToolInfo {
  name: string;
  description: string;
  category: string;
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
        let sawDone = false;
        let sawError = false;
        let malformed = 0;
        const handleLine = (line: string) => {
          if (!line.startsWith('data: ')) return;
          let ev: AgentEvent;
          try {
            ev = JSON.parse(line.slice(6));
          } catch {
            // 原来 catch 里直接吞掉：协议破口在页面上表现为"回答莫名停止"，无从排查
            malformed += 1;
            return;
          }
          if (ev.type === 'done') sawDone = true;
          else if (ev.type === 'error') sawError = true;
          cb.onEvent(ev);
        };
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          lines.forEach(handleLine);
        }
        handleLine(buffer); // 收尾：末条事件若缺换行符会留在 buffer 里
        if (malformed > 0) cb.onError?.(`有 ${malformed} 条 AI 事件无法解析，展示内容可能不完整`);
        // done 由路由层在流末尾补发；既无 done 也无 error ⇒ 连接被中途切断
        // （网关超时/浏览器断连），原来会静默停在半截回答上
        if (!sawDone && !sawError && !controller.signal.aborted) {
          cb.onError?.('连接中断，未收到结束标记，回答可能不完整');
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
    apiClient
      .get<{ ok: boolean; items: AgentConversationItem[] }>(`${BASE}/conversations`, { params: { limit } })
      .then((r) => r.data),

  listTools: () =>
    apiClient.get<{ ok: boolean; tools: AgentToolInfo[] }>(`${BASE}/tools`).then((r) => r.data),
};
