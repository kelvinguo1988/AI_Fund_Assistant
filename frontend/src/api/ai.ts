/**
 * AI 对话 API
 */

import apiClient from './client';
import type { ApiResponse, ChatMessage, ChatResponse } from '../types';

const BASE = '/api/ai';

export const aiApi = {
  chat: (data: ChatMessage) =>
    apiClient.post<ApiResponse<ChatResponse>>(`${BASE}/chat`, data).then((r) => r.data),

  getConversations: (conversationId: string) =>
    apiClient.get<ApiResponse<ChatMessage[]>>(`${BASE}/conversations`, {
      params: { conversation_id: conversationId },
    }).then((r) => r.data),
};
