/**
 * AI 工作台 — Agent 流式分析前台
 *
 * 左栏：最近会话列表（点选回放）；主区：三个一键任务卡（因子诊断/调仓分析/今日简报）
 * + 自由提问；运行过程渲染工具时间线（每轮折叠卡：入参/耗时/字符数/成败）
 * 与流式 Markdown 正文（delta_reset 时丢弃工具轮作废文本）。
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  IconButton,
  List,
  ListItemButton,
  ListItemText,
  Paper,
  TextField,
  Typography,
} from '@mui/material';
import {
  Add as AddIcon,
  ExpandMore as ExpandIcon,
  PlayArrow as PlayIcon,
  Science as ScienceIcon,
  SmartToy as AgentIcon,
  Stop as StopIcon,
  SwapHoriz as SwapIcon,
  Today as TodayIcon,
} from '@mui/icons-material';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { agentApi, type AgentEvent, type AgentRunRequest } from '../api/agent';
import { aiApi } from '../api/ai';

interface Msg { role: 'user' | 'assistant'; content: string }
interface Trace { tool: string; arguments: Record<string, unknown>; ok?: boolean; ms?: number; chars?: number; running: boolean }

const TASKS = [
  { id: 'factor_audit', title: '因子诊断', icon: <ScienceIcon />, desc: 'RankIC/胜率/whipsaw — 解读因子与策略效果', params: { days: 90 } },
  { id: 'rebalance', title: '调仓分析', icon: <SwapIcon />, desc: '四清单+组合约束 — 解读执行顺序与换仓代价', params: { window_days: 30 } },
  { id: 'daily_brief', title: '今日简报', icon: <TodayIcon />, desc: '信号概况+调仓摘要+市场环境 — 生成每日行动简报', params: {} },
];

const MdBox: React.FC<{ text: string }> = ({ text }) => (
  <Box sx={{ '& table': { borderCollapse: 'collapse', fontSize: 13, my: 1 },
    '& th, & td': { border: '1px solid', borderColor: 'divider', px: 1, py: 0.5 },
    '& pre': { bgcolor: 'action.hover', p: 1, borderRadius: 1, overflowX: 'auto', fontSize: 12 },
    '& code': { bgcolor: 'action.hover', px: 0.5, borderRadius: 0.5, fontSize: 13 },
    '& ul, & ol': { pl: 2.5 }, fontSize: 14 }}>
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
  </Box>
);

const ToolTimeline: React.FC<{ traces: Trace[]; note?: string }> = ({ traces, note }) => {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (!traces.length && !note) return null;
  return (
    <Paper variant="outlined" sx={{ p: 1.5, my: 1 }}>
      {note && <Typography variant="caption" color="text.secondary">{note}</Typography>}
      {traces.map((t, i) => {
        const key = `${t.tool}-${i}`;
        return (
          <Box key={key} sx={{ mb: 0.5 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
              <Chip size="small" label={t.tool} color={t.running ? 'primary' : t.ok ? 'default' : 'error'}
                variant={t.running ? 'filled' : 'outlined'} />
              {t.running ? <CircularProgress size={12} /> : (
                <Typography variant="caption" color="text.secondary">
                  {t.ok ? `${t.ms}ms · ${t.chars} 字符` : '失败'}
                </Typography>
              )}
              <IconButton size="small" onClick={() => setOpen((o) => ({ ...o, [key]: !o[key] }))}>
                <ExpandIcon fontSize="inherit" sx={{ transform: open[key] ? 'rotate(180deg)' : 'none' }} />
              </IconButton>
            </Box>
            <Collapse in={!!open[key]}>
              <Box component="pre" sx={{ m: 0.5, p: 1, bgcolor: 'action.hover', fontSize: 12, borderRadius: 1, overflowX: 'auto' }}>
                {JSON.stringify(t.arguments ?? {}, null, 2)}
              </Box>
            </Collapse>
          </Box>
        );
      })}
    </Paper>
  );
};

const AiWorkbench: React.FC = () => {
  const [convs, setConvs] = useState<{ conversation_id: string; preview: string; last_at: string; turns: number }[]>([]);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [activeConv, setActiveConv] = useState<string | null>(null);

  const [running, setRunning] = useState(false);
  const [streamText, setStreamText] = useState('');
  const [traces, setTraces] = useState<Trace[]>([]);
  const [ctxNote, setCtxNote] = useState('');
  const [runError, setRunError] = useState('');
  const [status, setStatus] = useState('');
  const [input, setInput] = useState('');
  const abortRef = useRef<{ abort: () => void } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadConvs = () => {
    agentApi.listConversations().then((r: any) => setConvs(r?.items ?? [])).catch(() => undefined);
  };
  useEffect(loadConvs, []);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, streamText, traces]);

  const resetRun = () => { setStreamText(''); setTraces([]); setCtxNote(''); setRunError(''); setStatus(''); };

  const openConv = async (cid: string) => {
    if (running) return;
    setActiveConv(cid);
    setMessages([]);
    resetRun();
    try {
      const res = await aiApi.getConversations(cid);
      const items = (res.data as unknown as { role: string; content: string }[] || [])
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role as Msg['role'], content: m.content }));
      setMessages(items);
    } catch { /* 静默 */ }
  };

  const run = (body: AgentRunRequest) => {
    if (running) return;
    resetRun();
    setRunning(true);
    if (body.prompt) setMessages((m) => [...m, { role: 'user', content: body.prompt! }]);
    abortRef.current = agentApi.run({ ...body, conversation_id: activeConv }, {
      onEvent: (ev: AgentEvent) => {
        switch (ev.type) {
          case 'start':
            setActiveConv((prev) => prev || ev.conversation_id || null);
            setStatus(`模型 ${ev.model} · ${ev.tools} 个工具`);
            break;
          case 'context': setCtxNote(ev.note || ''); break;
          case 'round': setStatus((s) => `${s} · 第 ${ev.round} 轮`); break;
          case 'delta': setCtxNote(''); setStreamText((t) => t + (ev.text || '')); break;
          case 'delta_reset': setStreamText(''); break;
          case 'tool_call':
            setTraces((ts) => [...ts, { tool: ev.name || '?', arguments: ev.arguments || {}, running: true }]);
            break;
          case 'tool_result':
            setTraces((ts) => {
              const idx = ts.findIndex((t) => t.running && t.tool === ev.tool);
              const done: Trace = { tool: ev.tool || '?', arguments: ev.arguments || {}, ok: ev.ok, ms: ev.ms, chars: ev.chars, running: false };
              if (idx < 0) return [...ts, done];
              const next = [...ts]; next[idx] = done; return next;
            });
            break;
          case 'final':
            setMessages((m) => [...m, { role: 'assistant', content: ev.content || '' }]);
            setStreamText('');
            setStatus(`${ev.rounds ?? '?'} 轮 · ${ev.tool_calls ?? 0} 次工具 · ${ev.tokens ?? 0} tokens`);
            break;
          case 'error': setRunError(ev.message || '运行失败'); break;
          case 'done': loadConvs(); break;
        }
      },
      onError: (msg) => { setRunError(msg); loadConvs(); },
      onFinish: () => { setRunning(false); abortRef.current = null; },
    });
  };

  const newSession = () => {
    if (running) return;
    setActiveConv(null); setMessages([]); resetRun();
  };

  return (
    <Box sx={{ p: 3, display: 'flex', gap: 2, height: 'calc(100vh - 64px)' }}>
      {/* ── 左栏：会话列表 ── */}
      <Paper variant="outlined" sx={{ width: 240, flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
        <Box sx={{ p: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
          <Button size="small" startIcon={<AddIcon />} onClick={newSession} disabled={running}>新建会话</Button>
        </Box>
        <Divider />
        <List dense sx={{ overflowY: 'auto', flex: 1 }}>
          {convs.map((c) => (
            <ListItemButton key={c.conversation_id} selected={c.conversation_id === activeConv}
              onClick={() => openConv(c.conversation_id)} disabled={running}>
              <ListItemText
                primary={c.preview || '（空）'}
                secondary={`${c.turns} 条 · ${c.last_at.slice(5, 16)}`}
                primaryTypographyProps={{ variant: 'body2', noWrap: true }}
                secondaryTypographyProps={{ variant: 'caption' }}
              />
            </ListItemButton>
          ))}
          {convs.length === 0 && (
            <Typography variant="caption" color="text.secondary" sx={{ p: 1.5 }}>暂无 Agent 会话</Typography>
          )}
        </List>
      </Paper>

      {/* ── 主区 ── */}
      <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <Typography variant="h5" sx={{ mb: 1 }}>AI 工作台</Typography>

        {/* 一键任务卡 */}
        <Box sx={{ display: 'flex', gap: 1.5, mb: 2, flexWrap: 'wrap' }}>
          {TASKS.map((t) => (
            <Card key={t.id} sx={{ width: 230 }}>
              <CardActionArea disabled={running} onClick={() => run({ task: t.id, params: t.params })}>
                <CardContent sx={{ '&:last-child': { pb: 1.5 } }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                    <AgentIcon fontSize="small" color="primary" />
                    <Typography variant="subtitle2">{t.title}</Typography>
                    <PlayIcon fontSize="small" sx={{ ml: 'auto', color: 'primary.main' }} />
                  </Box>
                  <Typography variant="caption" color="text.secondary">{t.desc}</Typography>
                  <Box sx={{ mt: 0.5 }}>{t.icon}</Box>
                </CardContent>
              </CardActionArea>
            </Card>
          ))}
        </Box>

        {/* 消息 + 运行过程 */}
        <Paper variant="outlined" sx={{ flex: 1, overflowY: 'auto', p: 2 }}>
          {messages.length === 0 && !running && !streamText && (
            <Typography variant="body2" color="text.secondary">
              点击上方任务卡一键运行，或在下方输入问题。Agent 通过只读工具查询真实数据（评分/信号/持仓/因子统计），
              统计口径由 Python 引擎产出，AI 仅负责解读。
            </Typography>
          )}
          {messages.map((m, i) => (
            <Box key={i} sx={{ mb: 2 }}>
              <Chip size="small" label={m.role === 'user' ? '我' : 'Agent'} color={m.role === 'user' ? 'default' : 'primary'}
                sx={{ mb: 0.5, fontWeight: 600 }} />
              {m.role === 'assistant' ? <MdBox text={m.content} /> : (
                <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{m.content}</Typography>
              )}
              {i < messages.length - 1 && <Divider sx={{ mt: 2 }} />}
            </Box>
          ))}
          {(traces.length > 0 || ctxNote) && <ToolTimeline traces={traces} note={ctxNote} />}
          {streamText && (
            <Box sx={{ mb: 1 }}>
              <Chip size="small" label="Agent（流式）" color="primary" sx={{ mb: 0.5 }} />
              <MdBox text={streamText} />
            </Box>
          )}
          {runError && <Alert severity="error" sx={{ my: 1 }}>{runError}</Alert>}
          {status && <Typography variant="caption" color="text.secondary">{status}</Typography>}
          <div ref={bottomRef} />
        </Paper>

        {/* 输入行 */}
        <Box sx={{ display: 'flex', gap: 1, mt: 1.5 }}>
          <TextField
            fullWidth size="small" placeholder="向 Agent 提问（将结合工具查询真实数据）…"
            value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && input.trim()) { run({ prompt: input.trim() }); setInput(''); } }}
            disabled={running}
          />
          {running ? (
            <Button variant="outlined" color="error" startIcon={<StopIcon />}
              onClick={() => abortRef.current?.abort()}>停止</Button>
          ) : (
            <Button variant="contained" startIcon={<PlayIcon />} disabled={!input.trim()}
              onClick={() => { run({ prompt: input.trim() }); setInput(''); }}>运行</Button>
          )}
        </Box>
      </Box>
    </Box>
  );
};

export default AiWorkbench;
