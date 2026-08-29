/**
 * 仪表盘页面 — 今日信号概览 + 市场资金流 + 板块排行
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Box,
  Grid,
  Card,
  CardContent,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Button,
  Chip,
  Tooltip,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Snackbar,
  Alert,
  Checkbox,
  FormControlLabel,
  FormGroup,
  Divider,
  Tabs,
  Tab,
  LinearProgress,
} from '@mui/material';
import { Refresh as RefreshIcon, PlayArrow as PlayArrowIcon } from '@mui/icons-material';
import SignalIndicator from '../components/SignalIndicator';
import ScoreGauge from '../components/ScoreGauge';
import FactorRadarChart from '../components/FactorRadarChart';
import { analysisApi } from '../api/analysis';
import { fundApi } from '../api/fund';
import type { FundRealtimeOut } from '../api/fund';
import type { AnalysisResultOut, FundOut, MarketSummaryOut, MarketRegimeOut, SectorFlowItem } from '../types';

const STRENGTH_COLOR_MAP: Record<string, 'error' | 'success' | 'default'> = {
  heavy_buy: 'error',
  moderate_buy: 'error',
  light_buy: 'error',
  hold: 'default',
  light_sell: 'success',
  moderate_sell: 'success',
  heavy_sell: 'success',
};

const formatAmount = (v: number): string => {
  if (v === 0) return '0亿';
  const abs = Math.abs(v);
  if (abs >= 1) return v.toFixed(2) + '亿';
  return (v * 10000).toFixed(0) + '万';
};

const flowColor = (v: number): string => v > 0 ? '#f44336' : v < 0 ? '#4caf50' : '#999';

/** 估值分位配色：高位红（贵）/低位绿（便宜）/中位橙 */
const regimeColor = (pct: number): string => pct > 0.6 ? '#f44336' : pct < 0.4 ? '#4caf50' : '#ff9800';
const regimeLabel = (pct: number): string =>
  pct > 0.8 ? '高估' : pct > 0.6 ? '偏贵' : pct < 0.2 ? '低估' : pct < 0.4 ? '偏便宜' : '中性';

