/**
 * 基金详情页面 — 先展示缓存数据，后台刷新后更新
 */

import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Button,
  Alert,
  Chip,
  CircularProgress,
} from '@mui/material';
import { Refresh as RefreshIcon } from '@mui/icons-material';
import { fundApi } from '../api/fund';
import FundDetailPanel from '../components/FundDetailPanel';
import type { FundPeriodReturn } from '../types';

const FundDetailPage: React.FC = () => {
  const [returns, setReturns] = useState<FundPeriodReturn[]>([]);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshStatus, setRefreshStatus] = useState<string>('');

  /** 加载缓存数据 */
  const loadCached = async () => {
    try {
      const res = await fundApi.detail();
      if (res.data) {
        setReturns(res.data.funds || []);
        setUpdatedAt(res.data.updated_at || null);
      }
    } catch (err) {
      console.error('加载基金详情失败', err);
      setError('加载基金详情失败');
    } finally {
      setLoading(false);
    }
  };

  /** 后台刷新数据 */
  const refreshInBackground = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshStatus('正在刷新阶段涨幅...');
    setError(null);
    try {
      // 先刷新阶段涨幅（较快），然后刷新持仓+经理（较慢）
      const res = await fundApi.refreshDetails();
      const r = res.data as any;
      const total = r?.total ?? 0;
      const errors = (r?.results ?? []).filter((x: any) => x.error).length;
      if (r?.updated_at) {
        setUpdatedAt(r.updated_at);
      }
      setRefreshStatus(`刷新完成: ${total} 只基金${errors > 0 ? `, ${errors} 只失败` : ''}`);

      // 重载最新数据
      const updated = await fundApi.detail();
      if (updated.data) {
        setReturns(updated.data.funds || []);
        if (updated.data.updated_at) {
          setUpdatedAt(updated.data.updated_at);
        }
      }
    } catch (err: any) {
      console.error('刷新详情失败', err);
      setError('刷新详情失败: ' + (err.message || ''));
    } finally {
      setRefreshing(false);
    }
  };

  // 首次加载：只展示缓存，不自动刷新
  useEffect(() => {
    loadCached();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const formatTime = (iso: string | null) => {
    if (!iso) return '暂无';
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      const bj = new Date(d.getTime() + 8 * 60 * 60 * 1000);
      const pad = (n: number) => String(n).padStart(2, '0');
      return `${bj.getUTCFullYear()}-${pad(bj.getUTCMonth() + 1)}-${pad(bj.getUTCDate())} ${pad(bj.getUTCHours())}:${pad(bj.getUTCMinutes())} (北京时间)`;
    } catch {
      return iso;
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      {/* 顶栏 */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Typography variant="h5">基金详情</Typography>
          <Chip size="small" label={`数据更新: ${formatTime(updatedAt)}`}
            variant="outlined" sx={{ fontSize: '0.75rem' }} />
          {refreshing && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <CircularProgress size={14} />
              <Typography variant="caption" color="text.secondary">{refreshStatus}</Typography>
            </Box>
          )}
        </Box>
        <Button size="small" variant="outlined" startIcon={<RefreshIcon />}
          disabled={refreshing} onClick={refreshInBackground}>
          {refreshing ? '刷新中...' : '刷新数据'}
        </Button>
      </Box>

      {/* 刷新结果提示 */}
      {refreshStatus && !refreshing && (
        <Alert severity="success" sx={{ mb: 1.5, py: 0, '& .MuiAlert-message': { py: 0.8 } }}>
          {refreshStatus}
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mb: 1.5 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* 主面板 */}
      <FundDetailPanel
        returns={returns}
        loading={loading}
        updatedAt={updatedAt}
      />
    </Box>
  );
};

export default FundDetailPage;
