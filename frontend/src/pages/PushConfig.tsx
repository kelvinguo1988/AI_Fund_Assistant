/**
 * 推送配置页面
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
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  Snackbar,
  Alert,
} from '@mui/material';
import { Add as AddIcon, Edit as EditIcon, Delete as DeleteIcon, Send as TestIcon } from '@mui/icons-material';
import { pushApi } from '../api/push';
import type { PushChannelOut, PushChannelCreate, PushChannelUpdate } from '../types';
import ConfirmDialog from '../components/ConfirmDialog';

const CHANNEL_TYPES = [
  { value: 'feishu', label: '飞书' },
  { value: 'qq', label: 'QQ' },
];

const PushConfig: React.FC = () => {
  const [channels, setChannels] = useState<PushChannelOut[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editChannel, setEditChannel] = useState<PushChannelOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PushChannelOut | null>(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' });

  const [formName, setFormName] = useState('');
  const [formType, setFormType] = useState<'feishu' | 'qq'>('feishu');
  const [formWebhook, setFormWebhook] = useState('');
  const [formToken, setFormToken] = useState('');
  const [formEnabled, setFormEnabled] = useState(true);

  const loadChannels = async () => {
    try {
      const res = await pushApi.list();
      if (res.data) setChannels(res.data);
    } catch {
      setSnackbar({ open: true, message: '加载推送渠道失败', severity: 'error' });
    }
  };

  useEffect(() => { loadChannels(); }, []);

  const handleOpenAdd = () => {
    setEditChannel(null);
    setFormName(''); setFormType('feishu'); setFormWebhook(''); setFormToken(''); setFormEnabled(true);
    setDialogOpen(true);
  };

  const handleOpenEdit = (ch: PushChannelOut) => {
    setEditChannel(ch);
    setFormName(ch.name); setFormType(ch.channel_type as any);
    setFormWebhook(ch.webhook_url || ''); setFormToken(ch.token || ''); setFormEnabled(ch.enabled);
    setDialogOpen(true);
  };

  const handleSave = async () => {
    try {
      if (editChannel) {
        const data: PushChannelUpdate = {
          name: formName, channel_type: formType, webhook_url: formWebhook, token: formToken, enabled: formEnabled,
        };
        await pushApi.update(editChannel.id, data);
      } else {
        const data: PushChannelCreate = {
          name: formName, channel_type: formType, webhook_url: formWebhook, token: formToken, enabled: formEnabled,
        };
        await pushApi.create(data);
      }
      setDialogOpen(false);
      loadChannels();
      setSnackbar({ open: true, message: '保存成功', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err.message || '保存失败', severity: 'error' });
    }
  };

  const handleToggle = async (ch: PushChannelOut) => {
    try {
      await pushApi.update(ch.id, { enabled: !ch.enabled });
      loadChannels();
    } catch {
      setSnackbar({ open: true, message: '切换状态失败', severity: 'error' });
    }
  };

  const handleTest = async (ch: PushChannelOut) => {
    try {
      await pushApi.test(ch.id);
      setSnackbar({ open: true, message: '测试推送已发送', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err.message || '测试推送失败', severity: 'error' });
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await pushApi.delete(deleteTarget.id);
      setDeleteTarget(null);
      loadChannels();
      setSnackbar({ open: true, message: '删除成功', severity: 'success' });
    } catch {
      setSnackbar({ open: true, message: '删除失败', severity: 'error' });
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 2 }}>
        <Typography variant="h5">推送配置</Typography>
        <Button variant="contained" startIcon={<AddIcon />} onClick={handleOpenAdd}>新增渠道</Button>
      </Box>

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>名称</TableCell>
              <TableCell>类型</TableCell>
              <TableCell>Webhook</TableCell>
              <TableCell>启用</TableCell>
              <TableCell>操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {channels.map((ch) => (
              <TableRow key={ch.id} hover>
                <TableCell>{ch.name}</TableCell>
                <TableCell>{ch.channel_type === 'feishu' ? '飞书' : 'QQ'}</TableCell>
                <TableCell sx={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>{ch.webhook_url || '-'}</TableCell>
                <TableCell><Switch checked={ch.enabled} onChange={() => handleToggle(ch)} size="small" /></TableCell>
                <TableCell>
                  <IconButton size="small" onClick={() => handleTest(ch)} title="测试推送"><TestIcon fontSize="small" /></IconButton>
                  <IconButton size="small" onClick={() => handleOpenEdit(ch)}><EditIcon fontSize="small" /></IconButton>
                  <IconButton size="small" color="error" onClick={() => setDeleteTarget(ch)}><DeleteIcon fontSize="small" /></IconButton>
                </TableCell>
              </TableRow>
            ))}
            {channels.length === 0 && (
              <TableRow><TableCell colSpan={5} align="center">暂无推送渠道</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editChannel ? '编辑渠道' : '新增渠道'}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="渠道名称" value={formName} onChange={(e) => setFormName(e.target.value)} />
          <TextField label="渠道类型" value={formType} onChange={(e) => setFormType(e.target.value as any)} select>
            {CHANNEL_TYPES.map((t) => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
          </TextField>
          <TextField label="Webhook URL" value={formWebhook} onChange={(e) => setFormWebhook(e.target.value)} />
          <TextField label="Secret/Token" value={formToken} onChange={(e) => setFormToken(e.target.value)} type="password" />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>取消</Button>
          <Button variant="contained" onClick={handleSave}>保存</Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={!!deleteTarget}
        title="确认删除"
        message={`确定要删除渠道 ${deleteTarget?.name} 吗？`}
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

export default PushConfig;
