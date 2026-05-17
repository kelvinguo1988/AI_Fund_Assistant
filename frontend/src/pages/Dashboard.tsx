/**
 * 仪表盘页面 — 今日信号概览
 */

import React, { useEffect, useState } from 'react';
import {
  Box,
  Grid,
  Card,
  CardContent,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Button,
  Chip,
  Snackbar,
  Alert,
} from '@mui/material';
import { Refresh as RefreshIcon } from '@mui/icons-material';
import SignalIndicator from '../components/SignalIndicator';
import ScoreGauge from '../components/ScoreGauge';
import FactorRadarChart from '../components/FactorRadarChart';
import { analysisApi } from '../api/analysis';
import type { AnalysisResultOut } from '../types';

const STRENGTH_COLOR_MAP: Record<string, 'error' | 'success' | 'default'> = {
  heavy_buy: 'error',
  moderate_buy: 'error',
  light_buy: 'error',
  hold: 'default',
  light_sell: 'success',
  moderate_sell: 'success',
  heavy_sell: 'success',
};

const Dashboard: React.FC = () => {
  const [results, setResults] = useState<AnalysisResultOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' });
  const [selectedFund, setSelectedFund] = useState<AnalysisResultOut | null>(null);

  const loadLatest = async () => {
    setLoading(true);
    try {
      const res = await analysisApi.latest();
      if (res.data) {
        setResults(res.data);
        if (res.data.length > 0 && !selectedFund) {
          setSelectedFund(res.data[0]);
        }
      }
    } catch (err: any) {
      setSnackbar({ open: true, message: '加载分析数据失败', severity: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadLatest();
  }, []);

  const handleTriggerAnalysis = async () => {
    setLoading(true);
    try {
      const res = await analysisApi.trigger();
      if (res.data) {
        setResults(res.data);
        setSnackbar({ open: true, message: '分析完成', severity: 'success' });
      }
    } catch (err: any) {
      setSnackbar({ open: true, message: '触发分析失败', severity: 'error' });
    } finally {
      setLoading(false);
    }
  };

  // 统计信号分布
  const buyCount = results.filter((r) => r.signal_direction === 'buy').length;
  const sellCount = results.filter((r) => r.signal_direction === 'sell').length;
  const holdCount = results.filter((r) => r.signal_direction === 'hold').length;

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h5">仪表盘</Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={loadLatest}
            disabled={loading}
          >
            刷新
          </Button>
          <Button
            variant="contained"
            onClick={handleTriggerAnalysis}
            disabled={loading}
          >
            手动触发分析
          </Button>
        </Box>
      </Box>

      {/* 信号概览卡片 */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={4}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="h6" sx={{ color: 'var(--signal-buy)' }}>买入信号</Typography>
              <Typography variant="h3" sx={{ color: 'var(--signal-buy)' }}>{buyCount}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={4}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="h6" sx={{ color: 'var(--signal-hold)' }}>观望持有</Typography>
              <Typography variant="h3" sx={{ color: 'var(--signal-hold)' }}>{holdCount}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={4}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="h6" sx={{ color: 'var(--signal-sell)' }}>卖出信号</Typography>
              <Typography variant="h3" sx={{ color: 'var(--signal-sell)' }}>{sellCount}</Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Grid container spacing={3}>
        {/* 基金列表 */}
        <Grid item xs={7}>
          <TableContainer component={Paper}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>基金代码</TableCell>
                  <TableCell>基金名称</TableCell>
                  <TableCell>评分(-6~+6)</TableCell>
                  <TableCell>权益仓位</TableCell>
                  <TableCell>信号</TableCell>
                  <TableCell>强度</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {results.map((r) => (
                  <TableRow
                    key={r.id}
                    hover
                    selected={selectedFund?.id === r.id}
                    onClick={() => setSelectedFund(r)}
                    sx={{ cursor: 'pointer' }}
                  >
                    <TableCell>{r.fund_code}</TableCell>
                    <TableCell>{r.fund_name}</TableCell>
                    <TableCell>{r.weighted_score}</TableCell>
                    <TableCell>{Math.round((r as any).equity_ratio * 100)}%</TableCell>
                    <TableCell>
                      <SignalIndicator direction={r.signal_direction} size={12} showLabel={false} />
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={r.signal_strength}
                        size="small"
                        color={STRENGTH_COLOR_MAP[r.signal_strength] || 'default'}
                      />
                    </TableCell>
                  </TableRow>
                ))}
                {results.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} align="center">暂无分析数据</TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </Grid>

        {/* 选中基金详情 */}
        <Grid item xs={5}>
          {selectedFund ? (
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  {selectedFund.fund_name} ({selectedFund.fund_code})
                </Typography>
                <ScoreGauge score={selectedFund.weighted_score} height={180} />
                <FactorRadarChart factorScores={selectedFund.factor_scores} height={250} />
                <Typography variant="body2" sx={{ mt: 1 }}>
                  {selectedFund.operation_advice}
                </Typography>
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent>
                <Typography color="text.secondary">点击左侧基金查看详情</Typography>
              </CardContent>
            </Card>
          )}
        </Grid>
      </Grid>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={3000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
      >
        <Alert severity={snackbar.severity}>{snackbar.message}</Alert>
      </Snackbar>
    </Box>
  );
};

export default Dashboard;
