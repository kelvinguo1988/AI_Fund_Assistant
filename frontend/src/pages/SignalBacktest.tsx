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
import {
  backtestApi,
  backtestBatchApi,
  type AutoBacktestConfig,
  type BacktestBatchItem,
  type BacktestMeasurementConfig,
} from '../api/backtest';
import type { FundOut, BacktestSummary } from '../types';
import { STRENGTH_LABELS, GROWTH_UP, GROWTH_DOWN } from '../utils/format';
import ConfirmDialog from '../components/ConfirmDialog';

/** 未平仓现金按 0% 计（后端 Q6 口径），文案必须写明"不计息" */
const FEE_NOTE = '调仓成本：|Δ仓位| × 费率；未平仓现金部分按 0% 计（不计息）';


const SignalBacktest: React.FC = () => {
  const [funds, setFunds] = useState<FundOut[]>([]);
  const [selectedFundId, setSelectedFundId] = useState<number | null>(null);
  const [period, setPeriod] = useState(365);
  const [effectivenessWindow, setEffectivenessWindow] = useState(5);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<BacktestSummary | null>(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' | 'info' });

  // ── 回测调仓费率（后端 system_config 持久化，0 = 不计成本）──
  const [feePct, setFeePct] = useState<number>(0.6);
  const [feeSaving, setFeeSaving] = useState(false);

  // ── 回测度量口径（Q6：仓位延续 + 样本下限，同时是回滚开关）──
  const [measure, setMeasure] = useState<BacktestMeasurementConfig | null>(null);

  const saveMeasurement = async (patch: Partial<BacktestMeasurementConfig>) => {
    try {
      const res = await backtestApi.updateMeasurementConfig(patch);
      if (res.data) setMeasure(res.data);
      setSnackbar({ open: true, message: '回测口径已保存，下一次回测生效', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.displayMessage || '保存回测口径失败', severity: 'error' });
    }
  };

  const saveFee = async (value: number) => {
    setFeeSaving(true);
    try {
      const res = await backtestApi.updateFeeConfig(value);
      const next = res.data?.fee_pct ?? value;
      setFeePct(next);
      setSnackbar({ open: true, message: `调仓费率已保存为 ${next}%`, severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.displayMessage || '保存费率失败', severity: 'error' });
    } finally {
      setFeeSaving(false);
    }
  };

  // ── 自动全量回测状态 ──
  const [autoCfg, setAutoCfg] = useState<AutoBacktestConfig | null>(null);
  const [batchRows, setBatchRows] = useState<BacktestBatchItem[]>([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);

  const clearBatchResults = async () => {
    setClearOpen(false);
    try {
      await backtestBatchApi.clearResults();
      loadBatch();
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.message || '清空失败', severity: 'error' });
    }
  };

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

  // 一次性加载：自动回测配置、调仓费率、已有批量结果都只在挂载时取一次
  // （2026-10-01 审查 P2：过去它们和轮询挂在同一个 [batchRunning] effect 上，
  //  每轮"运行中→完成"状态翻转就把三项整体重跑一遍）
  useEffect(() => {
    backtestBatchApi.getConfig()
      .then((r) => setAutoCfg(r.data))
      .catch(() => { /* 配置加载失败保持 null */ });
    backtestApi.getFeeConfig()
      .then((r) => setFeePct(r.data?.fee_pct ?? feePct))
      .catch(() => { /* 读不到配置则沿用默认展示值 */ });
    backtestApi.getMeasurementConfig()
      .then((r) => {
        if (r.data) {
          const { carry_position: carry, min_signals: minSignals, min_coverage_pct: minCoverage } = r.data;
          setMeasure({ carry_position: carry, min_signals: minSignals, min_coverage_pct: minCoverage });
        }
      })
      .catch(() => { /* 口径读不到时不显示编辑器，回测仍按后端默认跑 */ });
    loadBatch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* 批量进度轮询：只在一轮全量回测运行期间开 60s 轮询，空闲时不留定时器
     （过去空闲也每 60s 拉一次全表并闪一次加载圈）。
     终止条件以**服务端** running 为准（2026-09-29 审查 P0：旧实现只在
     triggerBatch 成功时把 batchRunning 置 true 且永不复位 → 按钮永久
     disabled、轮询无终止条件，后端早已跑完前端也不知道）；
     连续 3 次状态请求失败才兜底复位，避免后端不可达时空转。 */
  useEffect(() => {
    if (!batchRunning) return;
    let statusFailStreak = 0;
    const t = setInterval(async () => {
      loadBatch();
      try {
        const s = await backtestBatchApi.status();
        statusFailStreak = 0;
        // 端点是 ApiResponse 信封，running 在 data 里（读 s.running 恒 undefined，
        // 复位只剩"连续 3 次失败"兜底 → 按钮永远禁用）
        if (s?.data?.running === false) {
          setBatchRunning(false);
          loadBatch();   // 收尾再拉一次，表格里不留 60s 前的中间态
        }
      } catch {
        statusFailStreak += 1;
        if (statusFailStreak >= 3) setBatchRunning(false);
      }
    }, 60_000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    // 静态半仓基线（Q6-B）：恒 50% 敞口、零调仓 —— 与策略差才是信号能力
    const staticHalfReturns = data.points.map((p) => p.baseline_static_half ?? null);

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
          html += `净值收益(=满仓持有): ${pt.nav_return.toFixed(2)}%<br/>`;
          html += `策略收益: ${pt.strategy_return.toFixed(2)}%<br/>`;
          if (pt.baseline_static_half != null) {
            html += `静态半仓基准: ${pt.baseline_static_half.toFixed(2)}%<br/>`;
          }
          if (pt.position_applied != null) {
            html += `当日仓位: ${(pt.position_applied * 100).toFixed(0)}%<br/>`;
          }
          if (pt.signal_direction) {
            html += `信号: ${STRENGTH_LABELS[pt.signal_strength || ''] || pt.signal_direction}<br/>`;
            html += `评分(当日池内相对): ${pt.weighted_score?.toFixed(2) ?? '-'}<br/>`;
            if (pt.signal_effectiveness != null) {
              html += `有效性: ${pt.signal_effectiveness.toFixed(1)} 分`;
            }
          } else {
            html += '信号: 无（仓位延续上一信号）';
          }
          return html;
        },
      },
      legend: {
        data: ['净值累计收益(满仓持有)', '策略累计收益', '静态半仓基准', '买入信号', '卖出信号'],
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
          name: '净值累计收益(满仓持有)',
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
          name: '静态半仓基准',
          type: 'line',
          data: staticHalfReturns,
          smooth: true,
          lineStyle: { width: 1.5, type: 'dashed' },
          itemStyle: { color: '#9E9E9E' },
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
      const msg = err?.displayMessage || err?.message || '触发失败';
      // 409 = 服务端已有一轮在跑（调度或他人触发），保持"运行中"并继续轮询
      if (err?.response?.status === 409) {
        setSnackbar({ open: true, message: msg, severity: 'info' });
        return;
      }
      setBatchRunning(false);
      setSnackbar({ open: true, message: msg, severity: 'error' });
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
              <Button size="small" startIcon={<DeleteIcon />} onClick={() => setClearOpen(true)}>
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
                  <TableCell align="right">超额·对半仓</TableCell>
                  <TableCell align="right">超额·对满仓</TableCell>
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
                      {r.low_sample && (
                        <Tooltip title={r.caveat || '样本不足，仅过程展示'}>
                          <Chip
                            label="样本不足"
                            size="small"
                            color="warning"
                            variant="outlined"
                            sx={{ ml: 1, verticalAlign: 'middle' }}
                          />
                        </Tooltip>
                      )}
                    </TableCell>
                    <TableCell align="right" sx={{ color: (r.total_strategy_return ?? 0) >= 0 ? GROWTH_UP : GROWTH_DOWN }}>
                      {r.total_strategy_return != null ? `${r.total_strategy_return.toFixed(2)}%` : '—'}
                    </TableCell>
                    <TableCell align="right">
                      {r.total_nav_return != null ? `${r.total_nav_return.toFixed(2)}%` : '—'}
                    </TableCell>
                    <TableCell align="right" sx={{ color: (r.excess_vs_static_half ?? 0) >= 0 ? GROWTH_UP : GROWTH_DOWN }}>
                      {r.excess_vs_static_half != null ? `${r.excess_vs_static_half.toFixed(2)}pp` : '—'}
                    </TableCell>
                    <TableCell align="right">
                      {r.excess_return != null ? `${r.excess_return.toFixed(2)}pp` : '—'}
                    </TableCell>
                    <TableCell align="right">
                      {r.max_drawdown != null ? `${r.max_drawdown.toFixed(2)}%` : '—'}
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
                    <TableCell colSpan={8} align="center">
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
        <Tooltip title={`每次调仓按 |Δ仓位| × 费率扣减收益（申购+赎回综合口径）；0 表示不计成本。失焦即保存。${FEE_NOTE}`}>
          <TextField
            label="调仓费率(%)"
            type="number"
            value={feePct}
            onChange={(e) => setFeePct(Math.max(0, parseFloat(e.target.value) || 0))}
            onBlur={() => { void saveFee(feePct); }}
            inputProps={{ min: 0, max: 5, step: 0.1 }}
            size="small"
            disabled={feeSaving}
            sx={{ width: 130 }}
          />
        </Tooltip>
        <Button
          variant="contained"
          startIcon={loading ? <CircularProgress size={18} color="inherit" /> : <RunIcon />}
          onClick={handleRun}
          disabled={loading || !selectedFundId}
        >
          {loading ? '回测中...' : '运行回测'}
        </Button>
      </Paper>

      {/* ── 回测口径（Q6：仓位延续 + 样本下限，也是回滚开关）── */}
      <Paper sx={{ p: 2, mb: 3, display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
        <Typography variant="subtitle2" color="text.secondary">回测口径</Typography>
        {measure ? (
          <>
            <Tooltip title="开：无信号日沿用最近一次信号的仓位（漏跑一天分析不再被动砍半仓 + 白扣换仓费）；关：回落到 50% 旧口径">
              <FormControlLabel
                control={
                  <Switch
                    checked={measure.carry_position}
                    onChange={(e) => {
                      setMeasure({ ...measure, carry_position: e.target.checked });
                      void saveMeasurement({ carry_position: e.target.checked });
                    }}
                  />
                }
                label="仓位延续"
              />
            </Tooltip>
            <Tooltip title="非 hold 信号少于此数 → 只出警告不给结论；0 = 不拦（回到旧行为，无论样本多少都输出结论）">
              <TextField
                label="最少信号数"
                type="number"
                size="small"
                sx={{ width: 130 }}
                value={measure.min_signals}
                onChange={(e) => setMeasure({ ...measure, min_signals: parseInt(e.target.value) || 0 })}
                onBlur={() => measure && void saveMeasurement({ min_signals: measure.min_signals })}
                inputProps={{ min: 0, max: 250, step: 1 }}
              />
            </Tooltip>
            <Tooltip title="有信号的交易日占回测总交易日的比例下限（%）；0 = 不拦">
              <TextField
                label="最低覆盖度(%)"
                type="number"
                size="small"
                sx={{ width: 140 }}
                value={measure.min_coverage_pct}
                onChange={(e) => setMeasure({ ...measure, min_coverage_pct: parseFloat(e.target.value) || 0 })}
                onBlur={() => measure && void saveMeasurement({ min_coverage_pct: measure.min_coverage_pct })}
                inputProps={{ min: 0, max: 100, step: 5 }}
              />
            </Tooltip>
          </>
        ) : (
          <Typography variant="body2" color="text.secondary">
            口径配置读取中/不可用 — 后端按默认执行（仓位延续=开，非 hold 信号≥8 且覆盖≥30% 才出结论）
          </Typography>
        )}
        <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>
          {FEE_NOTE}
        </Typography>
      </Paper>

      {/* ── 样本不足警告（Q6-C：只出警告不出结论）── */}
      {result?.low_sample && result.caveat && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {result.caveat}
        </Alert>
      )}

      {/* ── 统计卡片 ── */}
      {result && (
        <Grid container spacing={2} sx={{ mb: 3 }}>
          {[
            { label: '净值总收益(满仓持有)', value: result.total_nav_return },
            { label: '策略总收益', value: result.total_strategy_return },
            { label: '超额·对静态半仓', value: result.excess_vs_static_half },
            { label: '超额·对满仓持有', value: result.excess_return },
            { label: '静态半仓基准', value: result.baseline_static_half },
            { label: '最大回撤', value: result.max_drawdown },
            { label: '信号有效性', value: result.avg_effectiveness },
            { label: '信号胜率', value: result.effectiveness_rate },
          ].map((s) => (
            <Grid item xs={6} sm={4} md={3} key={s.label}>
              <Card>
                <CardContent sx={{ textAlign: 'center', py: 1.5 }}>
                  <Typography variant="body2" color="text.secondary">
                    {s.label}
                  </Typography>
                  <Typography variant="h5" sx={{ color: s.value != null ? statColor(s.value) : 'text.disabled', fontWeight: 700 }}>
                    {s.value != null ? `${s.value >= 0 ? '+' : ''}${s.value.toFixed(2)}%` : '-'}
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
          <Typography variant="subtitle1" sx={{ mb: 0.5 }}>
            {result.fund_name}（{result.fund_code}）— 回测 {result.total_days} 个交易日；
            非 hold 信号 {result.signal_count_non_hold ?? 0} 个，有信号日 {result.signal_count} 天
            （覆盖 {((result.signal_coverage_ratio ?? 0) * 100).toFixed(0)}%）
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
            口径：净值=分红复权（场外含分红、ETF 前复权）；基线=满仓买入持有与静态半仓（非沪深300，不涉及股息）；未平仓现金按 0% 计（不计息）。
            仓位延续={result.carry_position ? '开' : '关'}；三条线分别是策略累计收益、净值累计收益(=满仓买入持有)、静态半仓基准（恒 50%
            仓位、不调仓不计费）——判断信号能力看「超额·对静态半仓」。
            {result.coverage_start_date
              ? ` 信号区间自 ${result.coverage_start_date} 起（该基金首次被分析日，${result.coverage_days ?? 0} 个交易日；当日池内 ${
                  result.pool_size_at ?? '未知'
                } 只），回测区间由入池时点决定，存在选择偏差。`
              : ' 该基金在本区间没有任何历史信号。'}
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
                  <TableCell align="right">
                    <Tooltip title="当日截面池内的相对分（池内均值 0），不是绝对收益率；跨期请看净值涨幅，回测里的分数只反映当时那批基金的相对位置">
                      <span>因子评分(池内相对分)</span>
                    </Tooltip>
                  </TableCell>
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

      <ConfirmDialog
        open={clearOpen}
        title="清空批量回测结果"
        message="清空全部批量回测结果？已生成的单基金回测不受影响。"
        confirmLabel="清空"
        confirmColor="error"
        onConfirm={clearBatchResults}
        onCancel={() => setClearOpen(false)}
      />

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
