/**
 * 系统设置页面
 */
import React, { useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Card,
  CardContent,
  LinearProgress,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Alert,
  Snackbar,
} from '@mui/material';
import { Refresh as RefreshIcon, CheckCircle, Error as ErrorIcon } from '@mui/icons-material';
import { systemApi } from '../api/system';
import type { ConnectivityResult } from '../types';

const SystemPage: React.FC = () => {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ConnectivityResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runTest = async () => {
    setTesting(true);
    setError(null);
    try {
      const res = await systemApi.testConnectivity();
      if (res.data) {
        setResult(res.data);
      } else {
        setError('服务器返回了空数据');
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || '连通性测试失败');
      setResult(null);
    } finally {
      setTesting(false);
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h5" sx={{ mb: 3 }}>系统设置</Typography>

      {/* 连通性测试 */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
            <Typography variant="h6">数据源连通性测试</Typography>
            <Button
              variant="contained"
              startIcon={<RefreshIcon />}
              onClick={runTest}
              disabled={testing}
            >
              {testing ? '测试中...' : '开始测试'}
            </Button>
          </Box>

          {testing && <LinearProgress sx={{ mb: 2 }} />}

          {result && (
            <>
              <Alert
                severity={result.status === 'ok' ? 'success' : result.status === 'partial' ? 'warning' : 'error'}
                sx={{ mb: 2 }}
              >
                测试完成：{result.summary.reachable}/{result.summary.total} 项可达
                {result.summary.unreachable > 0 && `，${result.summary.unreachable} 项不可达`}
              </Alert>

              <TableContainer component={Paper} variant="outlined">
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>数据源</TableCell>
                      <TableCell>状态</TableCell>
                      <TableCell>延迟</TableCell>
                      <TableCell>错误信息</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {result.results.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={4} align="center">暂无数据源配置</TableCell>
                      </TableRow>
                    )}
                    {result.results.map((item) => (
                      <TableRow key={item.name}>
                        <TableCell>{item.name}</TableCell>
                        <TableCell>
                          <Chip
                            size="small"
                            icon={item.reachable ? <CheckCircle /> : <ErrorIcon />}
                            label={item.reachable ? '可达' : '不可达'}
                            color={item.reachable ? 'success' : 'error'}
                          />
                        </TableCell>
                        <TableCell>
                          {item.latency_ms != null ? `${item.latency_ms} ms` : '-'}
                        </TableCell>
                        <TableCell sx={{ color: item.error ? 'error.main' : 'text.secondary' }}>
                          {item.error || '-'}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </>
          )}

          {!result && !testing && (
            <Typography variant="body2" color="text.secondary">
              点击"开始测试"检测各数据源的网络连通状态
            </Typography>
          )}
        </CardContent>
      </Card>

      <Snackbar open={!!error} autoHideDuration={5000} onClose={() => setError(null)}>
        <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>
      </Snackbar>
    </Box>
  );
};

export default SystemPage;
