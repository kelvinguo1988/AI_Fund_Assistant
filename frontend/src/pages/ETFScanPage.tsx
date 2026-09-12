/**
 * 潜力 ETF 扫描 — 全市场量价/资金三榜单（模块 B）
 * 仅供量价特征排名参考，不构成投资建议。
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Tab,
  Tabs,
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
import { TravelExplore as ScanIcon, Add as AddIcon } from '@mui/icons-material';
import { fundApi, type ETFScanItem, type ETFScanResult } from '../api/fund';
import type { FundCreate } from '../types';

const pct = (v?: number | null, suffix = '%') =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}${suffix}`;

const growthColor = (v?: number | null) =>
  v == null ? 'inherit' : v > 0 ? '#f44336' : v < 0 ? '#4caf50' : 'inherit';

const fmtAmount = (v?: number | null) =>
  v == null ? '—' : `${(v / 1e8).toFixed(2)} 亿`;

const ETFScanPage: React.FC = () => {
  const [tab, setTab] = useState(0);
  const [result, setResult] = useState<ETFScanResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addingCode, setAddingCode] = useState<string | null>(null);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({
    open: false, message: '', severity: 'success',
  });
  const poolRef = useRef<Set<string>>(new Set());

  const loadScan = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fundApi.etfScan();
      setResult(res.data as ETFScanResult);
      poolRef.current = new Set((res.data as ETFScanResult)?.pool_codes ?? []);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || '扫描失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadScan();
  }, [loadScan]);

  const addToPool = async (item: ETFScanItem) => {
    setAddingCode(item.code);
    try {
      await fundApi.create({
        code: item.code, name: item.name, fund_type: 'etf',
      } as FundCreate);
      poolRef.current.add(item.code);
      setResult((prev) => prev ? {
        ...prev,
        movers: prev.movers.map((x) => x.code === item.code ? { ...x, in_pool: true } : x),
        inflow: prev.inflow.map((x) => x.code === item.code ? { ...x, in_pool: true } : x),
        unusual: prev.unusual.map((x) => x.code === item.code ? { ...x, in_pool: true } : x),
      } : prev);
      setSnackbar({ open: true, message: `${item.name} 已加入基金池（标签后台自动补全）`, severity: 'success' });
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || '加入失败';
      setSnackbar({ open: true, message: detail.includes('已存在') ? '该基金已在池中' : detail, severity: 'error' });
    } finally {
      setAddingCode(null);
    }
  };

  const rows = report_rows(result, tab);

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h5">潜力 ETF 扫描</Typography>
        <Button
          variant="contained" startIcon={<ScanIcon />}
          onClick={loadScan} disabled={loading}
        >
          {loading ? '扫描中…' : '重新扫描'}
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading && !result && <CircularProgress sx={{ mb: 2 }} />}

      {result && (
        <>
          <Alert severity="info" sx={{ mb: 2 }}>
            扫描全市场 {result.scanned} 只 ETF（量价特征排名，仅供参考不构成投资建议）；
            标注「已持有」的在你的基金池中，可一键加入池内。
          </Alert>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
            <Tab label={`📈 量价齐升 (${result.movers.length})`} />
            <Tab label={`💰 资金流入 (${result.inflow.length})`} />
            <Tab label={`🔥 异动 (${result.unusual.length})`} />
          </Tabs>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>代码</TableCell>
                  <TableCell>名称</TableCell>
                  <TableCell align="right">最新价</TableCell>
                  <TableCell align="right">涨跌幅</TableCell>
                  <TableCell align="right">换手率</TableCell>
                  <TableCell align="right">量比</TableCell>
                  <TableCell align="right">主力净流入占比</TableCell>
                  <TableCell align="right">成交额</TableCell>
                  <TableCell align="center">操作</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((it) => (
                  <TableRow key={it.code} hover>
                    <TableCell>{it.code}</TableCell>
                    <TableCell>
                      {it.name}
                      {it.in_pool && <Chip size="small" label="已持有" sx={{ ml: 1, height: 20, fontSize: '0.7rem' }} />}
                    </TableCell>
                    <TableCell align="right">{it.price ?? '—'}</TableCell>
                    <TableCell align="right" sx={{ color: growthColor(it.pct), fontWeight: 500 }}>
                      {pct(it.pct)}
                    </TableCell>
                    <TableCell align="right">{it.turnover_rate != null ? `${it.turnover_rate.toFixed(2)}%` : '—'}</TableCell>
                    <TableCell align="right">{it.volume_ratio?.toFixed(2) ?? '—'}</TableCell>
                    <TableCell align="right" sx={{ color: growthColor(it.main_inflow_pct) }}>
                      {it.main_inflow_pct != null ? `${it.main_inflow_pct > 0 ? '+' : ''}${it.main_inflow_pct.toFixed(1)}%` : '—'}
                    </TableCell>
                    <TableCell align="right">{fmtAmount(it.amount)}</TableCell>
                    <TableCell align="center">
                      {it.in_pool ? (
                        <Chip size="small" label="在池" variant="outlined" />
                      ) : (
                        <Button
                          size="small" startIcon={<AddIcon />}
                          disabled={addingCode === it.code}
                          onClick={() => addToPool(it)}
                        >
                          加池
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
                {rows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={9} align="center">当前无符合条件的 ETF</TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
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

function report_rows(result: ETFScanResult | null, tab: number): ETFScanItem[] {
  if (!result) return [];
  return [result.movers, result.inflow, result.unusual][tab] ?? [];
}

export default ETFScanPage;
