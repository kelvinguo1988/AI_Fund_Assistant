/**
 * 全局渲染错误边界
 *
 * React 18 下渲染期抛错会卸载整棵树 → 白屏且只剩控制台报错，
 * 用户完全不知道发生了什么。这里兜底成可读面板：显示出错组件的
 * 错误信息，提供「重试渲染」（不重载页面，保留已填表单）与「刷新页面」。
 */

import React from 'react';
import { Box, Button, Paper, Typography } from '@mui/material';

interface Props {
  children: React.ReactNode;
}

interface State {
  error: Error | null;
}

class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // 保留完整堆栈：浏览器控制台是排查前端崩溃的第一现场
    console.error('[ErrorBoundary] 渲染崩溃', error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <Box sx={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', p: 3 }}>
        <Paper elevation={3} sx={{ p: 3, maxWidth: 560, width: '100%' }}>
          <Typography variant="h6" color="error" gutterBottom>
            页面出错了
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {error.message || '未知错误'}
          </Typography>
          <Typography variant="caption" component="pre" sx={{
            display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            maxHeight: 160, overflow: 'auto', bgcolor: 'grey.100', p: 1, borderRadius: 1, mb: 2,
          }}>
            {(error.stack || '').split('\n').slice(1, 6).join('\n')}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button variant="outlined" size="small" onClick={() => this.setState({ error: null })}>
              重试渲染
            </Button>
            <Button variant="contained" size="small" onClick={() => window.location.reload()}>
              刷新页面
            </Button>
          </Box>
        </Paper>
      </Box>
    );
  }
}

export default ErrorBoundary;
