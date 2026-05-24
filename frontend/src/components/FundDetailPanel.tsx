/**
 * 基金详情面板 — 阶段涨幅 / 持仓明细 & 调仓 / 基金经理 & 变更
 */

import React, { useEffect, useState, useMemo } from 'react';
import {
  Box,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  CircularProgress,
  Tabs,
  Tab,
  Button,
  Chip,
  Collapse,
  IconButton,
  Alert,
} from '@mui/material';
import { ExpandMore, ExpandLess } from '@mui/icons-material';
import { fundApi } from '../api/fund';
import type { FundPeriodReturn, HoldingChanges, ManagerChanges } from '../types';

/* ================================================================
   阶段涨幅子组件
   ================================================================ */

const PERIOD_LABELS: { key: keyof FundPeriodReturn; label: string }[] = [
  { key: 'return_1m', label: '近一月' },
  { key: 'return_3m', label: '近三月' },
  { key: 'return_6m', label: '近六月' },
  { key: 'return_1y', label: '近一年' },
];

const fmtReturn = (v: string | null): string => {
  if (!v) return '--';
  const n = parseFloat(v);
  return isNaN(n) ? '--' : `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
};

const returnColor = (v: string | null): string => {
  if (!v) return '#95A5A6';
  const n = parseFloat(v);
  if (isNaN(n)) return '#95A5A6';
  return n > 0 ? '#27AE60' : n < 0 ? '#E74C3C' : '#95A5A6';
};

const parseReturn = (v: string | null): number => {
  if (!v) return -Infinity;
  const n = parseFloat(v);
  return isNaN(n) ? -Infinity : n;
};

const ReturnTab: React.FC<{ data: FundPeriodReturn[] }> = ({ data }) => {
  const [sortBy, setSortBy] = useState<keyof FundPeriodReturn>('return_1m');
  const [showAll, setShowAll] = useState(false);

  const sorted = useMemo(() => {
    const copy = [...data];
    copy.sort((a, b) => parseReturn(b[sortBy]) - parseReturn(a[sortBy]));
    return copy;
  }, [data, sortBy]);

  const display = showAll ? sorted : sorted.slice(0, 5);

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          {PERIOD_LABELS.map((p) => (
            <Chip key={p.key} label={p.label}
              color={sortBy === p.key ? 'primary' : 'default'}
              size="small" onClick={() => setSortBy(p.key)} clickable />
          ))}
        </Box>
        <Button size="small" onClick={() => setShowAll(!showAll)}>
          {showAll ? '收起 (TOP5)' : `展示全部 (${data.length})`}
        </Button>
      </Box>
      <TableContainer component={Paper} variant="outlined">
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell sx={{ fontWeight: 600, width: 40 }}>#</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>代码</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>名称</TableCell>
              {PERIOD_LABELS.map((p) => (
                <TableCell key={p.key} align="right" sx={{ fontWeight: 600 }}>{p.label}</TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {display.map((fund, idx) => (
              <TableRow key={fund.code} hover selected={idx < 5}>
                <TableCell sx={{ color: idx < 5 ? '#1976D2' : undefined, fontWeight: idx < 5 ? 600 : 400 }}>
                  {idx + 1}
                </TableCell>
                <TableCell>{fund.code}</TableCell>
                <TableCell>{fund.name}</TableCell>
                {PERIOD_LABELS.map((p) => {
                  const val = fund[p.key];
                  return (
                    <TableCell key={p.key} align="right" sx={{ color: returnColor(val), fontWeight: 500 }}>
                      {fmtReturn(val)}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
};

/* ================================================================
   持仓 & 调仓 diff 子组件
   ================================================================ */

const ChangeTag: React.FC<{ label: string; color: 'success' | 'warning' }> = ({ label, color }) => (
  <Chip size="small" label={label}
    sx={{
      backgroundColor: color === 'success' ? '#e8f5e9' : '#fff3e0',
      color: color === 'success' ? '#2e7d32' : '#e65100',
      fontWeight: 600, fontSize: '0.7rem', height: 20, mr: 0.5,
    }} />
);

const HoldingRow: React.FC<{
  fundId: number;
  code: string;
  name: string;
  changes: HoldingChanges | null;
}> = ({ fundId, code, name, changes }) => {
  const [open, setOpen] = useState(false);
  const [holdings, setHoldings] = useState<any[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!open || loaded) return;
    fundApi.getHoldings(fundId).then((res) => { setHoldings(res.data || []); setLoaded(true); });
  }, [open, fundId, loaded]);

  const top3 = holdings.slice(0, 3)
    .map((h: any) => `${h.stock_name}${h.ratio != null ? h.ratio.toFixed(1) : ''}%`)
    .join(' | ');

  return (
    <>
      <TableRow hover sx={{ cursor: 'pointer' }} onClick={() => setOpen(!open)}>
        <TableCell><IconButton size="small">{open ? <ExpandLess /> : <ExpandMore />}</IconButton></TableCell>
        <TableCell>{code}</TableCell>
        <TableCell>{name}</TableCell>
        <TableCell>{changes?.latest_quarter?.slice(0, 10) || '--'}</TableCell>
        <TableCell sx={{ fontSize: '0.85em', color: 'text.secondary', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {top3 || '--'}
        </TableCell>
        <TableCell sx={{ minWidth: 160 }}>
          {changes && (changes.added.length > 0 || changes.removed.length > 0) && (
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.3 }}>
              {changes.added.length > 0 && <ChangeTag label={`+${changes.added.length}`} color="success" />}
              {changes.removed.length > 0 && <ChangeTag label={`-${changes.removed.length}`} color="warning" />}
            </Box>
          )}
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={6} sx={{ p: 0 }}>
          <Collapse in={open}>
            <Box sx={{ px: 3, pb: 2, pt: 1 }}>
              {/* 调仓 diff */}
              {changes && (changes.added.length > 0 || changes.removed.length > 0) && (
                <Box sx={{ mb: 1.5, p: 1.5, bgcolor: '#fafafa', borderRadius: 1 }}>
                  <Typography variant="caption" sx={{ fontWeight: 600, mb: 0.5, display: 'block' }}>
                    调仓 diff: {changes.previous_quarter?.slice(0, 10)} → {changes.latest_quarter?.slice(0, 10)}
                  </Typography>
                  {changes.added.length > 0 && (
                    <Typography variant="caption" sx={{ color: '#2e7d32', display: 'block' }}>
                      新增: {changes.added.map(s => `${s.stock_name}(${s.stock_code})${s.ratio != null ? ` ${s.ratio.toFixed(1)}%` : ''}`).join('、')}
                    </Typography>
                  )}
                  {changes.removed.length > 0 && (
                    <Typography variant="caption" sx={{ color: '#e65100', display: 'block' }}>
                      移除: {changes.removed.map(s => `${s.stock_name}(${s.stock_code})`).join('、')}
                    </Typography>
                  )}
                </Box>
              )}

              {holdings.length === 0 ? (
                <Typography variant="body2" color="text.secondary">暂无持仓数据</Typography>
              ) : (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ fontWeight: 600 }}>股票代码</TableCell>
                      <TableCell sx={{ fontWeight: 600 }}>股票名称</TableCell>
                      <TableCell align="right" sx={{ fontWeight: 600 }}>占净值比例%</TableCell>
                      <TableCell align="right" sx={{ fontWeight: 600 }}>持仓市值(万)</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {holdings.map((h: any) => (
                      <TableRow key={h.stock_code}>
                        <TableCell>{h.stock_code}</TableCell>
                        <TableCell>{h.stock_name}</TableCell>
                        <TableCell align="right">{h.ratio != null ? h.ratio.toFixed(2) : '--'}</TableCell>
                        <TableCell align="right">{h.market_value != null ? h.market_value.toFixed(2) : '--'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
};

const HoldingTab: React.FC<{ funds: { id: number; code: string; name: string }[]; changesMap: Record<number, HoldingChanges | null> }> = ({ funds, changesMap }) => (
  <TableContainer component={Paper} variant="outlined">
    <Table size="small">
      <TableHead>
        <TableRow>
          <TableCell sx={{ fontWeight: 600, width: 40 }} />
          <TableCell sx={{ fontWeight: 600 }}>代码</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>名称</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>最新季度</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>前3持仓</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>调仓</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {funds.length === 0 ? (
          <TableRow><TableCell colSpan={6} align="center">暂无基金数据</TableCell></TableRow>
        ) : (
          funds.map((f) => (
            <HoldingRow key={f.id} fundId={f.id} code={f.code} name={f.name}
              changes={changesMap[f.id] ?? null} />
          ))
        )}
      </TableBody>
    </Table>
  </TableContainer>
);

/* ================================================================
   基金经理 & 变更子组件
   ================================================================ */

const ManagerTab: React.FC<{
  funds: { id: number; code: string; name: string }[];
  changesMap: Record<number, ManagerChanges | null>;
}> = ({ funds, changesMap }) => (
  <TableContainer component={Paper} variant="outlined">
    <Table size="small">
      <TableHead>
        <TableRow>
          <TableCell sx={{ fontWeight: 600 }}>代码</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>名称</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>当前经理</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>所属公司</TableCell>
          <TableCell align="right" sx={{ fontWeight: 600 }}>从业天数</TableCell>
          <TableCell align="right" sx={{ fontWeight: 600 }}>管理规模(亿)</TableCell>
          <TableCell align="right" sx={{ fontWeight: 600 }}>最佳回报%</TableCell>
          <TableCell sx={{ fontWeight: 600 }}>变更</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {funds.length === 0 ? (
          <TableRow><TableCell colSpan={8} align="center">暂无基金数据</TableCell></TableRow>
        ) : (
          funds.map((f) => {
            const mgrData = changesMap[f.id];
            const current = mgrData?.current || [];
            const history = mgrData?.history || [];
            const changed = mgrData?.changed || false;

            return (
              <TableRow key={f.id} hover>
                <TableCell>{f.code}</TableCell>
                <TableCell>{f.name}</TableCell>
                <TableCell>
                  {current.map(m => m.manager_name).join(', ') || '--'}
                </TableCell>
                <TableCell>{current[0]?.company || '--'}</TableCell>
                <TableCell align="right">{current[0]?.tenure_days ?? '--'}</TableCell>
                <TableCell align="right">{current[0]?.asset_scale != null ? current[0].asset_scale.toFixed(1) : '--'}</TableCell>
                <TableCell align="right">{current[0]?.best_return != null ? current[0].best_return.toFixed(1) : '--'}</TableCell>
                <TableCell>
                  {changed && <ChangeTag label="经理变更" color="warning" />}
                  {history.length > 0 && (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontSize: '0.65rem' }}>
                      前任: {history.map(m => m.manager_name).join(', ')}
                    </Typography>
                  )}
                </TableCell>
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  </TableContainer>
);

/* ================================================================
   主组件
   ================================================================ */

interface FundItem {
  id: number;
  code: string;
  name: string;
}

interface Props {
  returns?: FundPeriodReturn[];
  loading?: boolean;
  updatedAt?: string | null;
}

const FundDetailPanel: React.FC<Props> = ({ returns: externalReturns, loading: externalLoading }) => {
  const [tabIdx, setTabIdx] = useState(0);
  const [funds, setFunds] = useState<FundItem[]>([]);
  const [changesMap, setChangesMap] = useState<Record<number, any>>({});
  const [returns, setReturns] = useState<FundPeriodReturn[]>(externalReturns || []);
  const [loading, setLoading] = useState(externalLoading ?? true);
  const [error, setError] = useState<string | null>(null);

  const loadData = async () => {
    if (externalReturns && externalReturns.length > 0) {
      // 外部已提供阶段涨幅数据
      setReturns(externalReturns);
    }

    // 基金列表 + 变更摘要始终并行加载（不依赖外部缓存）
    try {
      const [listRes, changeRes] = await Promise.all([
        fundApi.list('active'),
        fundApi.getChangeSummary(),
      ]);
      const fundList: FundItem[] = (listRes.data || []).map((f: any) => ({ id: f.id, code: f.code, name: f.name }));
      setFunds(fundList);

      const cm: Record<number, any> = {};
      (changeRes.data || []).forEach((c: any) => {
        cm[c.fund_id] = {
          holding_changes: c.holding_changes,
          manager_changes: c.manager_changes,
          tags: c.tags || [],
        };
      });
      setChangesMap(cm);
    } catch (err) {
      console.error('加载基金详情失败', err);
      setError('加载基金详情失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (externalReturns) {
      setReturns(externalReturns);
    }
  }, [externalReturns]);

  useEffect(() => {
    if (externalLoading !== undefined) {
      setLoading(externalLoading);
    }
  }, [externalLoading]);

  useEffect(() => { loadData(); }, []);

  if (loading) {
    return <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>;
  }

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error} <Button size="small" onClick={loadData}>重试</Button></Alert>}

      {/* 变更标签总览 */}
      {Object.entries(changesMap).length > 0 && (
        <Box sx={{ mb: 2, p: 1.5, bgcolor: '#f5f5f5', borderRadius: 1, display: 'flex', flexWrap: 'wrap', gap: 0.5, alignItems: 'center' }}>
          <Typography variant="caption" sx={{ fontWeight: 600, mr: 1 }}>变更标注:</Typography>
          {Object.entries(changesMap).map(([fid, c]: [string, any]) => {
            const fund = funds.find(f => f.id === Number(fid));
            if (!c.tags?.length) return null;
            return c.tags.map((tag: string) => (
              <Chip key={`${fid}-${tag}`} size="small"
                label={`${fund?.code || fid} ${tag}`}
                variant="outlined"
                sx={{ height: 22, fontSize: '0.7rem' }} />
            ));
          })}
        </Box>
      )}

      {/* 子 Tabs */}
      <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}>
        <Tabs value={tabIdx} onChange={(_, v) => setTabIdx(v)}>
          <Tab label="阶段涨幅" />
          <Tab label="持仓明细" />
          <Tab label="基金经理" />
        </Tabs>
      </Box>

      {tabIdx === 0 && <ReturnTab data={returns} />}
      {tabIdx === 1 && (
        <HoldingTab funds={funds} changesMap={Object.fromEntries(
          Object.entries(changesMap).map(([k, v]: [string, any]) => [Number(k), v.holding_changes])
        )} />
      )}
      {tabIdx === 2 && (
        <ManagerTab funds={funds} changesMap={Object.fromEntries(
          Object.entries(changesMap).map(([k, v]: [string, any]) => [Number(k), v.manager_changes])
        )} />
      )}
    </Box>
  );
};

export default FundDetailPanel;
