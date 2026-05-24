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
import { Add as AddIcon, Edit as EditIcon, Delete as DeleteIcon, Upload as UploadIcon, Refresh as RefreshIcon } from '@mui/icons-material';
import { fundApi } from '../api/fund';
import type { FundOut, FundCreate, FundUpdate } from '../types';
import ConfirmDialog from '../components/ConfirmDialog';

/** 主题标签颜色调色板：高区分度色值，同主题始终映射到同色 */
const THEME_COLORS = [
  '#1976D2', '#388E3C', '#F57C00', '#7B1FA2',
  '#C2185B', '#0097A7', '#E64A19', '#512DA8',
  '#00796B', '#D32F2F', '#FBC02D', '#5D4037',
];

const getThemeColor = (tag: string): string => {
  let hash = 0;
  for (let i = 0; i < tag.length; i++) {
    hash = hash * 31 + tag.charCodeAt(i);
    hash |= 0;
  }
  return THEME_COLORS[Math.abs(hash) % THEME_COLORS.length];
};

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

  // 批量导入状态
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [_importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{ total: number; created: number; skipped: string[]; errors: string[] } | null>(null);
  const [refreshingId, setRefreshingId] = useState<number | null>(null);

  // 表单状态
  const [formCode, setFormCode] = useState('');
  const [formName, setFormName] = useState('');
  const [formType, setFormType] = useState<'etf' | 'otc'>('etf');
  const [formTags, setFormTags] = useState('');

  const loadFunds = async () => {
    setLoading(true);
    try {
      const res = await fundApi.list();
      if (res.data) {
        setFunds(res.data);
        setSelected((prev) => prev.filter((id) => res.data!.some((f) => f.id === id)));
      }
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

  const allSelected = funds.length > 0 && funds.every((f) => selected.includes(f.id));
  const someSelected = selected.length > 0 && !allSelected;

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelected([]);
    } else {
      setSelected(funds.map((f) => f.id));
    }
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
          <Button variant="outlined" startIcon={<UploadIcon />} onClick={() => { setImportOpen(true); setImportResult(null); setImportText(''); }}>批量导入</Button>
          <Button variant="contained" startIcon={<AddIcon />} onClick={handleOpenAdd}>新增基金</Button>
        </Box>
      </Box>

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell padding="checkbox">
                <Checkbox
                  checked={allSelected}
                  indeterminate={someSelected}
                  onChange={toggleSelectAll}
                  size="small"
                />
              </TableCell>
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
                    <Chip key={tag} label={tag} size="small"
                      sx={{ backgroundColor: getThemeColor(tag), color: '#fff', mr: 0.5, mb: 0.3 }} />
                  ))}
                </TableCell>
                <TableCell>
                  <Chip label={fund.status === 'active' ? '启用' : '停用'} size="small"
                    color={fund.status === 'active' ? 'success' : 'default'} />
                </TableCell>
                <TableCell>
                  <IconButton size="small" title="刷新主题" disabled={refreshingId === fund.id}
                    onClick={async () => {
                      setRefreshingId(fund.id);
                      try {
                        await fundApi.refreshThemes(fund.id);
                        setSnackbar({ open: true, message: '主题刷新成功', severity: 'success' });
                        loadFunds();
                      } catch { setSnackbar({ open: true, message: '主题刷新失败', severity: 'error' }); }
                      finally { setRefreshingId(null); }
                    }}>
                    <RefreshIcon fontSize="small" />
                  </IconButton>
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
          <TextField label="标签(逗号分隔)" value={formTags} onChange={(e) => setFormTags(e.target.value)} placeholder="宽基,大盘"
            helperText="不填时系统将自动从天天基金抓取相关主题作为标签" />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>取消</Button>
          <Button variant="contained" onClick={handleSave}>保存</Button>
        </DialogActions>
      </Dialog>

      {/* 批量导入弹窗 */}
      <Dialog open={importOpen} onClose={() => setImportOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>批量导入基金</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <Typography variant="body2" color="text.secondary">
            每行一个基金，格式：<code>代码 名称 标签(可选)</code>。例如：<br />
            <code>510300 沪深300ETF 宽基,大盘</code><br />
            <code>018495 融通产业趋势臻选股票C</code><br />
            已有代码会被自动跳过。不填标签时系统将自动从天天基金抓取相关主题作为标签。
          </Typography>
          <TextField
            label="基金列表"
            multiline
            rows={10}
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            placeholder="510300 沪深300ETF 宽基,大盘&#10;018495 融通产业趋势臻选股票C"
            disabled={_importing}
          />
          {importResult && (
            <Alert severity={importResult.errors.length > 0 ? 'warning' : 'success'}>
              共 {importResult.total} 条，成功导入 {importResult.created} 条
              {importResult.skipped.length > 0 && `，跳过 ${importResult.skipped.length} 条（已存在）`}
              {importResult.errors.length > 0 && `，${importResult.errors.length} 条失败`}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setImportOpen(false)} disabled={_importing}>关闭</Button>
          <Button variant="contained" onClick={async () => {
            const lines = importText.split('\n').filter(Boolean);
            const items = lines.map((line) => {
              const parts = line.trim().split(/\s+/);
              return { code: parts[0], name: parts[1] || '', tags: parts.slice(2).join(',') || undefined };
            }).filter((item) => item.code);
            if (items.length === 0) return;
            setImporting(true);
            try {
              const res = await fundApi.batchImport(items);
              setImportResult(res.data || { total: items.length, created: 0, skipped: [], errors: [] });
              if (res.data && res.data.created > 0) loadFunds();
            } catch (err: any) {
              const detail = err?.response?.data?.detail || err?.message || '';
              console.error('批量导入失败:', detail, err);
              setSnackbar({ open: true, message: '批量导入失败' + (detail ? ': ' + detail : ''), severity: 'error' });
            } finally {
              setImporting(false);
            }
          }} disabled={_importing || !importText.trim()}>
            {_importing ? '导入中...' : '开始导入'}
          </Button>
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
