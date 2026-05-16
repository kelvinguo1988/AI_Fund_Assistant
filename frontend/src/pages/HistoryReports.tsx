/**
 * 历史报告页面
 */

import React, { useEffect, useState } from 'react';
import {
  Box,
  Typography,
  TextField,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Snackbar,
  Alert,
} from '@mui/material';
import { Visibility as ViewIcon } from '@mui/icons-material';
import SignalIndicator from '../components/SignalIndicator';
import { analysisApi } from '../api/analysis';
import type { AnalysisResultOut } from '../types';

const HistoryReports: React.FC = () => {
  const [results, setResults] = useState<AnalysisResultOut[]>([]);
  const [filterDate, setFilterDate] = useState('');
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedResult, setSelectedResult] = useState<AnalysisResultOut | null>(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' });

  const loadResults = async () => {
    try {
      const res = await analysisApi.query(filterDate ? { date: filterDate } : undefined);
      if (res.data) setResults(res.data);
    } catch {
      setSnackbar({ open: true, message: '加载历史报告失败', severity: 'error' });
    }
  };

  useEffect(() => { loadResults(); }, [filterDate]);

  const handleViewDetail = (result: AnalysisResultOut) => {
    setSelectedResult(result);
    setDetailOpen(true);
  };

  const STRENGTH_LABELS: Record<string, string> = {
    heavy_buy: '强烈买入', moderate_buy: '适度买入', light_buy: '轻仓买入',
    hold: '观望', light_sell: '轻仓减仓', moderate_sell: '适度减仓', heavy_sell: '强烈减仓',
  };

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h5">历史报告</Typography>
        <TextField
          label="筛选日期"
          type="date"
          value={filterDate}
          onChange={(e) => setFilterDate(e.target.value)}
          size="small"
          InputLabelProps={{ shrink: true }}
        />
      </Box>

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>日期</TableCell>
              <TableCell>基金代码</TableCell>
              <TableCell>基金名称</TableCell>
              <TableCell>评分</TableCell>
              <TableCell>信号</TableCell>
              <TableCell>强度</TableCell>
              <TableCell>操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {results.map((r) => (
              <TableRow key={r.id} hover>
                <TableCell>{r.analysis_date}</TableCell>
                <TableCell>{r.fund_code}</TableCell>
                <TableCell>{r.fund_name}</TableCell>
                <TableCell>{r.weighted_score}</TableCell>
                <TableCell>
                  <SignalIndicator direction={r.signal_direction} size={10} showLabel />
                </TableCell>
                <TableCell>
                  <Chip label={STRENGTH_LABELS[r.signal_strength] || r.signal_strength} size="small" />
                </TableCell>
                <TableCell>
                  <Button size="small" startIcon={<ViewIcon />} onClick={() => handleViewDetail(r)}>详情</Button>
                </TableCell>
              </TableRow>
            ))}
            {results.length === 0 && (
              <TableRow><TableCell colSpan={7} align="center">暂无历史报告</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* 详情弹窗 */}
      <Dialog open={detailOpen} onClose={() => setDetailOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>分析详情 — {selectedResult?.fund_name} ({selectedResult?.fund_code})</DialogTitle>
        <DialogContent>
          {selectedResult && (
            <Box sx={{ mt: 1 }}>
              <Typography variant="subtitle1" gutterBottom>
                日期: {selectedResult.analysis_date} | 评分: {selectedResult.weighted_score}
              </Typography>
              <Typography variant="subtitle1" gutterBottom>
                信号: <SignalIndicator direction={selectedResult.signal_direction} size={10} /> |
                强度: {selectedResult.signal_strength}
              </Typography>
              <Typography variant="subtitle1" gutterBottom sx={{ fontWeight: 600 }}>
                操作建议: {selectedResult.operation_advice}
              </Typography>

              <Typography variant="h6" sx={{ mt: 2, mb: 1 }}>因子评分</Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>因子</TableCell>
                    <TableCell>原始值</TableCell>
                    <TableCell>评分(0-5)</TableCell>
                    <TableCell>方向</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {selectedResult.factor_scores.map((fs, idx) => (
                    <TableRow key={idx}>
                      <TableCell>{fs.factor_name}</TableCell>
                      <TableCell>{fs.raw_value}</TableCell>
                      <TableCell>{fs.score}</TableCell>
                      <TableCell>{fs.direction === 'positive' ? '正向' : '反向'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDetailOpen(false)}>关闭</Button>
        </DialogActions>
      </Dialog>

      <Snackbar open={snackbar.open} autoHideDuration={3000} onClose={() => setSnackbar({ ...snackbar, open: false })}>
        <Alert severity={snackbar.severity}>{snackbar.message}</Alert>
      </Snackbar>
    </Box>
  );
};

export default HistoryReports;
