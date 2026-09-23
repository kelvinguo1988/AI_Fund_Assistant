/**
 * 我的持仓与调仓建议页（P3）
 * Tab1 持仓管理：手动建仓/更新（基金池代码）、CSV 粘贴导入（支付宝/天天兼容）、删除
 * Tab2 调仓建议：纯 Python 引擎四清单 + 组合层约束 + 工单原文，可一键喂 AI 解读
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Snackbar,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  Tab,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import {
  positionApi, rebalanceApi,
  PositionItem, ImportResult, RebalanceData, RebalanceRow,
} from '../api/position';
import { aiApi } from '../api/ai';

interface Snack { open: boolean; message: string; severity: 'success' | 'error' | 'info' }

const signalColor = (s?: string | null) =>
  s === 'buy' ? 'success.main' : s === 'sell' ? 'error.main' : 'text.secondary';

const ListCard: React.FC<{ title: string; color: string; rows: RebalanceRow[]; extra?: (r: RebalanceRow) => React.ReactNode; empty: string }> = ({ title, color, rows, extra, empty }) => (
  <Card sx={{ height: '100%' }}>
    <CardContent>
      <Typography variant="subtitle1" fontWeight={700} sx={{ color, mb: 1 }}>{title}（{rows.length}）</Typography>
      {rows.length === 0 ? (
        <Typography variant="body2" color="text.secondary">{empty}</Typography>
      ) : rows.map((r) => (
        <Box key={r.code} sx={{ mb: 1.5 }}>
          <Typography variant="body2">
            <b>{r.name}</b>({r.code}) 评分 <b>{r.score}</b>
            {' '}权重 {r.weight_pct}%
            {r.theme ? <Chip size="small" label={r.theme} sx={{ ml: 0.5, height: 20 }} /> : null}
          </Typography>
          {extra ? extra(r) : null}
        </Box>
      ))}
    </CardContent>
  </Card>
);

const PositionsPage: React.FC = () => {
  const [tab, setTab] = useState(0);
  const [items, setItems] = useState<PositionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [snack, setSnack] = useState<Snack>({ open: false, message: '', severity: 'info' });

  // 建仓/编辑对话框
  const [dlgOpen, setDlgOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<PositionItem | null>(null);
  const [fCode, setFCode] = useState('');
  const [fShares, setFShares] = useState('');
  const [fCost, setFCost] = useState('');

  // CSV 导入
  const [csvOpen, setCsvOpen] = useState(false);
  const [csvText, setCsvText] = useState('');
  const [csvMode, setCsvMode] = useState<'merge' | 'replace'>('merge');
  const [csvResult, setCsvResult] = useState<ImportResult | null>(null);

  // 调仓建议
  const [win, setWin] = useState(30);
  const [rbLoading, setRbLoading] = useState(false);
  const [rb, setRb] = useState<RebalanceData | null>(null);
  const [rbMd, setRbMd] = useState('');
  const [aiReading, setAiReading] = useState(false);

  const notify = (message: string, severity: Snack['severity']) =>
    setSnack({ open: true, message, severity });

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await positionApi.list();
      setItems(res.data.data?.items ?? []);
    } catch (e: any) {
      notify(e?.message || '持仓加载失败', 'error');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const openCreate = () => {
    setEditTarget(null); setFCode(''); setFShares(''); setFCost(''); setDlgOpen(true);
  };
  const openEdit = (p: PositionItem) => {
    setEditTarget(p); setFCode(p.fund_code); setFShares(String(p.shares));
    setFCost(p.cost_nav != null ? String(p.cost_nav) : ''); setDlgOpen(true);
  };

  const submitDlg = async () => {
    const shares = parseFloat(fShares);
    const cost = fCost.trim() === '' ? null : parseFloat(fCost);
    if (!fCode.trim() || !Number.isFinite(shares) || shares <= 0) {
      notify('请填写基金代码与有效份额', 'error'); return;
    }
    try {
      if (editTarget) {
        await positionApi.update(editTarget.id, shares, cost);
      } else {
        await positionApi.create(fCode.trim(), shares, cost);
      }
      setDlgOpen(false);
      notify(editTarget ? '已更新' : '已建仓', 'success');
      refresh();
    } catch (e: any) {
      notify(e?.response?.data?.detail || e?.message || '保存失败', 'error');
    }
  };

  const removeItem = async (p: PositionItem) => {
    if (!window.confirm(`删除持仓：${p.fund_name}(${p.fund_code})？`)) return;
    try {
      await positionApi.remove(p.id);
      notify('已删除', 'success');
      refresh();
    } catch (e: any) {
      notify(e?.message || '删除失败', 'error');
    }
  };

  const submitCsv = async () => {
    if (!csvText.trim()) { notify('请粘贴 CSV 内容', 'error'); return; }
    try {
      const res = await positionApi.importCsv(csvText, csvMode);
      const d = res.data.data;
      setCsvResult(d ?? null);
      if (d) notify(`导入完成：新增 ${d.imported}，更新 ${d.updated}`, 'success');
      refresh();
    } catch (e: any) {
      notify(e?.response?.data?.detail || e?.message || '导入失败', 'error');
    }
  };

  const runRebalance = async () => {
    setRbLoading(true);
    try {
      const res = await rebalanceApi.run(win);
      setRb(res.data.data as RebalanceData);
      setRbMd((res.data as any).summary_md || '');
    } catch (e: any) {
      notify(e?.message || '调仓引擎运行失败', 'error');
    } finally {
      setRbLoading(false);
    }
  };

  const askAi = async () => {
    if (!rbMd) return;
    setAiReading(true);
    try {
      await aiApi.chat({
        content: `请解读以下调仓工单（卖出/买入/换仓/观望与组合约束），给出执行顺序、换仓代价（费率/赎回到账）与风险提示：\n\n${rbMd}`,
        context_type: 'pool',
      } as any);
      notify('AI 解读已生成，请到 AI 对话窗口查看', 'success');
    } catch (e: any) {
      notify(e?.message || 'AI 解读失败', 'error');
    } finally {
      setAiReading(false);
    }
  };

  const reasons = (r: RebalanceRow) => (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, ml: 1 }}>
      {(r.reasons || (r.reason ? [r.reason] : [])).map((t: string) => (
        <Chip key={t} size="small" variant="outlined" label={t} sx={{ height: 20, fontSize: 11 }} />
      ))}
    </Box>
  );

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h5" gutterBottom>我的持仓与调仓</Typography>
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label={`持仓管理${items.length ? `（${items.length}）` : ''}`} />
        <Tab label="调仓建议" />
      </Tabs>

      {tab === 0 && (
        <Card>
          <CardContent>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              手动录入或从支付宝/天天基金 App 导出的 CSV 粘贴导入（仅本地存储，不接入第三方账户）。
              未录入持仓时，「调仓建议」按基金池等权近似。代码须在基金池中。
            </Typography>
            <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
              <Button variant="contained" size="small" onClick={openCreate}>建仓 / 录入</Button>
              <Button variant="outlined" size="small" onClick={() => { setCsvResult(null); setCsvOpen(true); }}>CSV 导入</Button>
              <Button size="small" onClick={refresh}>刷新</Button>
            </Box>
            {loading ? <CircularProgress size={22} /> : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      {['代码', '名称', '份额', '成本价', '最新评分', '信号', '来源', '更新时间', '操作'].map((h) => (
                        <TableCell key={h} sx={{ whiteSpace: 'nowrap' }}>{h}</TableCell>
                      ))}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {items.length === 0 && (
                      <TableRow><TableCell colSpan={9}><Typography variant="body2" color="text.secondary">暂无持仓，先「建仓 / 录入」或「CSV 导入」</Typography></TableCell></TableRow>
                    )}
                    {items.map((p) => (
                      <TableRow key={p.id} hover>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{p.fund_code}</TableCell>
                        <TableCell sx={{ minWidth: 140 }}>{p.fund_name}</TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{p.shares.toLocaleString()}</TableCell>
                        <TableCell>{p.cost_nav ?? '—'}</TableCell>
                        <TableCell>{p.latest_score ?? '—'}</TableCell>
                        <TableCell sx={{ color: signalColor(p.latest_signal), whiteSpace: 'nowrap' }}>{p.latest_signal ?? '—'}</TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{p.source === 'import' ? '导入' : '手动'}</TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{p.updated_at?.slice(0, 16).replace('T', ' ') ?? '—'}</TableCell>
                        <TableCell>
                          <IconButton size="small" onClick={() => openEdit(p)}><EditOutlinedIcon fontSize="small" /></IconButton>
                          <IconButton size="small" color="error" onClick={() => removeItem(p)}><DeleteOutlineIcon fontSize="small" /></IconButton>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </CardContent>
        </Card>
      )}

      {tab === 1 && (
        <>
          <Card sx={{ mb: 2 }}>
            <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
              <Typography variant="body2" color="text.secondary">
                四清单口径与信号链路一致（动态阈值 / 场外申购否决 / 翻转只比较相邻非 hold 信号）
              </Typography>
              <TextField select label="信号窗口" size="small" value={win} onChange={(e) => setWin(Number(e.target.value))} sx={{ width: 120 }}>
                {[7, 14, 30, 60, 120].map((d) => <MenuItem key={d} value={d}>{d} 天</MenuItem>)}
              </TextField>
              <Button variant="contained" onClick={runRebalance} disabled={rbLoading}>
                {rbLoading ? <CircularProgress size={18} /> : '生成调仓建议'}
              </Button>
              {rb && <Button variant="outlined" size="small" disabled={aiReading} onClick={askAi}>
                {aiReading ? 'AI 解读中…' : 'AI 解读工单'}
              </Button>}
              {rb && (
                <Button size="small" startIcon={<ContentCopyIcon />} onClick={() => {
                  navigator.clipboard.writeText(rbMd).then(() => notify('工单已复制', 'success'));
                }}>复制工单</Button>
              )}
            </CardContent>
          </Card>

          {rb && (
            <>
              {rb.holdings_mode !== 'positions' && (
                <Alert severity="info" sx={{ mb: 2 }}>
                  未检测到真实持仓，本次为「基金池等权近似」：卖出/观望针对全池候选，权重仅示意。
                </Alert>
              )}
              {rb.caveats.map((c) => <Alert key={c} severity="warning" sx={{ mb: 1 }}>{c}</Alert>)}
              <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2, mb: 2 }}>
                <ListCard title="减仓 / 卖出候选" color="error.main" rows={rb.sells} empty="无低分持仓候选" extra={reasons} />
                <ListCard title="买入候选" color="success.main" rows={rb.buys} empty="无高分可申购标的"
                  extra={(r) => (
                    <Box sx={{ ml: 1 }}>
                      <Chip size="small" label={`申购：${r.otc_status || '未知'}`} sx={{ height: 20, mr: 0.5 }}
                        color={String(r.otc_status).includes('开放') ? 'success' : 'default'} variant="outlined" />
                      {r.overlap_note ? <Chip size="small" color="warning" variant="outlined" sx={{ height: 20 }} label={r.overlap_note} /> : null}
                    </Box>
                  )} />
                <ListCard title="观望 / 待确认" color="warning.main" rows={rb.watch} empty="无边界带或翻转高发标的"
                  extra={(r) => (
                    <Box sx={{ ml: 1 }}>
                      <Typography variant="caption" color="text.secondary">{r.reason}（建议确认 {r.confirm_days} 日）</Typography>
                    </Box>
                  )} />
                <ListCard title="买入被否决（不可申购）" color="text.secondary" rows={rb.buy_blocked} empty="无被申购状态否决的高分标的"
                  extra={(r) => <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>{r.reason}</Typography>} />
              </Box>

              {rb.swaps.length > 0 && (
                <Card sx={{ mb: 2 }}>
                  <CardContent>
                    <Typography variant="subtitle1" fontWeight={700} gutterBottom>同赛道换仓配对（按分差）</Typography>
                    <Table size="small">
                      <TableHead><TableRow><TableCell>卖出</TableCell><TableCell>买入去向</TableCell><TableCell>赛道</TableCell><TableCell>分差</TableCell></TableRow></TableHead>
                      <TableBody>
                        {rb.swaps.map((s, i) => (
                          <TableRow key={i}>
                            <TableCell>{s.sell_name}({s.sell_code})</TableCell>
                            <TableCell>{s.buy_name}({s.buy_code})</TableCell>
                            <TableCell>{s.theme}</TableCell>
                            <TableCell>{s.score_gap}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              )}

              <Card sx={{ mb: 2 }}>
                <CardContent>
                  <Typography variant="subtitle1" fontWeight={700} gutterBottom>组合层约束</Typography>
                  {(rb.constraints.theme_concentration || []).map((t) => (
                    <Alert key={t.theme} severity="warning" sx={{ mb: 0.5 }}>
                      赛道集中度：「{t.theme}」权重 {t.weight_pct}%（≥30% 触发）
                    </Alert>
                  ))}
                  <Typography variant="body2">QDII 权重：{rb.constraints.qdii_weight_pct}%</Typography>
                  {(rb.constraints.twin_pairs || []).map((p) => (
                    <Typography key={`${p.a_code}${p.b_code}`} variant="body2" color="error.main">
                      重仓双胞胎：{p.a_name} × {p.b_name} 前十大重合 {p.common} 只
                    </Typography>
                  ))}
                  {rb.holdings.length > 0 && rb.holdings.length <= 30 && (
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 1 }}>
                      {rb.holdings.map((h) => (
                        <Tooltip key={h.code} title={`评分 ${h.score ?? '—'} / ${h.direction ?? '—'}`}>
                          <Chip size="small" variant="outlined" label={`${h.name} ${h.weight_pct}%`} sx={{ height: 22 }} />
                        </Tooltip>
                      ))}
                    </Box>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardContent>
                  <Typography variant="subtitle2" color="text.secondary" gutterBottom>调仓工单原文（可复制 / 喂 AI）</Typography>
                  <Box component="pre" sx={{ whiteSpace: 'pre-wrap', fontSize: 13, m: 0, fontFamily: 'inherit' }}>{rbMd}</Box>
                </CardContent>
              </Card>
            </>
          )}
        </>
      )}

      {/* 建仓/编辑 */}
      <Dialog open={dlgOpen} onClose={() => setDlgOpen(false)} maxWidth="xs" fullScreen={false}>
        <DialogTitle>{editTarget ? `编辑持仓：${editTarget.fund_name}` : '建仓 / 录入'}</DialogTitle>
        <DialogContent>
          <TextField autoFocus fullWidth margin="dense" label="基金代码（须在基金池）" value={fCode}
            onChange={(e) => setFCode(e.target.value)} disabled={!!editTarget} />
          <TextField fullWidth margin="dense" label="持有份额" value={fShares} onChange={(e) => setFShares(e.target.value)} />
          <TextField fullWidth margin="dense" label="持仓成本价（可空=等权）" value={fCost} onChange={(e) => setFCost(e.target.value)} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDlgOpen(false)}>取消</Button>
          <Button variant="contained" onClick={submitDlg}>保存</Button>
        </DialogActions>
      </Dialog>

      {/* CSV 导入 */}
      <Dialog open={csvOpen} onClose={() => setCsvOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>CSV 粘贴导入</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            直接粘贴支付宝/天天基金等导出内容；自动按表头别名识别「基金代码/持有份额/持仓成本价」列，
            无表头按「代码,份额[,成本价]」列序。池外代码逐行跳过并提示。
          </Typography>
          <ToggleButtonGroup size="small" exclusive value={csvMode} onChange={(_, v) => v && setCsvMode(v)} sx={{ mb: 1 }}>
            <ToggleButton value="merge">合并（按代码覆盖）</ToggleButton>
            <ToggleButton value="replace">全量替换（先清空）</ToggleButton>
          </ToggleButtonGroup>
          <TextField fullWidth multiline minRows={6} value={csvText} onChange={(e) => setCsvText(e.target.value)}
            placeholder={'基金代码,持有份额,持仓成本价\n004011,1200.5,1.2345'} />
          {csvResult && (
            <Alert severity={csvResult.errors.length ? 'warning' : 'success'} sx={{ mt: 1 }}>
              新增 {csvResult.imported}、更新 {csvResult.updated}、跳过 {csvResult.skipped}
              {csvResult.errors.length > 0 && (
                <Box sx={{ maxHeight: 120, overflow: 'auto' }}>
                  {csvResult.errors.map((e) => <Typography key={e} variant="caption" display="block">{e}</Typography>)}
                </Box>
              )}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCsvOpen(false)}>关闭</Button>
          <Button variant="contained" onClick={submitCsv}>导入</Button>
        </DialogActions>
      </Dialog>

      <Snackbar open={snack.open} autoHideDuration={4000} onClose={() => setSnack((s) => ({ ...s, open: false }))}>
        <Alert
          severity={snack.severity}
          variant="filled"
          sx={{ width: '100%' }}
          onClose={() => setSnack((s) => ({ ...s, open: false }))}
        >
          {snack.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default PositionsPage;
