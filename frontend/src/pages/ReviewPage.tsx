/**
 * 投资复盘页面 — 组合区间收益 vs 沪深300 + 信号命中率
 */

import React, { useEffect, useState } from 'react';
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
import { compareApi, type CompareReport, type FundCompareItem } from '../api/compare';
import { fundApi, overlapApi, type HoldingOverlap } from '../api/fund';
import {
  Tab as MuiTab,
  Tabs as MuiTabs,
} from '@mui/material';
import { reviewApi as _unusedGuard } from '../api/review';
import { aiApi } from '../api/ai';
import { pct, growthColor } from '../utils/format';

// 本地日期（UTC ISO 串在北京时间 0-8 点会显示昨天）
const localDate = (d: Date) => d.toLocaleDateString('en-CA');
const today = () => localDate(new Date());
const monthAgo = () => localDate(new Date(Date.now() - 30 * 86400000));

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

  // ── 基金 PK 状态 ──
  const [pageTab, setPageTab] = useState(0);
  const [allFunds, setAllFunds] = useState<{ id: number; code: string; name: string }[]>([]);
  const [pkSelected, setPkSelected] = useState<number[]>([]);
  const [pkYears, setPkYears] = useState(2);
  const [pkLoading, setPkLoading] = useState(false);
  const [pkReport, setPkReport] = useState<CompareReport | null>(null);
  const [overlap, setOverlap] = useState<HoldingOverlap | null>(null);

  useEffect(() => {
    fundApi.list('active')
      .then((r) => setAllFunds((r.data || []).map((f) => ({ id: f.id, code: f.code, name: f.name }))))
      .catch(() => { /* 静默 */ });
  }, []);

  const togglePk = (id: number) => {
    setPkSelected((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : prev.length >= 10 ? prev : [...prev, id];
      // 重叠度跟随选中基金（≥2 只时）
      if (next.length >= 2) {
        overlapApi.get(next).then((r) => setOverlap(r.data || null)).catch(() => setOverlap(null));
      } else {
        setOverlap(null);
      }
      return next;
    });
  };

  const runPk = async () => {
    if (pkSelected.length < 2) {
      setSnackbar({ open: true, message: '请至少选择 2 只基金', severity: 'error' });
      return;
    }
    setPkLoading(true);
    try {
      const res = await compareApi.run(pkSelected, pkYears);
      setPkReport(res.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'PK 失败');
    } finally {
      setPkLoading(false);
    }
  };

  const askAiPk = async () => {
    if (!pkReport) return;
    setAiReading(true);
    try {
      await aiApi.chat({
        content: `请解读以下基金 PK 对比报告（Beta/Alpha/IR/规模/机构视角，指出谁有真实选股能力与规模风险）：\n\n${pkReport.summary_md}`,
        context_type: 'pool',
      });
      setSnackbar({ open: true, message: 'AI 解读已生成，请到 AI 对话窗口查看', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.message || 'AI 解读失败', severity: 'error' });
    } finally {
      setAiReading(false);
    }
  };

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
      <MuiTabs value={pageTab} onChange={(_, v) => setPageTab(v)} sx={{ mb: 2 }}>
        <MuiTab label="组合复盘" />
        <MuiTab label={`基金 PK${pkSelected.length ? `（已选 ${pkSelected.length}）` : ''}`} />
      </MuiTabs>

