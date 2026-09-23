/**
 * 调度计划页面
 */

import React, { useEffect, useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  IconButton,
  Switch,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  Snackbar,
  Alert,
} from '@mui/material';
import { Add as AddIcon, Edit as EditIcon, Delete as DeleteIcon } from '@mui/icons-material';
import { scheduleApi } from '../api/schedule';
import { pushApi } from '../api/push';
import type { ScheduleOut, ScheduleCreate, ScheduleUpdate, ScheduleTaskType, PushChannelOut } from '../types';
import ConfirmDialog from '../components/ConfirmDialog';

const TASK_TYPE_LABELS: Record<ScheduleTaskType, string> = {
  analysis_push: '分析推送',
  ai_daily_brief: 'AI 每日简报',
};

const SchedulePlan: React.FC = () => {
  const [schedules, setSchedules] = useState<ScheduleOut[]>([]);
  const [channels, setChannels] = useState<PushChannelOut[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editSchedule, setEditSchedule] = useState<ScheduleOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScheduleOut | null>(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' });

  const [formName, setFormName] = useState('');
  const [formTimePoint, setFormTimePoint] = useState('14:50');
  const [formCronExpr, setFormCronExpr] = useState('');
  const [formChannelId, setFormChannelId] = useState<number | null>(null);
  const [formEnabled, setFormEnabled] = useState(true);
  const [formTaskType, setFormTaskType] = useState<ScheduleTaskType>('analysis_push');

  const loadData = async () => {
    try {
      const [schedRes, chRes] = await Promise.all([scheduleApi.list(), pushApi.list()]);
      if (schedRes.data) setSchedules(schedRes.data);
      if (chRes.data) setChannels(chRes.data);
    } catch {
      setSnackbar({ open: true, message: '加载数据失败', severity: 'error' });
    }
  };

  useEffect(() => { loadData(); }, []);

  const handleOpenAdd = () => {
    setEditSchedule(null);
    setFormName(''); setFormTimePoint('14:50'); setFormCronExpr('');
    setFormChannelId(null); setFormEnabled(true); setFormTaskType('analysis_push');
    setDialogOpen(true);
  };

  const handleOpenEdit = (s: ScheduleOut) => {
    setEditSchedule(s);
    setFormName(s.name); setFormTimePoint(s.time_point || '');
    setFormCronExpr(s.cron_expr || ''); setFormChannelId(s.channel_id); setFormEnabled(s.enabled);
    setFormTaskType((s.task_type as ScheduleTaskType) || 'analysis_push');
    setDialogOpen(true);
  };

  const handleSave = async () => {
    try {
      if (editSchedule) {
        const data: ScheduleUpdate = {
          name: formName, time_point: formTimePoint || null,
          cron_expr: formCronExpr || null, channel_id: formChannelId, enabled: formEnabled,
          task_type: formTaskType,
        };
        await scheduleApi.update(editSchedule.id, data);
      } else {
        const data: ScheduleCreate = {
          name: formName, time_point: formTimePoint || null,
          cron_expr: formCronExpr || null, channel_id: formChannelId, enabled: formEnabled,
          task_type: formTaskType,
        };
        await scheduleApi.create(data);
      }
      setDialogOpen(false);
      loadData();
      setSnackbar({ open: true, message: '保存成功', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err.message || '保存失败', severity: 'error' });
    }
  };

  const handleToggle = async (s: ScheduleOut) => {
    try {
      await scheduleApi.update(s.id, { enabled: !s.enabled });
      loadData();
    } catch {
      setSnackbar({ open: true, message: '切换状态失败', severity: 'error' });
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await scheduleApi.delete(deleteTarget.id);
      setDeleteTarget(null);
      loadData();
      setSnackbar({ open: true, message: '删除成功', severity: 'success' });
    } catch {
      setSnackbar({ open: true, message: '删除失败', severity: 'error' });
    }
  };

  const getChannelName = (id: number | null) => {
    if (!id) return '-';
    const ch = channels.find((c) => c.id === id);
    return ch ? ch.name : `#${id}`;
  };

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 2 }}>
        <Typography variant="h5">调度计划</Typography>
        <Button variant="contained" startIcon={<AddIcon />} onClick={handleOpenAdd}>新增调度</Button>
      </Box>

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>名称</TableCell>
              <TableCell>类型</TableCell>
              <TableCell>触发时间</TableCell>
              <TableCell>Cron</TableCell>
              <TableCell>推送渠道</TableCell>
              <TableCell>上次运行</TableCell>
              <TableCell>启用</TableCell>
              <TableCell>操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {schedules.map((s) => (
              <TableRow key={s.id} hover>
                <TableCell>{s.name}</TableCell>
                <TableCell>
                  <Chip size="small" variant={s.task_type === 'ai_daily_brief' ? 'filled' : 'outlined'}
                    color={s.task_type === 'ai_daily_brief' ? 'secondary' : 'default'}
                    label={TASK_TYPE_LABELS[s.task_type as ScheduleTaskType] || s.task_type} />
                </TableCell>
                <TableCell><Chip label={s.time_point || '-'} size="small" /></TableCell>
                <TableCell><Chip label={s.cron_expr || '-'} size="small" variant="outlined" /></TableCell>
                <TableCell>{getChannelName(s.channel_id)}</TableCell>
                <TableCell>{s.last_run_at || '-'}</TableCell>
                <TableCell><Switch checked={s.enabled} onChange={() => handleToggle(s)} size="small" /></TableCell>
                <TableCell>
                  <IconButton size="small" onClick={() => handleOpenEdit(s)}><EditIcon fontSize="small" /></IconButton>
                  <IconButton size="small" color="error" onClick={() => setDeleteTarget(s)}><DeleteIcon fontSize="small" /></IconButton>
                </TableCell>
              </TableRow>
            ))}
            {schedules.length === 0 && (
              <TableRow><TableCell colSpan={8} align="center">暂无调度计划</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editSchedule ? '编辑调度' : '新增调度'}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="调度名称" value={formName} onChange={(e) => setFormName(e.target.value)} />
          <TextField label="任务类型" value={formTaskType} onChange={(e) => setFormTaskType(e.target.value as ScheduleTaskType)} select
            helperText={formTaskType === 'ai_daily_brief'
              ? 'AI 每日简报：不跑全量分析，Agent 解读信号概况/调仓摘要/市场环境生成简报并推送（需已配置 AI Key；失败静默）'
              : '分析推送：全量多因子分析 + 按报告配置推送'}
          >
            {(Object.keys(TASK_TYPE_LABELS) as ScheduleTaskType[]).map((k) => (
              <MenuItem key={k} value={k}>{TASK_TYPE_LABELS[k]}</MenuItem>
            ))}
          </TextField>
          <TextField label="固定时间(HH:MM)" value={formTimePoint} onChange={(e) => setFormTimePoint(e.target.value)} placeholder="14:50" />
          <TextField label="Cron表达式(可选)" value={formCronExpr} onChange={(e) => setFormCronExpr(e.target.value)} placeholder="50 14 * * mon-fri" />
          <TextField label="推送渠道" value={formChannelId ?? ''} onChange={(e) => setFormChannelId(e.target.value ? parseInt(e.target.value) : null)} select>
            <MenuItem value="">不推送</MenuItem>
            {channels.map((ch) => <MenuItem key={ch.id} value={ch.id}>{ch.name} ({ch.channel_type})</MenuItem>)}
          </TextField>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>取消</Button>
          <Button variant="contained" onClick={handleSave}>保存</Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={!!deleteTarget}
        title="确认删除"
        message={`确定要删除调度 ${deleteTarget?.name} 吗？`}
        confirmColor="error"
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />

      <Snackbar open={snackbar.open} autoHideDuration={3000} onClose={() => setSnackbar({ ...snackbar, open: false })}>
        <Alert severity={snackbar.severity}>{snackbar.message}</Alert>
      </Snackbar>
    </Box>
  );
};

export default SchedulePlan;
