/**
 * 全局错误告警铃铛 — 顶栏右上角
 *
 * 60s 轮询未读错误数（localStorage 记录上次查看时间），徽标显示数量；
 * 点击弹出最近错误列表（分类 Chip 着色，限流/封禁红色最醒目）；
 * 支持直接下载日志、一键标记已读。
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  IconButton,
  Badge,
  Popover,
  List,
  ListItem,
  ListItemText,
  Typography,
  Button,
  Box,
  Chip,
} from '@mui/material';
import NotificationsIcon from '@mui/icons-material/Notifications';
import { errorLogApi, CATEGORY_META, type ErrorLogItem } from '../api/errorLog';

const SINCE_KEY = 'error_log_last_seen';

const SINCE_KEY_READ = (): string => localStorage.getItem(SINCE_KEY) || '';

const ErrorBell: React.FC = () => {
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<ErrorLogItem[]>([]);
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshCount = useCallback(async () => {
    try {
      const res = await errorLogApi.count(SINCE_KEY_READ());
      setUnread(res.data?.count ?? 0);
    } catch {
      // 静默：告警组件自身失败不打扰主流程
    }
  }, []);

  const loadList = useCallback(async () => {
    try {
      const res = await errorLogApi.list(10);
      setItems(res.data || []);
    } catch {
      setItems([]);
    }
  }, []);

  useEffect(() => {
    refreshCount();
    pollRef.current = setInterval(refreshCount, 60_000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [refreshCount]);

  const handleOpen = async (e: React.MouseEvent<HTMLElement>) => {
    setAnchorEl(e.currentTarget);
    await loadList();
  };

  const markRead = () => {
    localStorage.setItem(SINCE_KEY, new Date().toLocaleString('sv-SE').replace(' ', 'T'));
    setUnread(0);
  };

  const handleDownload = async () => {
    try {
      await errorLogApi.download();
    } catch {
      // 下载失败静默（Popover 内不便弹错误）
    }
  };

  return (
    <>
      <IconButton color="inherit" onClick={handleOpen} title="系统错误告警">
        <Badge badgeContent={unread} color="error" max={99}>
          <NotificationsIcon />
        </Badge>
      </IconButton>
      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        slotProps={{ paper: { sx: { width: 420, maxHeight: 480 } } }}
      >
        <Box sx={{ p: 1.5, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography variant="subtitle2">系统错误告警</Typography>
          <Box>
            <Button size="small" onClick={handleDownload}>下载日志</Button>
            <Button size="small" onClick={() => { markRead(); setUnread(0); }}>标记已读</Button>
          </Box>
        </Box>
        <List dense sx={{ maxHeight: 340, overflow: 'auto', pt: 0 }}>
          {items.map((it) => {
            const meta = CATEGORY_META[it.category] || CATEGORY_META.other;
            return (
              <ListItem key={it.id} alignItems="flex-start" divider>
                <ListItemText
                  primary={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Chip
                        size="small" label={meta.label} color={meta.color}
                        sx={{ height: 20, fontSize: '0.7rem' }}
                      />
                      <Typography variant="caption" color="text.secondary">{it.ts}</Typography>
                    </Box>
                  }
                  secondary={
                    <>
                      <Typography variant="caption" color="text.secondary" component="span">
                        [{it.module}]
                      </Typography>{' '}
                      <Typography variant="body2" component="span">{it.message}</Typography>
                    </>
                  }
                />
              </ListItem>
            );
          })}
          {items.length === 0 && (
            <ListItem>
              <ListItemText primary="暂无错误记录" primaryTypographyProps={{ color: 'text.secondary' }} />
            </ListItem>
          )}
        </List>
        {unread > 0 && (
          <Box sx={{ p: 1, textAlign: 'center' }}>
            <Typography variant="caption" color="error">
              {unread} 条未读错误 — 详情与下载见「系统设置 → 错误日志」
            </Typography>
          </Box>
        )}
      </Popover>
    </>
  );
};

export default ErrorBell;
