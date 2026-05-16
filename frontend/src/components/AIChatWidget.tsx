/**
 * AI 浮动对话窗口组件
 */

import React, { useState, useRef, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  IconButton,
  TextField,
  Button,
  Fab,
  Chip,
  Snackbar,
  Alert,
} from '@mui/material';
import { SmartToy as AIIcon, Close as CloseIcon, Send as SendIcon } from '@mui/icons-material';
import { aiApi } from '../api/ai';
import { useAppStore } from '../store';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

const QUICK_COMMANDS = [
  { label: '分析基金池', context_type: 'pool' as const },
  { label: '市场行情', context_type: 'market' as const },
];

const AIChatWidget: React.FC = () => {
  const { aiEnabled } = useAppStore();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'error' as 'error' });
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  if (!aiEnabled) return null;

  const handleSend = async () => {
    if (!input.trim() || loading) return;

    const userMessage: Message = { role: 'user', content: input.trim() };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    try {
      const res = await aiApi.chat({
        content: userMessage.content,
        conversation_id: conversationId,
        context_type: 'pool',
      });

      if (res.data) {
        setConversationId(res.data.conversation_id);
        setMessages((prev) => [...prev, { role: 'assistant', content: res.data!.content }]);
      }
    } catch (err: any) {
      setSnackbar({ open: true, message: err.message || 'AI 服务异常', severity: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const handleQuickCommand = async (contextType: 'pool' | 'market') => {
    const prompts: Record<string, string> = {
      pool: '请分析当前基金池的整体情况',
      market: '请分析当前市场行情',
    };

    setInput(prompts[contextType] || '');
  };

  const handleNewChat = () => {
    setMessages([]);
    setConversationId(null);
  };

  return (
    <>
      {/* 浮动按钮 */}
      <Fab
        color="secondary"
        sx={{ position: 'fixed', bottom: 24, right: 24, zIndex: 1000 }}
        onClick={() => setOpen(!open)}
      >
        {open ? <CloseIcon /> : <AIIcon />}
      </Fab>

      {/* 对话窗口 */}
      {open && (
        <Paper
          sx={{
            position: 'fixed',
            bottom: 80,
            right: 24,
            width: 400,
            height: 520,
            zIndex: 1000,
            display: 'flex',
            flexDirection: 'column',
            boxShadow: 8,
            borderRadius: 2,
          }}
        >
          {/* 标题栏 */}
          <Box sx={{ p: 1.5, bgcolor: 'primary.main', color: 'white', borderRadius: '8px 8px 0 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>AI 助手</Typography>
            <Box sx={{ display: 'flex', gap: 0.5 }}>
              <Button size="small" color="inherit" onClick={handleNewChat}>新对话</Button>
              <IconButton size="small" color="inherit" onClick={() => setOpen(false)}>
                <CloseIcon fontSize="small" />
              </IconButton>
            </Box>
          </Box>

          {/* 快捷指令 */}
          <Box sx={{ px: 1.5, py: 0.5, display: 'flex', gap: 0.5, flexWrap: 'wrap', borderBottom: '1px solid #e0e0e0' }}>
            {QUICK_COMMANDS.map((cmd) => (
              <Chip key={cmd.label} label={cmd.label} size="small" variant="outlined"
                onClick={() => handleQuickCommand(cmd.context_type)} />
            ))}
          </Box>

          {/* 消息区 */}
          <Box sx={{ flex: 1, overflowY: 'auto', p: 1.5 }}>
            {messages.length === 0 && (
              <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', mt: 4 }}>
                你可以问我关于基金分析的问题
              </Typography>
            )}
            {messages.map((msg, idx) => (
              <Box
                key={idx}
                sx={{
                  mb: 1.5,
                  display: 'flex',
                  justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                }}
              >
                <Paper
                  sx={{
                    px: 1.5,
                    py: 1,
                    maxWidth: '80%',
                    bgcolor: msg.role === 'user' ? 'primary.main' : 'grey.100',
                    color: msg.role === 'user' ? 'white' : 'text.primary',
                    borderRadius: 2,
                  }}
                >
                  <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{msg.content}</Typography>
                </Paper>
              </Box>
            ))}
            {loading && (
              <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center' }}>
                AI 正在思考...
              </Typography>
            )}
            <div ref={messagesEndRef} />
          </Box>

          {/* 输入区 */}
          <Box sx={{ p: 1.5, borderTop: '1px solid #e0e0e0', display: 'flex', gap: 1 }}>
            <TextField
              fullWidth
              size="small"
              placeholder="输入问题..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
              disabled={loading}
              multiline
              maxRows={3}
            />
            <IconButton color="primary" onClick={handleSend} disabled={loading || !input.trim()}>
              <SendIcon />
            </IconButton>
          </Box>
        </Paper>
      )}

      <Snackbar open={snackbar.open} autoHideDuration={3000} onClose={() => setSnackbar({ ...snackbar, open: false })}>
        <Alert severity={snackbar.severity}>{snackbar.message}</Alert>
      </Snackbar>
    </>
  );
};

export default AIChatWidget;