{pageTab === 0 && (
      <>
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

      </>
      )}

      {pageTab === 1 && (
      <>
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            选 2~10 只基金对比：双窗口（近 N 年 / 成立以来）年化收益、最大回撤、夏普，
            基准归因 Beta/Alpha/信息比率（默认沪深300），规模变化倍数与机构占比（季报）。
            无风险利率 2%，仅供参考。
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mb: 2 }}>
            {allFunds.map((f) => (
              <Chip
                key={f.id}
                label={`${f.name}(${f.code})`}
                size="small"
                clickable
                color={pkSelected.includes(f.id) ? 'primary' : 'default'}
                onClick={() => togglePk(f.id)}
                sx={{ mb: 0.5 }}
              />
            ))}
          </Box>
          <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
            <TextField
              label="近期窗口（年）" type="number" size="small" sx={{ width: 140 }}
              value={pkYears}
              onChange={(e) => setPkYears(Math.max(1, Math.min(5, Number(e.target.value) || 2)))}
              inputProps={{ min: 1, max: 5 }}
            />
            <Button variant="contained" onClick={runPk} disabled={pkLoading || pkSelected.length < 2}>
              {pkLoading ? '对比计算中…' : `开始 PK（${pkSelected.length} 只）`}
            </Button>
            {pkReport && (
              <Button startIcon={aiReading ? <CircularProgress size={16} /> : <AiIcon />} onClick={askAiPk} disabled={aiReading}>
                AI 解读
              </Button>
            )}
          </Box>
        </CardContent>
      </Card>

      {pkReport && (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>基金</TableCell>
                <TableCell>窗口</TableCell>
                <TableCell align="right">年化收益</TableCell>
                <TableCell align="right">最大回撤</TableCell>
                <TableCell align="right">夏普</TableCell>
                <TableCell align="right">Beta</TableCell>
                <TableCell align="right">Alpha(年化)</TableCell>
                <TableCell align="right">信息比率</TableCell>
                <TableCell align="right">规模变化</TableCell>
                <TableCell align="right">机构占比</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {pkReport.items.map((it: FundCompareItem) =>
                it.error ? (
                  <TableRow key={it.fund_code}>
                    <TableCell>{it.fund_name}({it.fund_code})</TableCell>
                    <TableCell colSpan={9}>
                      <Typography variant="caption" color="error">失败: {it.error}</Typography>
                    </TableCell>
                  </TableRow>
                ) : (
                  it.windows.map((w, wi) => (
                    <TableRow key={`${it.fund_code}-${w.window_label}`} hover>
                      <TableCell>
                        {wi === 0 ? it.fund_name : ''}
                        {wi === 0 && (
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                            {it.fund_code}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>{w.window_label}</TableCell>
                      <TableCell align="right" sx={{ color: growthColor(w.annual_return_pct), fontWeight: wi === 0 ? 500 : 400 }}>
                        {pct(w.annual_return_pct)}
                      </TableCell>
                      <TableCell align="right" sx={{ color: growthColor(w.max_drawdown_pct) }}>
                        {pct(w.max_drawdown_pct)}
                      </TableCell>
                      <TableCell align="right">{w.sharpe ?? '—'}</TableCell>
                      <TableCell align="right">{w.beta ?? '—'}</TableCell>
                      <TableCell align="right" sx={{ color: growthColor(w.alpha_annual_pct) }}>
                        {pct(w.alpha_annual_pct)}
                      </TableCell>
                      <TableCell align="right">{w.info_ratio ?? '—'}</TableCell>
                      <TableCell align="right">{it.scale_growth != null ? `${it.scale_growth}×` : '—'}</TableCell>
                      <TableCell align="right">{it.institution_pct != null ? `${it.institution_pct.toFixed(1)}%` : '—'}</TableCell>
                    </TableRow>
                  ))
                )
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {overlap && overlap.overlaps.length > 0 && (
        <Card sx={{ mt: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>重仓股重叠度</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              选中 {overlap.funds_count} 只基金的重仓股共同持有排行——只数越多抱团越集中，注意同涨同跌风险。
            </Typography>
            <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 300 }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>股票</TableCell>
                    <TableCell align="right">被持有基金数</TableCell>
                    <TableCell align="right">合计占净值</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {overlap.overlaps.filter((o) => o.funds_count >= 2).map((o) => (
                    <TableRow key={o.stock_code} hover>
                      <TableCell>{o.stock_name}({o.stock_code})</TableCell>
                      <TableCell align="right">{o.funds_count}</TableCell>
                      <TableCell align="right">{o.total_ratio != null ? `${o.total_ratio.toFixed(2)}%` : '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </CardContent>
        </Card>
      )}

      {pkReport && (
        <Card sx={{ mt: 3 }}>
          <CardContent>
            <Typography variant="subtitle2" color="text.secondary" gutterBottom>PK 报告原文</Typography>
            <Box component="pre" sx={{ whiteSpace: 'pre-wrap', fontSize: 13, m: 0, fontFamily: 'inherit' }}>
              {pkReport.summary_md}
            </Box>
          </CardContent>
        </Card>
      )}
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
