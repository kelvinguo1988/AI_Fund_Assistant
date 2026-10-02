/**
 * 我的持仓与调仓建议页（P3）
 * Tab1 持仓管理：手动建仓/更新（基金池代码）、CSV 粘贴导入（支付宝/天天兼容）、删除；
 *                含「首次买入日期」（Q10-C 持有期/赎回费约束的唯一输入，未填显示"持有 —"）
 * Tab2 调仓建议：纯 Python 引擎四清单 + 组合层约束 + 工单原文，可一键喂 AI 解读；
 *                权重按市值（实时估值缓存→成本→份额，逐只标注口径），卖出/换仓附持有期与阶梯赎回费
 *      底部「建议命中率与阈值校准」卡展示自进化闭环的双口径命中数（Q7）
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
  positionApi, rebalanceApi, adviceApi,
  PositionItem, ImportResult, RebalanceData, RebalanceRow,
  AdviceStats, AdviceSide,
} from '../api/position';
import { aiApi } from '../api/ai';
import ConfirmDialog from '../components/ConfirmDialog';

interface Snack { open: boolean; message: string; severity: 'success' | 'error' | 'info' }

const signalColor = (s?: string | null) =>
  s === 'buy' ? 'success.main' : s === 'sell' ? 'error.main' : 'text.secondary';

/** 本地日期的 YYYY-MM-DD（不用 toISOString：UTC 换算会把日期挪走一天） */
const todayIso = () => {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

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

/**
 * 建议自进化闭环卡片（Q7 双口径）
 *
 * 命中率同时给两个数：绝对口径在上涨市里近乎恒真（量的是 beta），超额口径才量得出
 * 选基能力。两者都落库，所以切换口径不需要重跑回填；校准实际使用的那个会标注出来。
 */
const AdviceLearningCard: React.FC<{
  stats: AdviceStats | null;
  loading: boolean;
  backfilling: boolean;
  onRefresh: () => void;
  onModeChange: (mode: string) => void;
  onBackfill: () => void;
}> = ({ stats, loading, backfilling, onRefresh, onModeChange, onBackfill }) => {
  const fmt = (s?: AdviceSide) => {
    if (!s || !s.total) return '—';
    return `${(s.hits / s.total * 100).toFixed(0)}%（${s.hits}/${s.total}）`;
  };
  const rows: { mode: 'excess' | 'abs'; label: string; tip: string }[] = [
    { mode: 'excess', label: '超额口径', tip: '与沪深300 比同区间涨跌：卖出后跑输=命中、买入后跑赢=命中，量的是选基能力' },
    { mode: 'abs', label: '绝对口径', tip: '只看基金自身涨跌：卖出后下跌=命中。上涨市里几乎恒真，量的是市场 beta' },
  ];
  return (
    <Card sx={{ mt: 2 }}>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap', mb: 1 }}>
          <Typography variant="subtitle1" fontWeight={700}>建议命中率与阈值校准</Typography>
          <Typography variant="caption" color="text.secondary">
            工单落库 → 满 30 个净值日后回填 → 按当前口径校准止盈/止损线（每周六 01:00 自动跑）
          </Typography>
          <Box sx={{ flex: 1 }} />
          <Tooltip title="选择阈值校准依据的口径（两组命中率始终同时展示，切换只影响校准与后续回填的 hit 列，不需要重跑历史）">
            <ToggleButtonGroup size="small" exclusive value={stats?.hit_mode ?? 'excess'}
              onChange={(_, v) => v && onModeChange(v)}>
              <ToggleButton value="excess">校准用超额</ToggleButton>
              <ToggleButton value="abs">校准用绝对</ToggleButton>
            </ToggleButtonGroup>
          </Tooltip>
          <Button size="small" onClick={onRefresh} disabled={loading}>
            {loading ? <CircularProgress size={18} /> : '刷新'}
          </Button>
          <Tooltip title="逐只取到期基金的净值，基金之间随机等待 10–30 秒防限流；没有到期样本时不发请求">
            <span>
              <Button size="small" variant="outlined" onClick={onBackfill} disabled={backfilling}>
                {backfilling ? '回填中…' : '立即回填到期建议'}
              </Button>
            </span>
          </Tooltip>
        </Box>

        {!stats || stats.evaluated === 0 ? (
          <Alert severity="info">
            暂无已回填样本。工单来源 = AI 工作台的「调仓建议」任务与调度计划里的「AI 每日简报」
            （同日同方向只记一次）；评估窗口固定为「建议日 → 其后第 30 个净值日」，
            窗口未走完的建议不计入 —— 首批样本要等约 46 个自然日。
            在此之前，止盈/止损线仍是默认值（不受校准影响）。
          </Alert>
        ) : (
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2 }}>
            {rows.map((r) => (
              <Box key={r.mode}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
                  <Tooltip title={r.tip}>
                    <Typography variant="body2" fontWeight={700} sx={{ cursor: 'help' }}>{r.label}</Typography>
                  </Tooltip>
                  {stats.hit_mode === r.mode && (
                    <Chip size="small" color="primary" label="校准使用" sx={{ height: 20, fontSize: 11 }} />
                  )}
                </Box>
                <Typography variant="body2">卖出命中 {fmt(r.mode === 'excess' ? stats.by_mode.excess.sell : stats.by_mode.abs.sell)}</Typography>
                <Typography variant="body2">买入命中 {fmt(r.mode === 'excess' ? stats.by_mode.excess.buy : stats.by_mode.abs.buy)}</Typography>
              </Box>
            ))}
            <Box>
              <Typography variant="body2" fontWeight={700} gutterBottom>当前校准阈值</Typography>
              <Typography variant="body2">止盈线 {stats.params.profit_take_pct ?? '—'}%</Typography>
              <Typography variant="body2">止损线 {stats.params.stop_loss_pct ?? '—'}%</Typography>
              <Typography variant="caption" color="text.secondary">
                已判定样本 {stats.evaluated} 条；样本不足 30 条时不调整（避免对着噪声动阈值）
              </Typography>
            </Box>
          </Box>
        )}
      </CardContent>
    </Card>
  );
};

