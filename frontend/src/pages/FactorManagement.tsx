/**
 * 因子管理页面
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
  Slider,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  Switch,
  Snackbar,
  Alert,
  Tooltip,
} from '@mui/material';
import { Add as AddIcon, Edit as EditIcon, Delete as DeleteIcon } from '@mui/icons-material';
import { factorApi } from '../api/factor';
import type { FactorOut, FactorCreate, FactorUpdate } from '../types';
import ConfirmDialog from '../components/ConfirmDialog';

const DIRECTION_OPTIONS = [
  { value: 'positive', label: '正向（越高越好）' },
  { value: 'negative', label: '反向（越低越好）' },
];

const NORMALIZATION_OPTIONS = [
  { value: 'none', label: '无' },
  { value: 'cross_sectional_zscore', label: '截面 Z-score' },
  { value: 'rolling_percentile', label: '滚动百分位' },
];

const NORM_LABELS: Record<string, string> = {
  none: '无',
  cross_sectional_zscore: 'Z-score',
  rolling_percentile: '百分位',
};

const FactorManagement: React.FC = () => {
  const [factors, setFactors] = useState<FactorOut[]>([]);
  const [_loading, setLoading] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editFactor, setEditFactor] = useState<FactorOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FactorOut | null>(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' });

  // 表单
  const [formName, setFormName] = useState('');
  const [formCode, setFormCode] = useState('');
  const [formWeight, setFormWeight] = useState(1.0);
  const [formDirection, setFormDirection] = useState<'positive' | 'negative'>('positive');
  const [formFormula, setFormFormula] = useState('');
  const [formWindow, setFormWindow] = useState<number | null>(null);
  const [formWindowUnit, setFormWindowUnit] = useState<'day' | 'quarter'>('day');
  const [formNormalization, setFormNormalization] = useState('none');

  const loadFactors = async () => {
    setLoading(true);
    try {
      const res = await factorApi.list();
      if (res.data) {
        const totalWeight = res.data.reduce((sum, f) => sum + f.weight, 0);
        const enriched = res.data.map((f) => ({
          ...f,
          weight_percentage: totalWeight > 0 ? Math.round(f.weight / totalWeight * 100 * 100) / 100 : 0,
        }));
        setFactors(enriched);
      }
    } catch {
      setSnackbar({ open: true, message: '加载因子列表失败', severity: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadFactors(); }, []);

  const handleOpenAdd = () => {
    setEditFactor(null);
    setFormName(''); setFormCode(''); setFormWeight(1.0);
    setFormDirection('positive'); setFormFormula(''); setFormWindow(null);
    setFormWindowUnit('day'); setFormNormalization('none');
    setDialogOpen(true);
  };

  const handleOpenEdit = (factor: FactorOut) => {
    setEditFactor(factor);
    setFormName(factor.name); setFormCode(factor.code);
    setFormWeight(factor.weight); setFormDirection(factor.direction as 'positive' | 'negative');
    setFormFormula(factor.formula || ''); setFormWindow(factor.window);
    setFormWindowUnit((factor.window_unit as 'day' | 'quarter') || 'day');
    setFormNormalization(factor.normalization || 'none');
    setDialogOpen(true);
  };

  const handleSave = async () => {
    try {
      if (editFactor) {
        const data: FactorUpdate = {
          name: formName, weight: formWeight, direction: formDirection,
          formula: formFormula || null,
          window: formWindow, window_unit: formWindowUnit,
          normalization: formNormalization,
        };
        await factorApi.update(editFactor.id, data);
      } else {
        const data: FactorCreate = {
          name: formName, code: formCode, weight: formWeight,
          direction: formDirection, formula: formFormula || null,
          window: formWindow, window_unit: formWindowUnit,
          normalization: formNormalization,
          sort_order: 0,
        };
        await factorApi.create(data);
      }
      setDialogOpen(false);
      loadFactors();
      setSnackbar({ open: true, message: '保存成功', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err.message || '保存失败', severity: 'error' });
    }
  };

  const handleWeightChange = async (factorId: number, newWeight: number) => {
    try {
      await factorApi.update(factorId, { weight: newWeight });
      loadFactors();
    } catch {
      setSnackbar({ open: true, message: '更新权重失败', severity: 'error' });
    }
  };

  const handleToggleStatus = async (factor: FactorOut) => {
    const newStatus = factor.status === 'active' ? 'disabled' : 'active';
    try {
      await factorApi.update(factor.id, { status: newStatus as any });
      loadFactors();
    } catch {
      setSnackbar({ open: true, message: '切换状态失败', severity: 'error' });
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await factorApi.delete(deleteTarget.id);
      setDeleteTarget(null);
      loadFactors();
      setSnackbar({ open: true, message: '删除成功', severity: 'success' });
    } catch {
      setSnackbar({ open: true, message: '删除失败', severity: 'error' });
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 2 }}>
        <Typography variant="h5">因子管理</Typography>
        <Button variant="contained" startIcon={<AddIcon />} onClick={handleOpenAdd}>新增因子</Button>
      </Box>

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>名称</TableCell>
              <TableCell>代码</TableCell>
              <TableCell>方向</TableCell>
              <TableCell>公式</TableCell>
              <TableCell>窗口</TableCell>
              <TableCell>标准化</TableCell>
              <TableCell sx={{ minWidth: 150 }}>权重</TableCell>
              <TableCell>占比</TableCell>
              <TableCell>状态</TableCell>
              <TableCell>操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {factors.map((f) => (
              <TableRow key={f.id} hover>
                <TableCell>{f.name}</TableCell>
                <TableCell><Chip label={f.code} size="small" /></TableCell>
                <TableCell>{f.direction === 'positive' ? '正向' : '反向'}</TableCell>
                <TableCell>
                  <Tooltip title={f.formula || ''}>
                    <Typography variant="body2" sx={{ maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {f.formula || '-'}
                    </Typography>
                  </Tooltip>
                </TableCell>
                <TableCell>{f.window ? `${f.window}${f.window_unit === 'quarter' ? '季' : '日'}` : '-'}</TableCell>
                <TableCell>{NORM_LABELS[f.normalization] || '无'}</TableCell>
                <TableCell>
                  <Slider
                    value={f.weight}
                    min={0} max={3} step={0.1}
                    valueLabelDisplay="auto"
                    onChange={(_, v) => handleWeightChange(f.id, v as number)}
                  />
                </TableCell>
                <TableCell>{f.weight_percentage}%</TableCell>
                <TableCell>
                  <Switch checked={f.status === 'active'} onChange={() => handleToggleStatus(f)} size="small" />
                </TableCell>
                <TableCell>
                  <IconButton size="small" onClick={() => handleOpenEdit(f)}><EditIcon fontSize="small" /></IconButton>
                  <IconButton size="small" color="error" onClick={() => setDeleteTarget(f)}><DeleteIcon fontSize="small" /></IconButton>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editFactor ? '编辑因子' : '新增因子'}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="因子名称" value={formName} onChange={(e) => setFormName(e.target.value)} />
          <TextField label="因子代码" value={formCode} onChange={(e) => setFormCode(e.target.value)} disabled={!!editFactor} />
          <TextField label="权重" type="number" value={formWeight} onChange={(e) => setFormWeight(parseFloat(e.target.value) || 1)} inputProps={{ min: 0, max: 5, step: 0.1 }} />
          <TextField label="方向" value={formDirection} onChange={(e) => setFormDirection(e.target.value as any)} select>
            {DIRECTION_OPTIONS.map((d) => <MenuItem key={d.value} value={d.value}>{d.label}</MenuItem>)}
          </TextField>
          <TextField label="计算公式" value={formFormula} onChange={(e) => setFormFormula(e.target.value)} placeholder="percentile_rank(pe, 1250)" helperText="可选的表达式" />
          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField label="窗口大小" type="number" value={formWindow ?? ''} onChange={(e) => setFormWindow(e.target.value ? parseInt(e.target.value) : null)} sx={{ width: 120 }} />
            <TextField label="单位" value={formWindowUnit} onChange={(e) => setFormWindowUnit(e.target.value as any)} select sx={{ width: 120 }}>
              <MenuItem value="day">日</MenuItem>
              <MenuItem value="quarter">季</MenuItem>
            </TextField>
          </Box>
          <TextField label="标准化方式" value={formNormalization} onChange={(e) => setFormNormalization(e.target.value)} select>
            {NORMALIZATION_OPTIONS.map((o) => <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>)}
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
        message={`确定要删除因子 ${deleteTarget?.name} 吗？`}
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

export default FactorManagement;
