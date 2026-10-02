/**
 * 影子评分卡（§3 第二批）— 新口径先并排算、只写 shadow_* 列，达标再切生产
 *
 * 这块页面的存在意义是"改完能证明变好"：生产信号仍是旧口径，所以这里显示的每个
 * 分歧数字都是真实并排跑出来的，而不是纸面推演。当前注册的口径是 `caliber_2c`
 * （Q2 市场因子退出加权和 + Q3 动量簇上限/趋势乘性位 + Q4 覆盖率折算 + Q1 五档只渲染），
 * 卡片下方的参数就是它这一轮实际用的那几个数。
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Divider, Paper, Switch, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip,
  Typography,
} from '@mui/material';
import { Refresh as RefreshIcon } from '@mui/icons-material';
import { analysisApi } from '../api/analysis';
import type { ShadowConfig, ShadowDivergence } from '../types';

interface Props {
  onNotify?: (message: string, severity: 'success' | 'error') => void;
}

const WINDOW_OPTIONS = [5, 10, 20, 40];

/** 方向迁移的可读写法：skip 是 2C 的"覆盖率不足，本轮不出记录"，不是观望 */
const DIRECTION_TEXT: Record<string, string> = {
  buy: '加仓', sell: '减仓', hold: '观望', skip: '不出记录',
};
const dirText = (v: string) => DIRECTION_TEXT[v] || v;
const migrationText = (key: string) => {
  const [oldDir, newDir] = key.split('->');
  return `${dirText(oldDir)}→${dirText(newDir)}`;
};

const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v}%`);
const signed = (v: number | null | undefined) => {
  if (v === null || v === undefined) return '—';
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}`;
};