const Dashboard: React.FC = () => {
  const [results, setResults] = useState<AnalysisResultOut[]>([]);
  const [summary, setSummary] = useState<MarketSummaryOut | null>(null);
  const [regime, setRegime] = useState<MarketRegimeOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' | 'info' });
  const [selectedFund, setSelectedFund] = useState<AnalysisResultOut | null>(null);

  // 流式分析进度
  const [streaming, setStreaming] = useState<{ active: boolean; current: number; total: number } | null>(null);
  const streamControlRef = React.useRef<{ abort: () => void } | null>(null);

  // 基金选择弹窗
  const [dialogOpen, setDialogOpen] = useState(false);
  const [availableFunds, setAvailableFunds] = useState<FundOut[]>([]);
  const [selectedFundIds, setSelectedFundIds] = useState<number[]>([]);

  const [sectorTab, setSectorTab] = useState(0);
  const [refreshTime, setRefreshTime] = useState<string | null>(null);

  // ── 实时净值预估（场外基金为主）──
  // 触发时机：页面加载/刷新时一次（force 取新）+ 定时推送任务预热缓存。
  // 不做前端轮询（2026-08-29 设计变更）；后端快照 60s 缓存防刷新风暴。
  const [realtimeMap, setRealtimeMap] = useState<Record<string, FundRealtimeOut>>({});

  const loadRealtime = useCallback(async (force = false) => {
    try {
      const res = await fundApi.realtime(force);
      if (res.data && Object.keys(res.data).length > 0) {
        setRealtimeMap(res.data);
      }
    } catch {
      // 实时估值为增强功能，失败静默（不打扰主流程）
    }
  }, []);

  useEffect(() => {
    loadRealtime(true);
  }, [loadRealtime]);

  /** 格式化更新时间为北京时间显示
   *  后端返回北京时间墙钟的 naive ISO 串（无时区标记）→ 直接展示；
   *  若带时区标记（Z / ±hh:mm）→ 按 Instant 换算到 Asia/Shanghai。 */
  const formatRefreshTime = (isoStr: string | null): string => {
    if (!isoStr) return '暂无';
    try {
      if (/[Zz]$|[+-]\d{2}:?\d{2}$/.test(isoStr)) {
        const d = new Date(isoStr);
        if (!isNaN(d.getTime())) {
          return new Intl.DateTimeFormat('zh-CN', {
            timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
            day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
          }).format(d).split('/').join('-');
        }
      }
      const m = isoStr.match(/(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
      return m ? `${m[1]} ${m[2]} (北京时间)` : isoStr;
    } catch {
      return isoStr;
    }
  };

  /** 加载缓存数据（快速） */
  const loadCached = async () => {
    setLoading(true);
    try {
      const [res, sumRes, regimeRes] = await Promise.all([
        analysisApi.latest(),
        analysisApi.summary().catch(() => null),
        analysisApi.marketRegime().catch(() => null),
      ]);
      if (regimeRes?.data) {
        setRegime(regimeRes.data);
      }
      if (res.data) {
        setResults(res.data);
        if (res.data.length > 0 && !selectedFund) {
          setSelectedFund(res.data[0]);
        }
      }
      if (sumRes?.data) {
        setSummary(sumRes.data);
        setRefreshTime(sumRes.data.updated_at || null);
      }
    } catch (err: any) {
      setSnackbar({ open: true, message: '加载数据失败', severity: 'error' });
    } finally {
      setLoading(false);
    }
  };

  /** 后台刷新行情数据 */
  const refreshInBackground = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      await analysisApi.refreshSummary();
      // 重载最新数据
      const [res, sumRes] = await Promise.all([
        analysisApi.latest(),
        analysisApi.summary(),
      ]);
      if (res.data) {
        setResults(res.data);
      }
      if (sumRes?.data) {
        setSummary(sumRes.data);
        setRefreshTime(sumRes.data.updated_at || new Date().toLocaleString('zh-CN'));
      }
    } catch (err: any) {
      console.error('刷新行情失败', err);
    } finally {
      setRefreshing(false);
    }
  };

  // 首次加载：只展示缓存，不自动刷新
  useEffect(() => {
    loadCached();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 组件卸载时中断流式请求
  useEffect(() => {
    return () => streamControlRef.current?.abort();
  }, []);

  // 当有新结果且未选中任何基金时，自动选中第一个
  useEffect(() => {
    if (results.length > 0 && !selectedFund) {
      setSelectedFund(results[0]);
    }
  }, [results, selectedFund]);

  // 打开选择弹窗时加载基金列表
  const openSelectDialog = async () => {
    try {
      const res = await fundApi.list('active');
      if (res.data) {
        setAvailableFunds(res.data);
        setSelectedFundIds(res.data.map((f) => f.id));
      }
    } catch (err: any) {
      setSnackbar({ open: true, message: '加载基金列表失败', severity: 'error' });
      return;
    }
    setDialogOpen(true);
  };

  const handleToggleFund = (id: number) => {
    setSelectedFundIds((prev) =>
      prev.includes(id) ? prev.filter((fid) => fid !== id) : [...prev, id]
    );
  };

  const handleSelectAll = () => {
    setSelectedFundIds(availableFunds.map((f) => f.id));
  };

  const handleDeselectAll = () => {
    setSelectedFundIds([]);
  };

  const handleTriggerAnalysis = () => {
    setDialogOpen(false);
    if (selectedFundIds.length === 0) {
      setSnackbar({ open: true, message: '请至少选择一只基金', severity: 'error' });
      return;
    }
    const ids = selectedFundIds.length === availableFunds.length ? undefined : selectedFundIds;

    setResults([]);
    setSelectedFund(null);
    setStreaming({ active: true, current: 0, total: selectedFundIds.length });

    const control = analysisApi.triggerStream(ids, {
      onProgress: (current, total) => {
        setStreaming({ active: true, current, total });
      },
      onChunk: (chunkResults) => {
        setResults((prev) => [...prev, ...chunkResults]);
      },
      onComplete: async (total, succeeded) => {
        setStreaming(null);
        setSnackbar({ open: true, message: `分析完成 (${succeeded}/${total})`, severity: 'success' });
        // 刷新汇总
        const sumRes = await analysisApi.summary().catch(() => null);
        if (sumRes?.data) setSummary(sumRes.data);
        setRefreshTime(new Date().toLocaleString('zh-CN'));
      },
      onError: (error) => {
        setStreaming(null);
        setSnackbar({ open: true, message: `分析失败: ${error}`, severity: 'error' });
      },
    });
    streamControlRef.current = control;
  };

  const handleCancelStream = () => {
    streamControlRef.current?.abort();
    streamControlRef.current = null;
    setStreaming(null);
    setSnackbar({ open: true, message: '分析已取消', severity: 'info' });
  };

  // 统计信号分布
  const buyCount = results.filter((r) => r.signal_direction === 'buy').length;
  const sellCount = results.filter((r) => r.signal_direction === 'sell').length;
  const holdCount = results.filter((r) => r.signal_direction === 'hold').length;

  // 板块排行
  const sectorRankings = summary?.sector_flow || [];

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Typography variant="h5">仪表盘</Typography>
          <Chip size="small" label={`数据更新: ${formatRefreshTime(refreshTime)}`}
            variant="outlined" sx={{ fontSize: '0.75rem' }} />
          {refreshing && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <CircularProgress size={14} />
              <Typography variant="caption" color="text.secondary">正在刷新行情...</Typography>
            </Box>
          )}
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={refreshInBackground}
            disabled={refreshing || !!streaming?.active}
          >
            {refreshing ? '刷新中...' : '刷新'}
          </Button>
          <Button
            variant="contained"
            startIcon={<PlayArrowIcon />}
            onClick={openSelectDialog}
            disabled={loading || !!streaming?.active}
          >
            手动触发分析
          </Button>
        </Box>
      </Box>

      {/* ── 流式分析进度条 ── */}
      {streaming?.active && (
        <Box sx={{ mb: 3, display: 'flex', alignItems: 'center', gap: 2 }}>
          <Box sx={{ flex: 1 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
              <Typography variant="body2" color="text.secondary">
                正在分析基金...
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {streaming.current} / {streaming.total}
              </Typography>
            </Box>
            <Box
              sx={{
                width: '100%',
                height: 8,
                bgcolor: 'action.hover',
                borderRadius: 4,
                overflow: 'hidden',
              }}
            >
              <Box
                sx={{
                  width: `${Math.round((streaming.current / streaming.total) * 100)}%`,
                  height: '100%',
                  bgcolor: 'primary.main',
                  borderRadius: 4,
                  transition: 'width 0.3s ease',
                }}
              />
            </Box>
          </Box>
          <Button size="small" variant="outlined" color="error" onClick={handleCancelStream}>
            取消
          </Button>
        </Box>
      )}

      {/* ── 信号概览卡片 ── */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={4}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="h6" sx={{ color: 'var(--signal-buy)' }}>买入信号</Typography>
              <Typography variant="h3" sx={{ color: 'var(--signal-buy)' }}>{buyCount}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={4}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="h6" sx={{ color: 'var(--signal-hold)' }}>观望持有</Typography>
              <Typography variant="h3" sx={{ color: 'var(--signal-hold)' }}>{holdCount}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={4}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="h6" sx={{ color: 'var(--signal-sell)' }}>卖出信号</Typography>
              <Typography variant="h3" sx={{ color: 'var(--signal-sell)' }}>{sellCount}</Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* ── 涨跌分布 + 两市成交额 ── */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={4}>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle2" gutterBottom>涨跌分布</Typography>
              {summary?.adv_decline ? (
                <Box>
                  <Box sx={{ display: 'flex', gap: 3, alignItems: 'baseline' }}>
                    <Box>
                      <Typography variant="caption" color="text.secondary">上涨</Typography>
                      <Typography variant="h5" sx={{ color: '#f44336' }}>{summary.adv_decline.up_count}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">下跌</Typography>
                      <Typography variant="h5" sx={{ color: '#4caf50' }}>{summary.adv_decline.down_count}</Typography>
                    </Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary">总计</Typography>
                      <Typography variant="h5">{summary.adv_decline.total_count}</Typography>
                    </Box>
                  </Box>
                  <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
                    上涨占比 {summary.adv_decline.total_count > 0
                      ? (summary.adv_decline.up_count / summary.adv_decline.total_count * 100).toFixed(1)
                      : '0.0'}%
                  </Typography>
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">暂无数据</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={4}>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle2" gutterBottom>两市成交额</Typography>
              {summary?.turnover ? (
                <Box>
                  <Typography variant="h5">{summary.turnover.total_amount.toFixed(0)}<Typography variant="caption" sx={{ ml: 0.5 }}>亿</Typography></Typography>
                  <Typography variant="caption" color="text.secondary">
                    沪 {summary.turnover.sse_amount.toFixed(0)}亿 &nbsp;|&nbsp; 深 {summary.turnover.szse_amount.toFixed(0)}亿
                  </Typography>
                  <br />
                  <Typography variant="caption" sx={{ color: flowColor(summary.turnover.change_pct) }}>
                    较上日 {summary.turnover.change_pct >= 0 ? '+' : ''}{summary.turnover.change_pct}%
                  </Typography>
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">暂无数据</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* ── 市场环境 — 估值分位 / 情绪 / 资金面 ── */}
      <Typography variant="h6" gutterBottom sx={{ mt: 1 }}>市场环境</Typography>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {/* 大盘估值分位 */}
        <Grid item xs={4}>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>大盘估值分位（沪深300 PE 近5年）</Typography>
              {regime?.valuation_percentile != null ? (
                <Box>
                  <Typography variant="h4" sx={{ color: regimeColor(regime.valuation_percentile) }}>
                    {(regime.valuation_percentile * 100).toFixed(0)}%
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    当前 PE {regime.valuation_current_pe} · {regime.valuation_date}
                    {' '}· {regimeLabel(regime.valuation_percentile)}
                  </Typography>
                  <LinearProgress
                    variant="determinate"
                    value={regime.valuation_percentile * 100}
                    sx={{ mt: 1, height: 6, borderRadius: 3 }}
                    color={regime.valuation_percentile > 0.6 ? 'error' : regime.valuation_percentile < 0.4 ? 'success' : 'warning'}
                  />
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">暂无数据</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* 市场情绪 */}
        <Grid item xs={4}>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>市场情绪（涨跌家数比）</Typography>
              {regime?.adv_decline_ratio != null ? (
                <Box>
                  <Typography variant="h4" sx={{ color: flowColor(regime.adv_decline_ratio) }}>
                    {regime.adv_decline_ratio >= 0 ? '+' : ''}{regime.adv_decline_ratio.toFixed(2)}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    涨 {regime.up_count?.toLocaleString() ?? '-'} / 跌 {regime.down_count?.toLocaleString() ?? '-'}
                  </Typography>
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">暂无数据</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* 资金面 */}
        <Grid item xs={4}>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>资金面（两融余额 7 日变化）</Typography>
              {regime?.margin_change_pct_7d != null ? (
                <Box>
                  <Typography variant="h4" sx={{ color: flowColor(regime.margin_change_pct_7d) }}>
                    {regime.margin_change_pct_7d >= 0 ? '+' : ''}{(regime.margin_change_pct_7d * 100).toFixed(2)}%
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    余额 {(regime.margin_balance ?? 0) / 1e12 >= 1
                      ? `${((regime.margin_balance ?? 0) / 1e12).toFixed(2)} 万亿`
                      : `${((regime.margin_balance ?? 0) / 1e8).toFixed(0)} 亿`} 元 · {regime.margin_date}
                  </Typography>
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">暂无数据</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* ── 市场概况 — TOP10 + 资金流 + 板块 ── */}
      <Typography variant="h6" gutterBottom sx={{ mt: 1 }}>市场概况</Typography>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {/* TOP10 买入 */}
        <Grid item xs={6}>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle2" sx={{ color: '#f44336', mb: 1 }}>TOP10 买入信号</Typography>
              {summary?.signals.top_buy.length ? (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ p: 0.5, fontSize: '0.75rem' }}>名称</TableCell>
                      <TableCell sx={{ p: 0.5, fontSize: '0.75rem' }} align="right">评分</TableCell>
                      <TableCell sx={{ p: 0.5, fontSize: '0.75rem' }} align="right">强度</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {summary.signals.top_buy.map((r) => (
                      <TableRow key={r.id} hover sx={{ cursor: 'pointer' }} onClick={() => setSelectedFund(r)}>
                        <TableCell sx={{ p: 0.5, fontSize: '0.8rem' }}>{r.fund_name}</TableCell>
                        <TableCell sx={{ p: 0.5, fontSize: '0.8rem', color: '#f44336' }} align="right">{r.weighted_score}</TableCell>
                        <TableCell sx={{ p: 0.5 }} align="right">
                          <Chip label={r.signal_strength} size="small" color={STRENGTH_COLOR_MAP[r.signal_strength] || 'default'} sx={{ height: 20, fontSize: '0.65rem' }} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <Typography variant="body2" color="text.secondary">暂无买入信号</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* TOP10 卖出 */}
        <Grid item xs={6}>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle2" sx={{ color: '#4caf50', mb: 1 }}>TOP10 卖出信号</Typography>
              {summary?.signals.top_sell.length ? (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ p: 0.5, fontSize: '0.75rem' }}>名称</TableCell>
                      <TableCell sx={{ p: 0.5, fontSize: '0.75rem' }} align="right">评分</TableCell>
                      <TableCell sx={{ p: 0.5, fontSize: '0.75rem' }} align="right">强度</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {summary.signals.top_sell.map((r) => (
                      <TableRow key={r.id} hover sx={{ cursor: 'pointer' }} onClick={() => setSelectedFund(r)}>
                        <TableCell sx={{ p: 0.5, fontSize: '0.8rem' }}>{r.fund_name}</TableCell>
                        <TableCell sx={{ p: 0.5, fontSize: '0.8rem', color: '#4caf50' }} align="right">{r.weighted_score}</TableCell>
                        <TableCell sx={{ p: 0.5 }} align="right">
                          <Chip label={r.signal_strength} size="small" color={STRENGTH_COLOR_MAP[r.signal_strength] || 'default'} sx={{ height: 20, fontSize: '0.65rem' }} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <Typography variant="body2" color="text.secondary">暂无卖出信号</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* 大盘资金流 + 沪深港通 */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={6}>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle2" gutterBottom>大盘资金流</Typography>
              {summary?.market_flow ? (
                <Box>
                  <Typography variant="body2">
                    上证: <b>{summary.market_flow.sh_index ?? '-'}</b>
                    <span style={{ color: flowColor(summary.market_flow.sh_change ?? 0), marginLeft: 4 }}>
                      {summary.market_flow.sh_change ?? '-'}%
                    </span>
                    &nbsp;&nbsp;|&nbsp;&nbsp;
                    深证: <b>{summary.market_flow.sz_index ?? '-'}</b>
                    <span style={{ color: flowColor(summary.market_flow.sz_change ?? 0), marginLeft: 4 }}>
                      {summary.market_flow.sz_change ?? '-'}%
                    </span>
                  </Typography>
                  <Divider sx={{ my: 1 }} />
                  <Typography variant="body2">
                    主力净流入: <span style={{ color: flowColor(summary.market_flow.main_flow.net_amount) }}>
                      <b>{formatAmount(summary.market_flow.main_flow.net_amount)}</b>
                    </span>
                    &nbsp;(占比: {summary.market_flow.main_flow.net_ratio}%)
                  </Typography>
                  <Typography variant="body2" sx={{ fontSize: '0.75rem', color: 'text.secondary', mt: 0.5 }}>
                    超大单: {formatAmount(summary.market_flow.main_flow.super_large_net)}&nbsp;
                    大单: {formatAmount(summary.market_flow.main_flow.large_net)}&nbsp;
                    中单: {formatAmount(summary.market_flow.main_flow.medium_net)}&nbsp;
                    小单: {formatAmount(summary.market_flow.main_flow.small_net)}
                  </Typography>
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">暂无资金流数据</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={6}>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle2" gutterBottom>沪深港通</Typography>
              {summary?.hsgt_flow ? (
                <Box>
                  <Typography variant="body2">
                    北向资金: <span style={{ color: flowColor(summary.hsgt_flow.north_net_buy) }}>
                      <b>{formatAmount(summary.hsgt_flow.north_net_buy)}</b>
                    </span>
                  </Typography>
                  <Typography variant="body2">
                    南向资金: <span style={{ color: flowColor(summary.hsgt_flow.south_net_buy) }}>
                      <b>{formatAmount(summary.hsgt_flow.south_net_buy)}</b>
                    </span>
                  </Typography>
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">暂无沪深港通数据</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* 行业板块资金流排行 */}
      {sectorRankings.length > 0 && (
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle2" gutterBottom>行业板块资金流排行</Typography>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Tabs value={sectorTab} onChange={(_, v) => setSectorTab(v)} sx={{ minHeight: 36, mb: 1 }}>
                {sectorRankings.map((sr) => (
                  <Tab key={sr.timeframe} label={sr.timeframe} sx={{ minHeight: 36, fontSize: '0.8rem' }} />
                ))}
              </Tabs>
              {sectorRankings.map((sr, idx) => (
                <Box key={sr.timeframe} sx={{ display: idx === sectorTab ? 'block' : 'none' }}>
                  {idx === sectorTab && (
                    <Grid container spacing={2}>
                      <Grid item xs={6}>
                        <Typography variant="caption" sx={{ color: '#f44336', fontWeight: 'bold' }}>主力流入 TOP</Typography>
                        <SectorFlowTable items={sr.by_inflow} />
                      </Grid>
                      <Grid item xs={6}>
                        <Typography variant="caption" sx={{ color: '#4caf50', fontWeight: 'bold' }}>主力流出 TOP</Typography>
                        <SectorFlowTable items={sr.by_outflow} />
                      </Grid>
                    </Grid>
                  )}
                </Box>
              ))}
            </CardContent>
          </Card>
        </Box>
      )}

      <Grid container spacing={3}>
        {/* 基金列表 */}
        <Grid item xs={7}>
          <Typography variant="h6" gutterBottom>基金分析列表</Typography>
          <TableContainer component={Paper}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>基金代码</TableCell>
                  <TableCell>基金名称</TableCell>
                  <TableCell>实时估值</TableCell>
                  <TableCell>评分(-6~+6)</TableCell>
                  <TableCell>权益仓位</TableCell>
                  <TableCell>信号</TableCell>
                  <TableCell>强度</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {results.map((r) => {
                  const rt = realtimeMap[r.fund_code];
                  const pct = rt?.growth_pct;
                  return (
                  <TableRow
                    key={r.id}
                    hover
                    selected={selectedFund?.id === r.id}
                    onClick={() => setSelectedFund(r)}
                    sx={{ cursor: 'pointer' }}
                  >
                    <TableCell>{r.fund_code}</TableCell>
                    <TableCell>{r.fund_name}</TableCell>
                    <TableCell>
                      {pct != null ? (
                        <Tooltip
                          title={
                            (rt.source === 'fundgz'
                              ? '天天基金官方盘中估值'
                              : rt.source === 'etf_spot'
                              ? '场内实时行情'
                              : `持仓加权估算（覆盖率 ${Math.round((rt.coverage ?? 0) * 100)}%，${rt.est_model === 'index_blend' ? '含指数混合' : '归一法'}）`)
                            + (rt.quote_time ? `\n行情时间: ${rt.quote_time}` : '')
                          }
                        >
                          <span style={{
                            color: pct > 0 ? '#f44336' : pct < 0 ? '#4caf50' : 'inherit',
                            fontWeight: 500,
                          }}>
                            {pct > 0 ? '+' : ''}{pct.toFixed(2)}%
                          </span>
                        </Tooltip>
                      ) : (
                        <span style={{ color: '#bbb' }}>—</span>
                      )}
                    </TableCell>
                    <TableCell>{r.weighted_score}</TableCell>
                    <TableCell>{Math.round((r as any).equity_ratio * 100)}%</TableCell>
                    <TableCell>
                      <SignalIndicator direction={r.signal_direction} size={12} showLabel={false} />
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={r.signal_strength}
                        size="small"
                        color={STRENGTH_COLOR_MAP[r.signal_strength] || 'default'}
                      />
                    </TableCell>
                  </TableRow>
                  );
                })}
                {results.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={7} align="center">暂无分析数据</TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </Grid>

        {/* 选中基金详情 */}
        <Grid item xs={5}>
          {selectedFund ? (
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  {selectedFund.fund_name} ({selectedFund.fund_code})
                </Typography>
                <ScoreGauge score={selectedFund.weighted_score} height={180} />

                {/* ── 第零层质量过滤信息 ── */}
                {selectedFund.original_score != null &&
                  selectedFund.original_score !== selectedFund.weighted_score && (
                    <Box sx={{ mt: 1, mb: 1, p: 1, bgcolor: 'action.hover', borderRadius: 1 }}>
                      <Typography variant="caption" color="text.secondary">
                        质量过滤修正：原始评分 <b>{selectedFund.original_score.toFixed(2)}</b>
                        {' → 修正后 '}<b>{selectedFund.weighted_score.toFixed(2)}</b>
                      </Typography>
                    </Box>
                  )}
                {selectedFund.dynamic_buy_threshold != null &&
                  selectedFund.dynamic_buy_threshold !== 1.5 && (
                    <Chip
                      size="small"
                      label={`买入阈值: ${selectedFund.dynamic_buy_threshold}（动态调整）`}
                      color="warning"
                      variant="outlined"
                      sx={{ mt: 0.5, mb: 0.5 }}
                    />
                  )}
                {selectedFund.quality_warnings?.map((w, i) => (
                  <Alert key={i} severity="warning" sx={{ mt: 0.5, py: 0, fontSize: '0.75rem' }}>
                    {w}
                  </Alert>
                ))}

                <FactorRadarChart factorScores={selectedFund.factor_scores} height={250} />
                <Typography variant="body2" sx={{ mt: 1 }}>
                  {selectedFund.operation_advice}
                </Typography>
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent>
                <Typography color="text.secondary">点击左侧基金查看详情</Typography>
              </CardContent>
            </Card>
          )}
        </Grid>
      </Grid>

      {/* 基金选择弹窗 */}
      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>选择要分析的基金</DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', gap: 1, mb: 2, mt: 1 }}>
            <Button size="small" onClick={handleSelectAll}>全选</Button>
            <Button size="small" onClick={handleDeselectAll}>取消全选</Button>
            <Typography variant="body2" sx={{ ml: 'auto', alignSelf: 'center', color: 'text.secondary' }}>
              已选 {selectedFundIds.length} / {availableFunds.length}
            </Typography>
          </Box>
          <Divider sx={{ mb: 1 }} />
          {availableFunds.length === 0 ? (
            <Typography color="text.secondary">暂无可用基金</Typography>
          ) : (
            <FormGroup>
              {availableFunds.map((fund) => (
                <FormControlLabel
                  key={fund.id}
                  control={
                    <Checkbox
                      checked={selectedFundIds.includes(fund.id)}
                      onChange={() => handleToggleFund(fund.id)}
                    />
                  }
                  label={
                    <Typography variant="body2">
                      {fund.code} - {fund.name}
                      <Chip
                        label={fund.fund_type}
                        size="small"
                        variant="outlined"
                        sx={{ ml: 1, height: 20, fontSize: '0.7rem' }}
                      />
                    </Typography>
                  }
                />
              ))}
            </FormGroup>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>取消</Button>
          <Button
            variant="contained"
            onClick={handleTriggerAnalysis}
            disabled={selectedFundIds.length === 0 || loading}
          >
            开始分析 ({selectedFundIds.length})
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={3000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
      >
        <Alert severity={snackbar.severity}>{snackbar.message}</Alert>
      </Snackbar>
    </Box>
  );
};

/** 板块资金流子表格 */
const SectorFlowTable: React.FC<{ items: SectorFlowItem[] }> = ({ items }) => {
  if (!items.length) return <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>暂无数据</Typography>;
  return (
    <Table size="small" sx={{ '& td, & th': { p: 0.3, fontSize: '0.75rem' } }}>
      <TableHead>
        <TableRow>
          <TableCell>板块</TableCell>
          <TableCell align="right">主力净流入</TableCell>
          <TableCell align="right">涨跌幅</TableCell>
          <TableCell>领涨股</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {items.map((item, i) => (
          <TableRow key={i}>
            <TableCell>{item.sector_name}</TableCell>
            <TableCell align="right" sx={{ color: flowColor(item.main_net_inflow) }}>
              {formatAmount(item.main_net_inflow)}
            </TableCell>
            <TableCell align="right" sx={{ color: flowColor(item.change_pct) }}>
              {item.change_pct}%
            </TableCell>
            <TableCell sx={{ fontSize: '0.7rem' }}>{item.top_stock}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
};

export default Dashboard;
