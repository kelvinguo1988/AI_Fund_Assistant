/**
 * 信号回测页面 — 历史信号与净值对齐，模拟仓位策略累计收益
 */

import React, { useEffect, useState } from 'react';
import {
  Box,
  Typography,
  Button,
  TextField,
  MenuItem,
  Paper,
  Grid,
  Card,
  CardContent,
  CircularProgress,
  Snackbar,
  Alert,
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
  Chip,
  TableContainer,
} from '@mui/material';
import {
  PlayArrow as RunIcon,
  Schedule as ScheduleIcon,
  Delete as DeleteIcon,
} from '@mui/icons-material';
import {
  Switch,
  FormControlLabel,
  Tooltip,
} from '@mui/material';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { fundApi } from '../api/fund';
import { backtestApi, backtestBatchApi, type BacktestBatchItem, type AutoBacktestConfig } from '../api/backtest';
import type { FundOut, BacktestSummary } from '../types';

const STRENGTH_LABELS: Record<string, string> = {
  heavy_buy: '强烈买入',
  moderate_buy: '适度买入',
  hold: '观望',
  moderate_sell: '适度减仓',
  heavy_sell: '强烈减仓',
};

const SignalBacktest: React.FC = () => {
  const [funds, setFunds] = useState<FundOut[]>([]);
  const [selectedFundId, setSelectedFundId] = useState<number | null>(null);
  const [period, setPeriod] = useState(365);
  const [effectivenessWindow, setEffectivenessWindow] = useState(5);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<BacktestSummary | null>(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' });

  // ── 自动全量回测状态 ──
  const [autoCfg, setAutoCfg] = useState<AutoBacktestConfig | null>(null);
  const [batchRows, setBatchRows] = useState<BacktestBatchItem[]>([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);

  const loadBatch = async () => {
    setBatchLoading(true);
    try {
      const res = await backtestBatchApi.listResults();
      setBatchRows(res.data || []);
    } catch {
      // 静默：批量面板失败不影响单基金回测
    } finally {
      setBatchLoading(false);
    }
  };

  useEffect(() => {
    backtestBatchApi.getConfig()
      .then((r) => setAutoCfg(r.data))
      .catch(() => { /* 配置加载失败保持 null */ });
    loadBatch();
    // 批量结果 60s 轮询：手动触发一轮全量回测后逐只落库，可实时看到进度
    const t = setInterval(() => {
      if (batchRunning) loadBatch();
    }, 60_000);
    return () => clearInterval(t);
  }, [batchRunning]);

  useEffect(() => {
    fundApi.list().then((res) => {
      if (res.data) setFunds(res.data);
    }).catch(() => {
      setSnackbar({ open: true, message: '加载基金列表失败', severity: 'error' });
    });
  }, []);

  const handleRun = async () => {
    if (!selectedFundId) {
      setSnackbar({ open: true, message: '请先选择基金', severity: 'error' });
      return;
    }
    setLoading(true);
    setResult(null);
    try {
      const res = await backtestApi.run(selectedFundId, period, effectivenessWindow);
      if (res.data) setResult(res.data);
    } catch (err: any) {
      setSnackbar({ open: true, message: err.message || '回测失败', severity: 'error' });
    } finally {
      setLoading(false);
    }
  };

  /* ── ECharts 配置 ───────────────────────────────────────────── */
  const buildChartOption = (data: BacktestSummary): EChartsOption => {
    const dates = data.points.map((p) => p.date);
    const navReturns = data.points.map((p) => p.nav_return);
    const strategyReturns = data.points.map((p) => p.strategy_return);

    // 信号标注：scatter 在 category 轴上须用 [类目值, y值] 数对格式
    // （{xAxis, yAxis} 对象格式仅对 markPoint 生效，series.data 中不渲染）
    const buyMarkers = data.points
      .filter((p) => p.signal_direction === 'buy')
      .map((p) => ({
        value: [p.date, p.nav_return],
        name: STRENGTH_LABELS[p.signal_strength || ''] || '买入',
        itemStyle: { color: '#e53935' },
      }));

    const sellMarkers = data.points
      .filter((p) => p.signal_direction === 'sell')
      .map((p) => ({
        value: [p.date, p.nav_return],
        name: STRENGTH_LABELS[p.signal_strength || ''] || '卖出',
        itemStyle: { color: '#43a047' },
      }));

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: any) => {
          if (!Array.isArray(params) || params.length === 0) return '';
          const idx = params[0].dataIndex;
          const pt = data.points[idx];
          let html = `<b>${pt.date}</b><br/>`;
          html += `净值: ${pt.nav}<br/>`;
          html += `净值收益: ${pt.nav_return.toFixed(2)}%<br/>`;
          html += `策略收益: ${pt.strategy_return.toFixed(2)}%<br/>`;
          if (pt.signal_direction) {
            html += `信号: ${STRENGTH_LABELS[pt.signal_strength || ''] || pt.signal_direction}<br/>`;
            html += `评分: ${pt.weighted_score?.toFixed(2) ?? '-'}<br/>`;
            if (pt.signal_effectiveness != null) {
              html += `有效性: ${pt.signal_effectiveness.toFixed(1)} 分`;
            }
          } else {
            html += '信号: 无';
          }
          return html;
        },
      },
      legend: {
        data: ['净值累计收益', '策略累计收益', '买入信号', '卖出信号'],
        top: 5,
      },
      grid: { left: 60, right: 40, top: 50, bottom: 40 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: {
          formatter: (v: string) => v.slice(5), // MM-DD
          interval: Math.floor(dates.length / 8),
        },
      },
      yAxis: {
        type: 'value',
        axisLabel: { formatter: '{value}%' },
        name: '累计收益率',
      },
      dataZoom: [
        { type: 'inside', start: 0, end: 100 },
        { type: 'slider', start: 0, end: 100, height: 20, bottom: 5 },
      ],
      series: [
        {
          name: '净值累计收益',
          type: 'line',
          data: navReturns,
          smooth: true,
          lineStyle: { width: 2 },
          itemStyle: { color: '#1976D2' },
          symbol: 'none',
        },
        {
          name: '策略累计收益',
          type: 'line',
          data: strategyReturns,
          smooth: true,
          lineStyle: { width: 2 },
          itemStyle: { color: '#FF9800' },
          symbol: 'none',
        },
        {
          name: '买入信号',
          type: 'scatter',
          data: buyMarkers,
          symbol: 'triangle',
          symbolSize: 12,
          itemStyle: { color: '#e53935' },
        },
        {
          name: '卖出信号',
          type: 'scatter',
          data: sellMarkers,
          symbol: 'pin',
          symbolSize: 12,
          symbolRotate: 180,
          itemStyle: { color: '#43a047' },
        },
      ],
    };
  };

  /* ── 统计卡片颜色 ──────────────────────────────────────────── */
  const statColor = (val: number) => (val >= 0 ? '#e53935' : '#43a047');


  const saveAutoCfg = async (patch: Partial<AutoBacktestConfig>) => {
    try {
      const res = await backtestBatchApi.updateConfig(patch);
      setAutoCfg(res.data);
      setSnackbar({ open: true, message: '自动回测配置已保存并生效', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.message || '保存失败', severity: 'error' });
    }
  };

  const triggerBatch = async () => {
    setBatchRunning(true);
    try {
      await backtestBatchApi.trigger();
      setSnackbar({ open: true, message: '全量回测已启动（后台逐只执行，结果实时落库）', severity: 'success' });
      loadBatch();
    } catch (err: any) {
      setBatchRunning(false);
      setSnackbar({ open: true, message: err?.message || '触发失败', severity: 'error' });
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h5" sx={{ mb: 2 }}>
        信号回测
      </Typography>

      {/* ── 自动全量回测 ── */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1, flexWrap: 'wrap', gap: 1 }}>
            <Typography variant="h6">
              <ScheduleIcon sx={{ verticalAlign: 'middle', mr: 1 }} />
              自动全量回测
            </Typography>
            <Box sx={{ display: 'flex', gap: 1 }}>
              <Button
                size="small" variant="contained" startIcon={<RunIcon />}
                disabled={batchRunning}
                onClick={triggerBatch}
              >
                {batchRunning ? '运行中…' : '立即全量回测'}
              </Button>
              <Button size="small" startIcon={<DeleteIcon />} onClick={async () => {
                if (!window.confirm('清空全部批量回测结果？')) return;
                await backtestBatchApi.clearResults();
                loadBatch();
              }}>
                清空
              </Button>
            </Box>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', mb: 1 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={!!autoCfg?.enabled}
                  onChange={async (e) => {
                    setAutoCfg((c) => (c ? { ...c, enabled: e.target.checked } : c));
                    await saveAutoCfg({ enabled: e.target.checked });
                  }}
                  disabled={!autoCfg}
                />
              }
              label={autoCfg?.enabled ? '已开启：每周日 00:00 自动全量回测' : '已关闭'}
            />
            <Tooltip title="每只基金之间的随机等待区间（秒）。周末低峰拉长间隔防数据源封禁；60 只基金约 30~60 分钟，上限 12 小时">
              <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
                <TextField
                  label="间隔下限(秒)" size="small" type="number" sx={{ width: 120 }}
                  value={autoCfg?.min_interval ?? 20}
                  onChange={(e) => setAutoCfg((c) => (c ? { ...c, min_interval: Number(e.target.value) } : c))}
                  onBlur={() => autoCfg && saveAutoCfg({ min_interval: autoCfg.min_interval })}
                />
                <TextField
                  label="间隔上限(秒)" size="small" type="number" sx={{ width: 120 }}
                  value={autoCfg?.max_interval ?? 60}
                  onChange={(e) => setAutoCfg((c) => (c ? { ...c, max_interval: Number(e.target.value) } : c))}
                  onBlur={() => autoCfg && saveAutoCfg({ max_interval: autoCfg.max_interval })}
                />
              </Box>
            </Tooltip>
          </Box>

          {batchLoading && <CircularProgress size={20} sx={{ mb: 1 }} />}
          <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 420 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>基金</TableCell>
                  <TableCell align="right">策略收益</TableCell>
                  <TableCell align="right">净值收益</TableCell>
                  <TableCell align="right">超额</TableCell>
                  <TableCell align="right">回撤落差</TableCell>
                  <TableCell align="right">有效率</TableCell>
                  <TableCell>完成时间</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {batchRows.map((r) => (
                  <TableRow key={r.fund_id} hover>
                    <TableCell>
                      {r.fund_name}
                      <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>{r.fund_code}</Typography>
                    </TableCell>
                    <TableCell align="right" sx={{ color: (r.total_strategy_return ?? 0) >= 0 ? '#f44336' : '#4caf50' }}>
                      {r.total_strategy_return != null ? `${r.total_strategy_return.toFixed(2)}%` : '—'}
                    </TableCell>
                    <TableCell align="right">
                      {r.total_nav_return != null ? `${r.total_nav_return.toFixed(2)}%` : '—'}
                    </TableCell>
                    <TableCell align="right">
                      {r.excess_return != null ? `${r.excess_return.toFixed(2)}pp` : '—'}
                    </TableCell>
                    <TableCell align="right">
                      {r.max_drawdown != null ? `${r.max_drawdown.toFixed(2)}pp` : '—'}
                    </TableCell>
                    <TableCell align="right">
                      {r.avg_effectiveness != null ? `${r.avg_effectiveness.toFixed(1)}` : '—'}
                    </TableCell>
                    <TableCell>
                      {r.ok
                        ? (r.finished_at ?? '—')
                        : <Typography variant="caption" color="error">失败: {r.error}</Typography>}
                    </TableCell>
                  </TableRow>
                ))}
                {batchRows.length === 0 && !batchLoading && (
                  <TableRow>
                    <TableCell colSpan={7} align="center">
                      暂无批量结果 — 开启自动回测（每周日 00:00）或点击"立即全量回测"
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>

      {/* ── 控制栏 ── */}
      <Paper sx={{ p: 2, mb: 3, display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
        <TextField
          select
          label="选择基金"
          value={selectedFundId ?? ''}
          onChange={(e) => setSelectedFundId(Number(e.target.value))}
          sx={{ minWidth: 240 }}
          size="small"
        >
          {funds.map((f) => (
            <MenuItem key={f.id} value={f.id}>
              {f.name} ({f.code})
            </MenuItem>
          ))}
        </TextField>
        <TextField
          label="回测天数"
          type="number"
          value={period}
          onChange={(e) => setPeriod(parseInt(e.target.value) || 365)}
          inputProps={{ min: 30, max: 1500, step: 30 }}
          size="small"
          sx={{ width: 120 }}
        />
        <TextField
          label="评估窗口(天)"
          type="number"
          value={effectivenessWindow}
          onChange={(e) => setEffectivenessWindow(parseInt(e.target.value) || 5)}
          inputProps={{ min: 1, max: 20 }}
          size="small"
          sx={{ width: 130 }}
        />
        <Button
          variant="contained"
          startIcon={loading ? <CircularProgress size={18} color="inherit" /> : <RunIcon />}
          onClick={handleRun}
          disabled={loading || !selectedFundId}
        >
          {loading ? '回测中...' : '运行回测'}
        </Button>
      </Paper>

      {/* ── 统计卡片 ── */}
      {result && (
        <Grid container spacing={2} sx={{ mb: 3 }}>
          {[
            { label: '净值总收益', value: result.total_nav_return },
            { label: '策略总收益', value: result.total_strategy_return },
            { label: '超额收益', value: result.excess_return },
            { label: '最大回撤', value: result.max_drawdown },
            { label: '信号有效性', value: result.avg_effectiveness },
            { label: '信号胜率', value: result.effectiveness_rate },
          ].map((s) => (
            <Grid item xs={6} sm={3} md={2} key={s.label}>
              <Card>
                <CardContent sx={{ textAlign: 'center', py: 1.5 }}>
                  <Typography variant="body2" color="text.secondary">
                    {s.label}
                  </Typography>
                  <Typography variant="h5" sx={{ color: s.value != null ? statColor(s.value) : 'text.disabled', fontWeight: 700 }}>
                    {s.value != null ? `${s.value >= 0 ? '+' : ''}${s.value.toFixed(2)}${s.label.includes('胜') || s.label.includes('有效') ? '%' : '%'}` : '-'}
                  </Typography>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}

      {/* ── 图表 ── */}
      {result && result.points.length > 0 && (
        <Paper sx={{ p: 2 }}>
          <Typography variant="subtitle1" sx={{ mb: 1 }}>
            {result.fund_name}（{result.fund_code}）— 回测 {result.total_days} 天，信号覆盖 {result.signal_count} 天
          </Typography>
          <ReactECharts
            option={buildChartOption(result)}
            style={{ height: 480, width: '100%' }}
            notMerge
          />
        </Paper>
      )}

      {/* ── 信号评分表格 ── */}
      {result && result.points.filter((p) => p.signal_direction === 'buy' || p.signal_direction === 'sell').length > 0 && (
        <Paper sx={{ p: 2, mt: 3 }}>
          <Typography variant="subtitle1" sx={{ mb: 1 }}>
            信号有效性明细（{result.effectiveness_window} 日窗口）
          </Typography>
          <Box sx={{ maxHeight: 360, overflow: 'auto' }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>日期</TableCell>
                  <TableCell>方向</TableCell>
                  <TableCell>强度</TableCell>
                  <TableCell align="right">因子评分</TableCell>
                  <TableCell align="right">有效性评分</TableCell>
                  <TableCell align="center">结果</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {result.points
                  .filter((p) => p.signal_direction === 'buy' || p.signal_direction === 'sell')
                  .map((p) => {
                    const eff = p.signal_effectiveness;
                    const ok = eff != null && eff >= 50;
                    return (
                      <TableRow key={p.date} hover>
                        <TableCell>{p.date}</TableCell>
                        <TableCell>
                          <Chip
                            label={p.signal_direction === 'buy' ? '买入' : p.signal_direction === 'sell' ? '卖出' : '观望'}
                            size="small"
                            color={p.signal_direction === 'buy' ? 'error' : p.signal_direction === 'sell' ? 'success' : 'default'}
                            variant="outlined"
                          />
                        </TableCell>
                        <TableCell>{STRENGTH_LABELS[p.signal_strength || ''] || '-'}</TableCell>
                        <TableCell align="right">{p.weighted_score?.toFixed(2) ?? '-'}</TableCell>
                        <TableCell align="right">{eff != null ? `${eff.toFixed(1)}` : '-'}</TableCell>
                        <TableCell align="center">
                          {eff != null ? (
                            <Chip
                              label={ok ? '正确' : '错误'}
                              size="small"
                              color={ok ? 'success' : 'error'}
                              variant="filled"
                            />
                          ) : '-'}
                        </TableCell>
                      </TableRow>
                    );
                  })}
              </TableBody>
            </Table>
          </Box>
        </Paper>
      )}

      {/* ── 空状态 ── */}
      {!result && !loading && (
        <Paper sx={{ p: 6, textAlign: 'center' }}>
          <Typography color="text.secondary">
            选择一只基金并点击「运行回测」，将历史信号与每日净值对齐分析
          </Typography>
        </Paper>
      )}

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
      >
        <Alert severity={snackbar.severity}>{snackbar.message}</Alert>
      </Snackbar>
    </Box>
  );
};

export default SignalBacktest;