const ShadowScoringCard: React.FC<Props> = ({ onNotify }) => {
  const [cfg, setCfg] = useState<ShadowConfig | null>(null);
  const [report, setReport] = useState<ShadowDivergence | null>(null);
  const [days, setDays] = useState(10);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async (windowDays: number) => {
    setLoading(true);
    try {
      const [c, r] = await Promise.all([
        analysisApi.shadowConfig(),
        analysisApi.shadowDivergence(windowDays),
      ]);
      setCfg(c.data ?? null);
      setReport(r.data ?? null);
    } catch (e: any) {
      onNotify?.(e?.response?.data?.detail || e?.message || '影子评分数据加载失败', 'error');
    } finally {
      setLoading(false);
    }
  }, [onNotify]);

  useEffect(() => { load(days); }, [days, load]);

  const patch = async (body: { enabled?: boolean; variant?: string }, okText: string) => {
    setSaving(true);
    try {
      const res = await analysisApi.updateShadowConfig(body);
      setCfg(res.data ?? null);
      onNotify?.(okText, 'success');
      await load(days);
    } catch (e: any) {
      onNotify?.(e?.response?.data?.detail || e?.message || '影子评分配置保存失败', 'error');
    } finally {
      setSaving(false);
    }
  };

  const summary = report?.summary ?? {};
  const shadowRows = summary.shadow_rows ?? 0;
  const noData = !report || report.daily.every((d) => d.shadow_rows === 0);
  const variants = cfg?.registered ?? [];

  return (
    <Paper variant="outlined" sx={{ p: 2, mt: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 2, flexWrap: 'wrap' }}>
        <Box sx={{ flex: '1 1 320px' }}>
          <Typography variant="h6">影子评分与口径分歧</Typography>
          <Typography variant="body2" color="text.secondary">
            生产列（weighted_score / signal_direction）仍是旧口径；新口径每轮并排另算一次，
            只写 shadow_* 列。纯 Python 加权，<b>零上游请求</b>。
            切换判据：分歧比例连续 {cfg?.stable_days_required ?? 5} 个交易日 &lt; {cfg?.divergence_threshold_pct ?? 15}%。
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <TextField
            size="small" select label="回看交易日" value={days}
            onChange={(e) => setDays(Number(e.target.value))} sx={{ width: 130 }}
            SelectProps={{ native: true }}
          >
            {WINDOW_OPTIONS.map((d) => <option key={d} value={d}>{d} 天</option>)}
          </TextField>
          <Button variant="outlined" startIcon={<RefreshIcon />}
            onClick={() => load(days)} disabled={loading}>
            {loading ? '加载中...' : '刷新'}
          </Button>
        </Box>
      </Box>

      <Divider sx={{ my: 1.5 }} />

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', mb: 1 }}>
        <Tooltip title="关闭后本轮起完全不再计算影子，shadow_* 列写 NULL；已落库的历史影子行保留，回滚零成本">
          <span>
            <FormControlLabelLike
              label="启用影子评分"
              checked={!!cfg?.enabled}
              disabled={saving || !cfg}
              onChange={(v) => patch({ enabled: v }, v ? '影子评分已开启' : '影子评分已关闭（生产信号不受影响）')}
            />
          </span>
        </Tooltip>
        <Tooltip title={variants.length
          ? '变体 = 一套新口径的实现；空 = 用注册表里的第一个。切换变体后的分歧数字与之前不可比'
          : '注册表为空：内置的 2C 口径（caliber_2c）没被加载，本轮分析不会产生影子列'}>
          <TextField
            size="small" select label="影子变体" value={cfg?.variant ?? ''}
            disabled={saving || !cfg || variants.length === 0}
            onChange={(e) => patch({ variant: e.target.value }, '影子变体已更新')}
            sx={{ width: 220 }} SelectProps={{ native: true }}
          >
            <option value="">（自动：注册表首个）</option>
            {variants.map((v) => <option key={v} value={v}>{v}</option>)}
          </TextField>
        </Tooltip>
        {variants.length === 0 && (
          <Chip size="small" color="warning" label="变体注册表为空（无可用口径）" />
        )}
      </Box>

      {cfg?.variant && cfg?.variant_descriptions?.[cfg.variant] && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
          当前口径 {cfg.variant}：{cfg.variant_descriptions[cfg.variant]}
        </Typography>
      )}

      {(cfg?.caliber_params?.length ?? 0) > 0 && (
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 1 }}>
          {cfg!.caliber_params!.map((p) => (
            <Tooltip key={p.key}
              title={`${p.governs}｜${p.note || ''}｜合法区间 ${p.min}~${p.max}，默认 ${p.default}；`
                + '改这些数只影响影子列，生产信号不动（在「质量过滤参数」页搜 shadow_2c）'}>
              <Chip size="small" variant="outlined" color={p.out_of_range ? 'warning' : 'default'}
                label={`${p.label}：${p.value}${p.out_of_range ? '（越界已钳位）' : ''}`} />
            </Tooltip>
          ))}
        </Box>
      )}

      {noData ? (
        <Alert severity="info" sx={{ mb: 1 }}>
          <Typography variant="body2">尚无影子对照数据</Typography>
          <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
            {(report?.caveats ?? []).map((c, i) => (
              <li key={i}><Typography variant="caption">{c}</Typography></li>
            ))}
          </ul>
        </Alert>
      ) : (
        <>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 1 }}>
            <Chip size="small" label={`影子样本 ${shadowRows} 行 / ${summary.days_with_shadow ?? 0} 个交易日`} />
            <Chip size="small" label={`分歧 ${summary.divergent ?? 0} 只（${pct(summary.divergence_pct)}）`} />
            {(summary.skip_rows ?? 0) > 0 && (
              <Tooltip title="新口径认为覆盖率不足、本轮不出记录：这类分歧的含义是记录消失（仪表盘/推送/回测里都不再有这只），比买卖翻转更重">
                <span><Chip size="small" color="warning" label={`其中 skip ${summary.skip_rows} 只`} /></span>
              </Tooltip>
            )}
            <Chip size="small" label={`日均可分歧比例 ${pct(summary.avg_divergence_pct)}`} />
            <Chip
              size="small"
              color={report?.meets_ratio_criterion ? 'success' : 'default'}
              label={`连续达标 ${summary.stable_days ?? 0} 日${report?.meets_ratio_criterion ? '（判据一满足）' : ''}`}
            />
            {(summary.buy_to_other ?? 0) > 0 && (
              <Chip size="small" color="secondary" label={`旧 buy→其他 ${summary.buy_to_other} 只`} />
            )}
            {(summary.sell_to_other ?? 0) > 0 && (
              <Chip size="small" color="secondary" label={`旧 sell→其他 ${summary.sell_to_other} 只`} />
            )}
          </Box>
          {report?.conclusion && (
            <Alert
              severity={report.meets_ratio_criterion ? 'success' : 'warning'}
              sx={{ mb: 1 }}
            >
              <Typography variant="body2">{report.conclusion}</Typography>
            </Alert>
          )}

          <TableContainer component={Paper} variant="outlined" sx={{ mb: 1 }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>日期</TableCell>
                  <TableCell align="right">影子/总行</TableCell>
                  <TableCell align="right">分歧</TableCell>
                  <TableCell align="right">其中 skip</TableCell>
                  <TableCell align="right">比例</TableCell>
                  <TableCell align="right">平均分差</TableCell>
                  <TableCell align="right">最大绝对分差</TableCell>
                  <TableCell>方向迁移</TableCell>
                  <TableCell align="right">池/覆盖率</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {(report?.daily ?? []).map((d) => (
                  <TableRow key={d.date}>
                    <TableCell>{d.date}</TableCell>
                    <TableCell align="right">
                      {d.shadow_rows}/{d.rows}
                      {d.low_sample && (
                        <Tooltip title={`影子样本 < 10 只：单日一两只分歧就能越过判据线，只作观察`}>
                          <Chip size="small" label="小样本" color="warning" sx={{ ml: 0.5, height: 20 }} />
                        </Tooltip>
                      )}
                    </TableCell>
                    <TableCell align="right">{d.divergent}</TableCell>
                    <TableCell align="right" sx={{ color: d.skip_rows ? 'warning.main' : 'text.secondary' }}>
                      {d.skip_rows || '—'}
                    </TableCell>
                    <TableCell align="right" sx={{ color: d.divergence_pct >= (report?.divergence_threshold_pct ?? 15) ? 'warning.main' : 'success.main' }}>
                      {d.divergence_pct}%
                    </TableCell>
                    <TableCell align="right">{signed(d.avg_delta)}</TableCell>
                    <TableCell align="right">{signed(d.max_abs_delta)}</TableCell>
                    <TableCell>
                      {Object.entries(d.migration).map(([k, v]) => (
                        <Tooltip key={k} title={`${k}（原始迁移键）`}>
                          <Chip size="small" label={`${migrationText(k)} ×${v}`}
                            color={k.endsWith('skip') ? 'warning' : 'default'}
                            sx={{ mr: 0.5, mb: 0.5, height: 20 }} />
                        </Tooltip>
                      ))}
                    </TableCell>
                    <TableCell align="right">
                      {d.pool_size ?? '—'} / {d.coverage === null ? '—' : `${Math.round(d.coverage * 100)}%`}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>

          {Object.keys(report?.buckets ?? {}).length > 0 && (
            <TableContainer component={Paper} variant="outlined" sx={{ mb: 1 }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>原始分档（按当次动态阈值）</TableCell>
                    <TableCell align="right">行数</TableCell>
                    <TableCell align="right">分歧</TableCell>
                    <TableCell align="right">比例</TableCell>
                    <TableCell align="right">平均分差</TableCell>
                    <TableCell>主要迁移</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {Object.entries(report?.buckets ?? {}).map(([key, b]) => (
                    <TableRow key={key}>
                      <TableCell>{b.label}</TableCell>
                      <TableCell align="right">{b.rows}</TableCell>
                      <TableCell align="right">{b.divergent}</TableCell>
                      <TableCell align="right">{b.divergence_pct}%</TableCell>
                      <TableCell align="right">{signed(b.avg_delta)}</TableCell>
                      <TableCell>
                        {Object.entries(b.migration).slice(0, 3).map(([k, v]) => (
                          <Chip key={k} size="small" label={`${migrationText(k)} ×${v}`} sx={{ mr: 0.5, height: 20 }} />
                        ))}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}

          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 1 }}>
            {Object.entries(report?.variants ?? {}).map(([name, v]) => (
              <Chip key={name} size="small" variant="outlined"
                label={`${name}：${v.rows} 行（${v.first_date} ~ ${v.last_date}）`} />
            ))}
          </Box>

          {report.caveats.length > 0 && (
            <Box sx={{ pl: 1 }}>
              <Typography variant="subtitle2">解读注意</Typography>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {report.caveats.map((c, i) => (
                  <li key={i}>
                    <Typography variant="caption"
                      color={c.includes('skip') ? 'warning.main' : 'text.secondary'}>
                      {c}
                    </Typography>
                  </li>
                ))}
              </ul>
            </Box>
          )}
        </>
      )}
    </Paper>
  );
};

/** Switch + label 的小封装（MUI FormControlLabel 在 Tooltip 里包 span 会丢点击） */
const FormControlLabelLike: React.FC<{
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
}> = ({ label, checked, disabled, onChange }) => (
  <Box sx={{ display: 'flex', alignItems: 'center' }}>
    <Switch size="small" checked={checked} disabled={disabled}
      onChange={(_, v) => onChange(v)} aria-label={label} />
    <Typography variant="body2">{label}</Typography>
  </Box>
);

export default ShadowScoringCard;
