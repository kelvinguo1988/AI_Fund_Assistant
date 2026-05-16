/**
 * AI 对话 Hook
 */

import { useState, useCallback } from 'react';
import { aiApi } from '../api/ai';
import type { ChatResponse } from '../types';

interface UseAIChatReturn {
  conversationId: string | null;
  loading: boolean;
  error: string | null;
  sendMessage: (content: string, contextType?: 'single_fund' | 'pool' | 'market', fundId?: number) => Promise<ChatResponse | null>;
  resetConversation: () => void;
}

export function useAIChat(): UseAIChatReturn {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sendMessage = useCallback(async (
    content: string,
    contextType?: 'single_fund' | 'pool' | 'market',
    fundId?: number,
  ): Promise<ChatResponse | null> => {
    setLoading(true);
    setError(null);
    try {
      const res = await aiApi.chat({
        content,
        conversation_id: conversationId,
        context_type: contextType,
        fund_id: fundId,
      });
      if (res.data) {
        setConversationId(res.data.conversation_id);
        return res.data;
      }
      return null;
    } catch (err: any) {
      const msg = err.message || 'AI 服务异常';
      setError(msg);
      return null;
    } finally {
      setLoading(false);
    }
  }, [conversationId]);

  const resetConversation = useCallback(() => {
    setConversationId(null);
    setError(null);
  }, []);

  return {
    conversationId,
    loading,
    error,
    sendMessage,
    resetConversation,
  };
}
