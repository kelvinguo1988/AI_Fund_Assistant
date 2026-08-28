/**
 * 基金详情页面 — 先展示缓存数据，后台刷新后更新
 */

import React, { useState, useEffect, useRef } from 'react';
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
  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /** 组件卸载时清理轮询定时器 */
  useEffect(() => {
    return () => {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    };
  }, []);

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

  /** 后台刷新数据：触发后立即返回，前端轮询进度，完成后重载 */
  const refreshInBackground = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setError(null);

    const stopPolling = () => {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    };

    // 轮询刷新进度，完成后重载详情
    const startPolling = () => {
      refreshTimerRef.current = setInterval(async () => {
        try {
          const st = await fundApi.refreshDetailsStatus();
          const s = st.data;
          if (!s) return;
          if (s.status === 'running') {
            setRefreshStatus(
              s.message || `刷新中 ${s.done}/${s.total}`
            );
          } else if (s.status === 'done') {
            stopPolling();
            setRefreshing(false);
            if (s.updated_at) setUpdatedAt(s.updated_at);
            setRefreshStatus(`刷新完成: ${s.total} 只基金`);
            const updated = await fundApi.detail();
            if (updated.data) {
              setReturns(updated.data.funds || []);
              if (updated.data.updated_at) setUpdatedAt(updated.data.updated_at);
            }
          } else if (s.status === 'failed') {
            stopPolling();
            setRefreshing(false);
            setError('刷新详情失败: ' + (s.error || '未知错误'));
          }
        } catch (e) {
          // 轮询异常不打断后端任务，仅记录
          console.error('查询刷新进度失败', e);
        }
      }, 2000);
    };

    try {
      const res = await fundApi.refreshDetails();
      if (res.data?.already_running) {
        setRefreshStatus('刷新任务已在后台运行');
      } else {
        setRefreshStatus('已启动后台刷新...');
      }
      startPolling();
    } catch (err: any) {
      stopPolling();
      setRefreshing(false);
      console.error('刷新详情失败', err);
      setError('刷新详情失败: ' + (err.message || ''));
    }
  };

  // 首次加载：只展示缓存，不自动刷新
  useEffect(() => {
    loadCached();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 格式化更新时间为北京时间显示（与 Dashboard.formatRefreshTime 逻辑一致） */
  const formatTime = (iso: string | null) => {
    if (!iso) return '暂无';
    try {
      if (/[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso)) {
        const d = new Date(iso);
        if (!isNaN(d.getTime())) {
          return new Intl.DateTimeFormat('zh-CN', {
            timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
            day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
          }).format(d).split('/').join('-');
        }
      }
      const m = iso.match(/(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
      return m ? `${m[1]} ${m[2]} (北京时间)` : iso;
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
