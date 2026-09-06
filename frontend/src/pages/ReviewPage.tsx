/**
 * 投资复盘页面 — 组合区间收益 vs 沪深300 + 信号命中率
 */

import React, { useState } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Grid,
  TextField,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  CircularProgress,
  Alert,
  Snackbar,
  Chip,
} from '@mui/material';
import {
  Insights as ReviewIcon,
  AutoAwesome as AiIcon,
} from '@mui/icons-material';
import { reviewApi, type ReviewReport } from '../api/review';
import { aiApi } from '../api/ai';

// 本地日期（UTC ISO 串在北京时间 0-8 点会显示昨天）
const localDate = (d: Date) => d.toLocaleDateString('en-CA');
const today = () => localDate(new Date());
const monthAgo = () => localDate(new Date(Date.now() - 30 * 86400000));

const pct = (v?: number | null, digits = 2) =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`;

const growthColor = (v?: number | null) =>
  v == null ? 'inherit' : v > 0 ? '#f44336' : v < 0 ? '#4caf50' : 'inherit';

const ReviewPage: React.FC = () => {
  const [startDate, setStartDate] = useState(monthAgo());
  const [endDate, setEndDate] = useState(today());
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<ReviewReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [aiReading, setAiReading] = useState(false);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({
    open: false, message: '', severity: 'success',
  });

  const runReview = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await reviewApi.run(startDate, endDate);
      setReport(res.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || '复盘失败');
    } finally {
      setLoading(false);
    }
  };

  const askAi = async () => {
    if (!report) return;
    setAiReading(true);
    try {
      const res = await aiApi.chat({
        content: `请用简洁专业的口吻解读以下投资复盘报告，指出关键风险与后续观察点：\n\n${report.summary_md}`,
        context_type: 'pool',
      });
      setSnackbar({ open: true, message: 'AI 解读已生成，请到 AI 对话窗口查看', severity: 'success' });
      void res;
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.message || 'AI 解读失败', severity: 'error' });
    } finally {
      setAiReading(false);
    }
  };

  const ss = report?.signal_stats;

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h5" gutterBottom>投资复盘</Typography>

      {/* ── 运行条件 ── */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Grid container spacing={2} alignItems="center">
            <Grid item>
              <TextField
                label="起始日期" type="date" size="small"
                value={startDate} onChange={(e) => setStartDate(e.target.value)}
                InputLabelProps={{ shrink: true }}
              />
            </Grid>
            <Grid item>
              <TextField
                label="结束日期" type="date" size="small"
                value={endDate} onChange={(e) => setEndDate(e.target.value)}
                InputLabelProps={{ shrink: true }}
              />
            </Grid>
            <Grid item>
              <Button
                variant="contained" startIcon={<ReviewIcon />}
                onClick={runReview} disabled={loading}
              >
                {loading ? '复盘计算中…' : '开始复盘'}
              </Button>
            </Grid>
            <Grid item>
              <Button
                startIcon={aiReading ? <CircularProgress size={16} /> : <AiIcon />}
                onClick={askAi} disabled={!report || aiReading}
              >
                AI 解读
              </Button>
            </Grid>
          </Grid>
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            口径：基金池等权买入持有（期间无调仓假设）；基准为沪深300 官方指数点位。覆盖全部活跃基金，区间最长 2 年。
          </Typography>
        </CardContent>
      </Card>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading && <LinearProgressBox />}

      {/* ── 汇总卡片 ── */}
      {report && (
        <>
          <Grid container spacing={2} sx={{ mb: 3 }}>
            <Grid item xs={12} sm={4}>
              <SummaryCard
                title="组合区间收益"
                value={pct(report.portfolio_growth_pct)}
                color={growthColor(report.portfolio_growth_pct)}
                sub={`${report.fund_count} 只基金（有效 ${report.items.filter((i) => i.growth_pct != null).length}）`}
              />
            </Grid>
            <Grid item xs={12} sm={4}>
              <SummaryCard
                title="沪深300 同期"
                value={pct(report.benchmark_growth_pct)}
                color={growthColor(report.benchmark_growth_pct)}
                sub={report.excess_pct != null
                  ? `${report.excess_pct >= 0 ? '跑赢' : '跑输'} ${Math.abs(report.excess_pct)}pp`
                  : '基准数据缺失'}
              />
            </Grid>
            <Grid item xs={12} sm={4}>
              <SummaryCard
                title="信号命中率"
                value={ss?.hit_rate != null ? `${ss.hit_rate}%` : '—'}
                color="inherit"
                sub={`buy ${ss?.buy_hits ?? 0}/${ss?.buy_total ?? 0} · sell ${ss?.sell_hits ?? 0}/${ss?.sell_total ?? 0}`}
              />
            </Grid>
          </Grid>

          {/* ── 明细表 ── */}
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>基金</TableCell>
                  <TableCell align="right">区间涨跌</TableCell>
                  <TableCell align="right">起点净值</TableCell>
                  <TableCell align="right">终点净值</TableCell>
                  <TableCell align="right">评分变化</TableCell>
                  <TableCell>信号（始→末）</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {report.items.map((it) => (
                  <TableRow key={it.fund_code} hover>
                    <TableCell>
                      {it.fund_name}
                      <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                        {it.fund_code}
                      </Typography>
                    </TableCell>
                    <TableCell align="right" sx={{ color: growthColor(it.growth_pct), fontWeight: 500 }}>
                      {pct(it.growth_pct)}
                    </TableCell>
                    <TableCell align="right">{it.nav_start ?? '—'}</TableCell>
                    <TableCell align="right">{it.nav_end ?? '—'}</TableCell>
                    <TableCell align="right">
                      {it.score_start != null && it.score_end != null
                        ? `${it.score_start} → ${it.score_end}` : '—'}
                    </TableCell>
                    <TableCell>
                      {it.signal_start ? (
                        <Chip size="small" label={`${it.signal_start} → ${it.signal_end ?? '—'}`} variant="outlined" />
                      ) : '—'}
                      {it.error && (
                        <Typography variant="caption" color="error" sx={{ ml: 1 }}>{it.error}</Typography>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>

          {/* ── Markdown 报告原文 ── */}
          <Card sx={{ mt: 3 }}>
            <CardContent>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                复盘报告原文（可直接复制 / 喂给 AI）
              </Typography>
              <Box
                component="pre"
                sx={{ whiteSpace: 'pre-wrap', fontSize: 13, m: 0, fontFamily: 'inherit' }}
              >
                {report.summary_md}
              </Box>
            </CardContent>
          </Card>
        </>
      )}

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
      >
        <Alert severity={snackbar.severity}>{snackbar.message}</Alert>
      </Snackbar>
    </Box>
  );
};

const LinearProgressBox: React.FC = () => (
  <Box sx={{ mb: 2 }}>
    <Alert severity="info">
      正在拉取 {''}
      区间内各基金净值并计算（并发受数据源限流保护，最长约 1 分钟）…
    </Alert>
  </Box>
);

const SummaryCard: React.FC<{ title: string; value: string; color: string; sub: string }> = ({
  title, value, color, sub,
}) => (
  <Card variant="outlined">
    <CardContent>
      <Typography variant="caption" color="text.secondary">{title}</Typography>
      <Typography variant="h5" sx={{ color, fontWeight: 600 }}>{value}</Typography>
      <Typography variant="caption" color="text.secondary">{sub}</Typography>
    </CardContent>
  </Card>
);

export default ReviewPage;
