/**
 * 投资复盘页面 — 组合区间收益 vs 含息基准 + 信号同向率（绝对/超额双口径）
 *
 * 口径头三行（净值/基准/计息）由后端 caliber_service 生成并随报告返回（Q11），
 * 前端只负责显示；未运行复盘时用 GET /caliber 的当前生效值占位，避免注释骗人。
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
  Collapse,
  Switch,
  FormControlLabel,
} from '@mui/material';
import {
  Insights as ReviewIcon,
  AutoAwesome as AiIcon,
  Tune as CaliberIcon,
} from '@mui/icons-material';
import {
  reviewApi, caliberApi,
  type ReviewReport, type CaliberState,
} from '../api/review';
import { compareApi, type CompareReport, type FundCompareItem } from '../api/compare';
import { fundApi, overlapApi, type HoldingOverlap } from '../api/fund';
import {
  Tab as MuiTab,
  Tabs as MuiTabs,
} from '@mui/material';
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

  // ── 收益口径（Q11）：当前生效值 + 回滚开关 ──
  const [caliber, setCaliber] = useState<CaliberState | null>(null);
  const [caliberOpen, setCaliberOpen] = useState(false);
  const [caliberDraft, setCaliberDraft] = useState({ nav_adjusted: true, bench_div_yield_pct: 2.7 });
  const [caliberSaving, setCaliberSaving] = useState(false);

  useEffect(() => {
    caliberApi.get()
      .then((r) => {
        const c = r.data;
        if (!c) return;
        setCaliber(c);
        setCaliberDraft({
          nav_adjusted: c.nav_adjusted,
          bench_div_yield_pct: c.bench_div_yield_pct,
        });
      })
      .catch(() => { /* 静默：口径头退化为报告自带文案 */ });
  }, []);

  const saveCaliber = async () => {
    setCaliberSaving(true);
    try {
      const res = await caliberApi.update({
        review_nav_adjusted: caliberDraft.nav_adjusted,
        benchmark_dividend_yield_pct: Number(caliberDraft.bench_div_yield_pct),
      });
      if (res.data) {
        setCaliber(res.data);
        setCaliberDraft({
          nav_adjusted: res.data.nav_adjusted,
          bench_div_yield_pct: res.data.bench_div_yield_pct,
        });
      }
      setSnackbar({
        open: true,
        message: `口径已更新（净值${res.data?.nav_adjusted ? '复权' : '未复权'} / 股息 ${res.data?.bench_div_yield_pct ?? 0}%/年）：改口径后历史报告与新报告不可比`,
        severity: 'success',
      });
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.message || '口径保存失败', severity: 'error' });
    } finally {
      setCaliberSaving(false);
    }
  };

  /** 三行口径头：优先用报告自带的（与报告数字同源），否则按当前生效值组一句占位 */
  const caliberLines = (cashLine = '满仓假设，不涉及现金利息', extra = ''): string[] => {
    const nav = caliber?.nav_adjusted;
    const div = caliber?.bench_div_yield_pct ?? 0;
    if (nav == null) return [];
    return [
      `净值口径：${nav ? '场外基金分红复权、场内 ETF 前复权' : '单位净值（未复权，除息日会记成下跌）'}`
        + (extra ? `；${extra}` : ''),
      `基准口径：沪深300 价格指数${div > 0 ? ` + 股息 ${div}%/年（按区间交易日折算）` : '（不含股息）'}`,
      `计息口径：${cashLine}`,
    ];
  };

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
      setSnackbar({ open: true, message: err?.displayMessage || err?.message || 'PK 失败', severity: 'error' });
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
      await aiApi.chat({
        content: `请用简洁专业的口吻解读以下投资复盘报告，指出关键风险与后续观察点：\n\n${report.summary_md}`,
        context_type: 'pool',
      });
      setSnackbar({ open: true, message: 'AI 解读已生成，请到 AI 对话窗口查看', severity: 'success' });
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
          <Box sx={{ mt: 1 }}>
            <Typography variant="caption" color="text.secondary" component="div">
              {(() => {
                const raw = report?.caliber?.lines;
                const lines = raw && raw.length
                  ? raw.map((l) => l.replace(/^>\s*/, ''))
                  : caliberLines('满仓假设，不涉及现金利息', '组合按基金池等权买入持有（期间无调仓假设）');
                return lines.length
                  ? `${lines.join('　·　')}　覆盖全部活跃基金，区间最长 2 年。`
                  : '口径：基金池等权买入持有（期间无调仓假设）。覆盖全部活跃基金，区间最长 2 年。';
              })()}
            </Typography>
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={caliberOpen}
                  onChange={(e) => setCaliberOpen(e.target.checked)}
                />
              }
              label={
                <Typography variant="caption" color="text.secondary">
                  口径设置（净值复权 / 基准股息率）
                </Typography>
              }
              sx={{ mt: 0.25 }}
            />
            <Collapse in={caliberOpen}>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', pl: 1 }}>
                <FormControlLabel
                  control={
                    <Switch
                      size="small"
                      checked={caliberDraft.nav_adjusted}
                      onChange={(e) => setCaliberDraft((d) => ({ ...d, nav_adjusted: e.target.checked }))}
                    />
                  }
                  label={
                    <Typography variant="caption">
                      场外净值分红复权（关掉回到裸单位净值旧口径）
                    </Typography>
                  }
                />
                <TextField
                  label="基准股息率 %/年"
                  type="number" size="small" sx={{ width: 150 }}
                  value={caliberDraft.bench_div_yield_pct}
                  onChange={(e) => setCaliberDraft((d) => ({
                    ...d,
                    bench_div_yield_pct: Math.max(0, Math.min(10, Number(e.target.value) || 0)),
                  }))}
                  inputProps={{ min: 0, max: 10, step: 0.1 }}
                  helperText={caliber ? `上限 ${caliber.bench_div_yield_max}，0 = 纯价格指数` : ''}
                />
                <Button
                  size="small" variant="outlined" startIcon={<CaliberIcon />}
                  onClick={saveCaliber} disabled={caliberSaving || !caliber}
                >
                  {caliberSaving ? '保存中…' : '保存口径'}
                </Button>
                <Typography variant="caption" color="warning.main">
                  只影响此后生成的复盘/PK/建议回填，历史报告不可比
                </Typography>
              </Box>
            </Collapse>
          </Box>
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
                title={(() => {
                  const div = report.caliber?.bench_div_yield_pct ?? caliber?.bench_div_yield_pct ?? 0;
                  return div > 0 ? `基准同期（沪深300+股息 ${div}%/年）` : '基准同期（沪深300 价格指数）';
                })()}
                value={pct(report.benchmark_growth_pct)}
                color={growthColor(report.benchmark_growth_pct)}
                sub={report.excess_pct != null
                  ? `${report.excess_pct >= 0 ? '跑赢' : '跑输'} ${Math.abs(report.excess_pct)}pp`
                  : '基准数据缺失'}
              />
            </Grid>
            <Grid item xs={12} sm={4}>
              {(() => {
                const ex = ss?.excess;
                if (ex?.hit_rate != null) {
                  return (
                    <SummaryCard
                      title="信号同向率（超额口径）"
                      value={`${ex.hit_rate}%`}
                      color="inherit"
                      sub={`跑赢基准 buy ${ex.buy_hits ?? 0}/${ex.buy_total ?? 0} · sell ${ex.sell_hits ?? 0}/${ex.sell_total ?? 0}｜绝对口径 ${ss?.hit_rate ?? '—'}%`}
                    />
                  );
                }
                return (
                  <SummaryCard
                    title="信号命中率（绝对口径）"
                    value={ss?.hit_rate != null ? `${ss.hit_rate}%` : '—'}
                    color="inherit"
                    sub={`buy ${ss?.buy_hits ?? 0}/${ss?.buy_total ?? 0} · sell ${ss?.sell_hits ?? 0}/${ss?.sell_total ?? 0}（只量市场方向 beta，选基能力看超额口径）`}
                  />
                );
              })()}
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
            基准归因 Beta/Alpha/信息比率（默认沪深300+股息），规模变化倍数与机构占比（季报）。
            无风险利率 2%，仅供参考。
          </Typography>
          {(() => {
            const raw = pkReport?.caliber?.lines;
            const lines = raw && raw.length
              ? raw.map((l) => l.replace(/^>\s*/, ''))
              : caliberLines('夏普/Alpha 扣减无风险利率 2%/年，净值不另计利息');
            if (!lines.length) return null;
            return (
              <Typography variant="caption" color="text.secondary" sx={{ mb: 1.5, display: 'block' }}>
                {lines.join('　·　')}
              </Typography>
            );
          })()}
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
