/**
 * 仪表盘页面 — 今日信号概览 + 市场资金流 + 板块排行
 */

import React, { useEffect, useState } from 'react';
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
} from '@mui/material';
import { Refresh as RefreshIcon, PlayArrow as PlayArrowIcon } from '@mui/icons-material';
import SignalIndicator from '../components/SignalIndicator';
import ScoreGauge from '../components/ScoreGauge';
import FactorRadarChart from '../components/FactorRadarChart';
import { analysisApi } from '../api/analysis';
import { fundApi } from '../api/fund';
import type { AnalysisResultOut, FundOut, MarketSummaryOut, SectorFlowItem } from '../types';

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

const Dashboard: React.FC = () => {
  const [results, setResults] = useState<AnalysisResultOut[]>([]);
  const [summary, setSummary] = useState<MarketSummaryOut | null>(null);
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

  /** 格式化缓存更新时间为北京时间显示 */
  const formatRefreshTime = (isoStr: string | null): string => {
    if (!isoStr) return '暂无';
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      const bj = new Date(d.getTime() + 8 * 60 * 60 * 1000);
      const pad = (n: number) => String(n).padStart(2, '0');
      return `${bj.getUTCFullYear()}-${pad(bj.getUTCMonth() + 1)}-${pad(bj.getUTCDate())} ${pad(bj.getUTCHours())}:${pad(bj.getUTCMinutes())} (北京时间)`;
    } catch {
      return isoStr;
    }
  };

  /** 加载缓存数据（快速） */
  const loadCached = async () => {
    setLoading(true);
    try {
      const [res, sumRes] = await Promise.all([
        analysisApi.latest(),
        analysisApi.summary().catch(() => null),
      ]);
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

      {/* ── 市场概况 — TOP5 + 资金流 + 板块 ── */}
      <Typography variant="h6" gutterBottom sx={{ mt: 1 }}>市场概况</Typography>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {/* TOP5 买入 */}
        <Grid item xs={6}>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle2" sx={{ color: '#f44336', mb: 1 }}>TOP 买入信号</Typography>
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

        {/* TOP5 卖出 */}
        <Grid item xs={6}>
          <Card variant="outlined">
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle2" sx={{ color: '#4caf50', mb: 1 }}>TOP 卖出信号</Typography>
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
                  <TableCell>评分(-6~+6)</TableCell>
                  <TableCell>权益仓位</TableCell>
                  <TableCell>信号</TableCell>
                  <TableCell>强度</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {results.map((r) => (
                  <TableRow
                    key={r.id}
                    hover
                    selected={selectedFund?.id === r.id}
                    onClick={() => setSelectedFund(r)}
                    sx={{ cursor: 'pointer' }}
                  >
                    <TableCell>{r.fund_code}</TableCell>
                    <TableCell>{r.fund_name}</TableCell>
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
                ))}
                {results.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} align="center">暂无分析数据</TableCell>
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