const PositionsPage: React.FC = () => {
  const [tab, setTab] = useState(0);
  const [items, setItems] = useState<PositionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [snack, setSnack] = useState<Snack>({ open: false, message: '', severity: 'info' });
  const [delTarget, setDelTarget] = useState<PositionItem | null>(null);

  // 建仓/编辑对话框
  const [dlgOpen, setDlgOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<PositionItem | null>(null);
  const [fCode, setFCode] = useState('');
  const [fShares, setFShares] = useState('');
  const [fCost, setFCost] = useState('');
  const [fDate, setFDate] = useState('');

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

  // 建议自进化闭环（命中率双口径 + 校准口径开关）
  const [adv, setAdv] = useState<AdviceStats | null>(null);
  const [advLoading, setAdvLoading] = useState(false);
  const [advBackfilling, setAdvBackfilling] = useState(false);

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
    setEditTarget(null); setFCode(''); setFShares(''); setFCost(''); setFDate(''); setDlgOpen(true);
  };
  const openEdit = (p: PositionItem) => {
    setEditTarget(p); setFCode(p.fund_code); setFShares(String(p.shares));
    setFCost(p.cost_nav != null ? String(p.cost_nav) : '');
    setFDate(p.first_buy_date ?? ''); setDlgOpen(true);
  };

  const submitDlg = async () => {
    const shares = parseFloat(fShares);
    const cost = fCost.trim() === '' ? null : parseFloat(fCost);
    const firstBuy = fDate.trim() === '' ? null : fDate.trim();
    if (!fCode.trim() || !Number.isFinite(shares) || shares <= 0) {
      notify('请填写基金代码与有效份额', 'error'); return;
    }
    if (firstBuy && firstBuy > todayIso()) {
      notify('首次买入日不能晚于今天', 'error'); return;
    }
    try {
      if (editTarget) {
        // 编辑对话框里"留空即清除"：后端用字段是否出现来区分"不动"和"清空"
        await positionApi.update(editTarget.id, { shares, cost_nav: cost, first_buy_date: firstBuy });
      } else {
        await positionApi.create(fCode.trim(), shares, cost, firstBuy);
      }
      setDlgOpen(false);
      notify(editTarget ? '已更新' : '已建仓', 'success');
      refresh();
    } catch (e: any) {
      notify(e?.response?.data?.detail || e?.message || '保存失败', 'error');
    }
  };

  const removeItem = async () => {
    const p = delTarget;
    setDelTarget(null);
    if (!p) return;
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
      setRb(res.data.data);
      setRbMd(res.data.summary_md || '');
    } catch (e: any) {
      notify(e?.message || '调仓引擎运行失败', 'error');
    } finally {
      setRbLoading(false);
    }
  };

  const loadAdvice = useCallback(async () => {
    setAdvLoading(true);
    try {
      const res = await adviceApi.stats();
      setAdv(res.data ?? null);
    } catch (e: any) {
      notify(e?.displayMessage || e?.message || '命中率统计加载失败', 'error');
    } finally {
      setAdvLoading(false);
    }
  }, []);

  useEffect(() => { if (tab === 1) loadAdvice(); }, [tab, loadAdvice]);

  const saveMode = async (mode: string) => {
    try {
      await adviceApi.setHitMode(mode);
      notify(mode === 'excess' ? '校准口径已切换为超额（相对沪深300）' : '校准口径已回退为绝对涨跌（旧口径）', 'success');
      loadAdvice();
    } catch (e: any) {
      notify(e?.response?.data?.detail || e?.displayMessage || '口径切换失败', 'error');
    }
  };

  const runAdviceEval = async () => {
    setAdvBackfilling(true);
    try {
      const r = (await adviceApi.runEval()).data;
      notify(
        `回填完成：判定 ${r?.evaluated ?? 0} 条 / 到期 ${r?.pending ?? 0} 条，`
        + `窗口未走完 ${r?.skipped_window_incomplete ?? 0} 条`
        + `${r?.calibration?.adjusted ? '；阈值已调整' : ''}`,
        'info',
      );
      loadAdvice();
    } catch (e: any) {
      notify(e?.displayMessage || e?.message || '回填失败', 'error');
    } finally {
      setAdvBackfilling(false);
    }
  };

  const askAi = async () => {
    if (!rbMd) return;
    setAiReading(true);
    try {
      await aiApi.chat({
        content: `请解读以下调仓工单（卖出/买入/换仓/观望与组合约束），给出执行顺序、换仓代价（费率/赎回到账）与风险提示：\n\n${rbMd}`,
        context_type: 'pool',
      });
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
            {items.some((p) => p.first_buy_date == null) && (
              <Alert severity="info" sx={{ mb: 1.5 }}>
                {items.filter((p) => p.first_buy_date == null).length} 只持仓未填首次买入日期，
                调仓工单对它们不做赎回费/持有期约束（显示"持有 —"）。点行尾编辑按钮补录即可。
              </Alert>
            )}            {loading ? <CircularProgress size={22} /> : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      {['代码', '名称', '份额', '成本价', '持有期', '最新评分(池内)', '信号', '来源', '更新时间', '操作'].map((h) => (
                        <TableCell key={h} sx={{ whiteSpace: 'nowrap' }}>{h}</TableCell>
                      ))}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {items.length === 0 && (
                      <TableRow><TableCell colSpan={10}><Typography variant="body2" color="text.secondary">暂无持仓，先「建仓 / 录入」或「CSV 导入」</Typography></TableCell></TableRow>
                    )}
                    {items.map((p) => (
                      <TableRow key={p.id} hover>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{p.fund_code}</TableCell>
                        <TableCell sx={{ minWidth: 140 }}>{p.fund_name}</TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{p.shares.toLocaleString()}</TableCell>
                        <TableCell>{p.cost_nav ?? '—'}</TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>
                          <Tooltip title={p.first_buy_date
                            ? `首次买入 ${p.first_buy_date}（赎回费持有期按自然日计）`
                            : '未填首次买入日 → 持有期未知，调仓工单对该只不做赎回费约束（可点编辑补录）'}>
                            <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, cursor: 'help' }}>
                              <span>{p.first_buy_date ?? '—'}</span>
                              <Typography variant="caption"
                                color={p.holding_days == null ? 'text.disabled' : 'text.secondary'}>
                                {p.holding_days == null ? '持有 —' : `持有 ${p.holding_days} 天`}
                              </Typography>
                            </Box>
                          </Tooltip>
                        </TableCell>
                        <TableCell>{p.latest_score ?? '—'}</TableCell>
                        <TableCell sx={{ color: signalColor(p.latest_signal), whiteSpace: 'nowrap' }}>{p.latest_signal ?? '—'}</TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{p.source === 'import' ? '导入' : '手动'}</TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{p.updated_at?.slice(0, 16).replace('T', ' ') ?? '—'}</TableCell>
                        <TableCell>
                          <IconButton size="small" aria-label={`编辑 ${p.fund_name}`} onClick={() => openEdit(p)}><EditOutlinedIcon fontSize="small" /></IconButton>
                          <IconButton size="small" color="error" aria-label={`删除 ${p.fund_name}`} onClick={() => setDelTarget(p)}><DeleteOutlineIcon fontSize="small" /></IconButton>
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
              {rb?.fee_policy && (
                <Tooltip title="阶梯来自可配参数 redemption_fee_ladder；惩罚档是 redemption_fee_penalize_pct；关闭开关 redemption_fee_enabled=0 回到旧工单">
                  <Typography variant="caption" color="text.secondary" sx={{ cursor: 'help' }}>
                    {rb.fee_policy.enabled
                      ? `赎回费阶梯 ${rb.fee_policy.ladder_text}，≥${rb.fee_policy.penalize_pct}% 降级观望`
                      : '赎回费约束已关闭（redemption_fee_enabled=0）'}
                  </Typography>
                </Tooltip>
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
                      <TableHead>
                        <TableRow>
                          <TableCell>卖出</TableCell><TableCell>买入去向</TableCell>
                          <TableCell>赛道</TableCell>
                          <TableCell>
                            <Tooltip title="分差 = 两侧各自最新一条分析记录的池内相对分之差。两条记录可能来自不同交易日，而池内相对分只在当日池内可比，所以这个差值适合用来给候选配对排序，不适合当作多 3 分就值 3 分的绝对刻度">
                              <span>分差</span>
                            </Tooltip>
                          </TableCell>
                          <TableCell>卖侧持有/费率</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {rb.swaps.map((s, i) => (
                          <TableRow key={i}>
                            <TableCell>{s.sell_name}({s.sell_code})</TableCell>
                            <TableCell>{s.buy_name}({s.buy_code})</TableCell>
                            <TableCell>{s.theme}</TableCell>
                            <TableCell>{s.score_gap}</TableCell>
                            <TableCell sx={{ whiteSpace: 'nowrap' }}>
                              {s.sell_holding_days == null ? '持有 —' : `持有 ${s.sell_holding_days} 天`}
                              {s.sell_fee_pct != null ? ` / 费 ${s.sell_fee_pct}%` : ''}
                            </TableCell>
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
                        <Tooltip key={h.code} title={`评分 ${h.score ?? '—'} / ${h.direction ?? '—'}｜权重口径 ${
                            h.weight_basis === 'market' ? '实时净值市值'
                              : h.weight_basis === 'cost' ? '成本市值（净值未命中）'
                              : h.weight_basis === 'shares' ? '份额估算（无净值无成本）'
                              : '等权近似'}｜${h.holding_days == null ? '持有期未知' : `持有 ${h.holding_days} 天`}`}>
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

          <AdviceLearningCard
            stats={adv}
            loading={advLoading}
            onRefresh={loadAdvice}
            onModeChange={saveMode}
            onBackfill={runAdviceEval}
            backfilling={advBackfilling}
          />
        </>
      )}

      {/* 建仓/编辑 */}
      <Dialog open={dlgOpen} onClose={() => setDlgOpen(false)} maxWidth="xs" fullScreen={false}>
        <DialogTitle>{editTarget ? `编辑持仓：${editTarget.fund_name}` : '建仓 / 录入'}</DialogTitle>
        <DialogContent>
          <TextField autoFocus fullWidth margin="dense" label="基金代码（须在基金池）" value={fCode}
            onChange={(e) => setFCode(e.target.value)} disabled={!!editTarget} />
          <TextField fullWidth margin="dense" label="持有份额" value={fShares} onChange={(e) => setFShares(e.target.value)} />
          <TextField fullWidth margin="dense" label="持仓成本价（可空=无成本口径）" value={fCost} onChange={(e) => setFCost(e.target.value)} />
          <TextField fullWidth margin="dense" type="date" InputLabelProps={{ shrink: true }}
            label="首次买入日期（可空）" value={fDate} onChange={(e) => setFDate(e.target.value)} />
          <Typography variant="caption" color="text.secondary">
            首买日是持有期/赎回费约束的唯一输入。不填则该持仓不做费用约束（工单显示"持有 —"）；
            它是实际买入日，与"录入系统时间"无关，可以补录历史。
          </Typography>
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
            直接粘贴支付宝/天天基金等导出内容；自动按表头别名识别「基金代码/持有份额/持仓成本价/首次买入日期」列
            （首次买入日期列名兼容「买入日期」「确认日期」），
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

      <ConfirmDialog
        open={!!delTarget}
        title="删除持仓"
        message={`删除持仓：${delTarget?.fund_name}(${delTarget?.fund_code})？`}
        confirmLabel="删除"
        confirmColor="error"
        onConfirm={removeItem}
        onCancel={() => setDelTarget(null)}
      />

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
