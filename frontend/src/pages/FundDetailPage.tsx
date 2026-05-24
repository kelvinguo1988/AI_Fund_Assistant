/**
 * 基金详情页面 — 阶段涨幅 / 持仓明细 / 调仓变更 / 基金经理
 * 独立页面，与基金池管理分离
 */

import React, { useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Alert,
} from '@mui/material';
import { Refresh as RefreshIcon } from '@mui/icons-material';
import { fundApi } from '../api/fund';
import FundDetailPanel from '../components/FundDetailPanel';

const FundDetailPage: React.FC = () => {
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshResult, setRefreshResult] = useState<string | null>(null);

  const handleRefresh = async () => {
    setRefreshing(true);
    setError(null);
    setRefreshResult(null);
    try {
      const res = await fundApi.refreshDetails();
      const total = res.data?.total ?? 0;
      const errors = (res.data?.results ?? []).filter((r: any) => r.error).length;
      setRefreshResult(`刷新完成: ${total} 只基金, ${errors > 0 ? `${errors} 只失败` : '全部成功'}`);
    } catch {
      setError('刷新详情失败');
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h5">基金详情</Typography>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          {refreshResult && (
            <Typography variant="body2" color="success.main">{refreshResult}</Typography>
          )}
          <Button size="small" variant="outlined" startIcon={<RefreshIcon />}
            disabled={refreshing} onClick={handleRefresh}>
            {refreshing ? '刷新中...' : '刷新详情'}
          </Button>
        </Box>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      <FundDetailPanel />
    </Box>
  );
};

export default FundDetailPage;
