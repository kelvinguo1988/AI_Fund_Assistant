/**
 * 基金池管理页面
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
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  Checkbox,
  Snackbar,
  Alert,
} from '@mui/material';
import { Add as AddIcon, Edit as EditIcon, Delete as DeleteIcon } from '@mui/icons-material';
import { fundApi } from '../api/fund';
import type { FundOut, FundCreate, FundUpdate } from '../types';
import ConfirmDialog from '../components/ConfirmDialog';

const FUND_TYPES = [
  { value: 'etf', label: 'ETF（场内）' },
  { value: 'otc', label: '场外基金' },
];

const FundPool: React.FC = () => {
  const [funds, setFunds] = useState<FundOut[]>([]);
  const [_loading, setLoading] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editFund, setEditFund] = useState<FundOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FundOut | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' });

  // 表单状态
  const [formCode, setFormCode] = useState('');
  const [formName, setFormName] = useState('');
  const [formType, setFormType] = useState<'etf' | 'otc'>('etf');
  const [formTags, setFormTags] = useState('');

  const loadFunds = async () => {
    setLoading(true);
    try {
      const res = await fundApi.list();
      if (res.data) setFunds(res.data);
    } catch {
      setSnackbar({ open: true, message: '加载基金列表失败', severity: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadFunds(); }, []);

  const handleOpenAdd = () => {
    setEditFund(null);
    setFormCode('');
    setFormName('');
    setFormType('etf');
    setFormTags('');
    setDialogOpen(true);
  };

  const handleOpenEdit = (fund: FundOut) => {
    setEditFund(fund);
    setFormCode(fund.code);
    setFormName(fund.name);
    setFormType(fund.fund_type as 'etf' | 'otc');
    setFormTags(fund.tags || '');
    setDialogOpen(true);
  };

  const handleSave = async () => {
    try {
      if (editFund) {
        const data: FundUpdate = { name: formName, fund_type: formType, tags: formTags || null };
        await fundApi.update(editFund.id, data);
        setSnackbar({ open: true, message: '更新成功', severity: 'success' });
      } else {
        const data: FundCreate = { code: formCode, name: formName, fund_type: formType, tags: formTags || null };
        await fundApi.create(data);
        setSnackbar({ open: true, message: '新增成功', severity: 'success' });
      }
      setDialogOpen(false);
      loadFunds();
    } catch (err: any) {
      setSnackbar({ open: true, message: err.message || '保存失败', severity: 'error' });
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await fundApi.delete(deleteTarget.id);
      setSnackbar({ open: true, message: '删除成功', severity: 'success' });
      setDeleteTarget(null);
      loadFunds();
    } catch {
      setSnackbar({ open: true, message: '删除失败', severity: 'error' });
    }
  };

  const handleBatchAction = async (action: 'active' | 'disabled') => {
    if (selected.length === 0) return;
    try {
      await fundApi.batchUpdate(selected, action);
      setSnackbar({ open: true, message: `批量${action === 'active' ? '启用' : '停用'}成功`, severity: 'success' });
      setSelected([]);
      loadFunds();
    } catch {
      setSnackbar({ open: true, message: '批量操作失败', severity: 'error' });
    }
  };

  const toggleSelect = (id: number) => {
    setSelected((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);
  };

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h5">基金池管理</Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          {selected.length > 0 && (
            <>
              <Button size="small" variant="outlined" onClick={() => handleBatchAction('active')}>批量启用</Button>
              <Button size="small" variant="outlined" color="warning" onClick={() => handleBatchAction('disabled')}>批量停用</Button>
            </>
          )}
          <Button variant="contained" startIcon={<AddIcon />} onClick={handleOpenAdd}>新增基金</Button>
        </Box>
      </Box>

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell padding="checkbox"></TableCell>
              <TableCell>代码</TableCell>
              <TableCell>名称</TableCell>
              <TableCell>类型</TableCell>
              <TableCell>标签</TableCell>
              <TableCell>状态</TableCell>
              <TableCell>操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {funds.map((fund) => (
              <TableRow key={fund.id} hover>
                <TableCell padding="checkbox">
                  <Checkbox checked={selected.includes(fund.id)} onChange={() => toggleSelect(fund.id)} size="small" />
                </TableCell>
                <TableCell>{fund.code}</TableCell>
                <TableCell>{fund.name}</TableCell>
                <TableCell>{fund.fund_type === 'etf' ? 'ETF' : '场外'}</TableCell>
                <TableCell>
                  {(fund.tags || '').split(',').filter(Boolean).map((tag) => (
                    <Chip key={tag} label={tag} size="small" sx={{ mr: 0.5 }} />
                  ))}
                </TableCell>
                <TableCell>
                  <Chip label={fund.status === 'active' ? '启用' : '停用'} size="small"
                    color={fund.status === 'active' ? 'success' : 'default'} />
                </TableCell>
                <TableCell>
                  <IconButton size="small" onClick={() => handleOpenEdit(fund)}><EditIcon fontSize="small" /></IconButton>
                  <IconButton size="small" color="error" onClick={() => setDeleteTarget(fund)}><DeleteIcon fontSize="small" /></IconButton>
                </TableCell>
              </TableRow>
            ))}
            {funds.length === 0 && (
              <TableRow><TableCell colSpan={7} align="center">暂无基金数据</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* 新增/编辑弹窗 */}
      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editFund ? '编辑基金' : '新增基金'}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="基金代码" value={formCode} onChange={(e) => setFormCode(e.target.value)}
            disabled={!!editFund} placeholder="6位数字代码如510300" />
          <TextField label="基金名称" value={formName} onChange={(e) => setFormName(e.target.value)} />
          <TextField label="基金类型" value={formType} onChange={(e) => setFormType(e.target.value as any)} select>
            {FUND_TYPES.map((t) => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
          </TextField>
          <TextField label="标签(逗号分隔)" value={formTags} onChange={(e) => setFormTags(e.target.value)} placeholder="宽基,大盘" />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>取消</Button>
          <Button variant="contained" onClick={handleSave}>保存</Button>
        </DialogActions>
      </Dialog>

      {/* 删除确认 */}
      <ConfirmDialog
        open={!!deleteTarget}
        title="确认删除"
        message={`确定要删除基金 ${deleteTarget?.name}(${deleteTarget?.code}) 吗？`}
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

export default FundPool;
