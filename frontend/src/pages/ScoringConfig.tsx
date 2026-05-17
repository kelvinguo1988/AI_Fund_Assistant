/**
 * 评分配置页面 — 五档阈值编辑
 */

import React, { useEffect, useState } from 'react';
import {
  Box, Typography, Button, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Paper, TextField,
  Snackbar, Alert, IconButton,
} from '@mui/material';
import { Add as AddIcon, Delete as DeleteIcon, Save as SaveIcon } from '@mui/icons-material';
import { systemApi } from '../api/system';
import type { ScoringTier } from '../types';

const SIGNAL_DIRECTIONS = ['buy', 'hold', 'sell'] as const;
const STRENGTH_OPTIONS = [
  { value: 'heavy_buy', label: '强烈加仓' },
  { value: 'moderate_buy', label: '适度加仓' },
  { value: 'hold', label: '中性' },
  { value: 'moderate_sell', label: '适度减仓' },
  { value: 'heavy_sell', label: '强烈减仓' },
];

const ScoringConfig: React.FC = () => {
  const [thresholds, setThresholds] = useState<ScoringTier[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' });

  const loadConfig = async () => {
    setLoading(true);
    try {
      const res = await systemApi.getScoringConfig();
      if (res.data) setThresholds(res.data.thresholds);
    } catch {
      setSnackbar({ open: true, message: '加载评分配置失败', severity: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadConfig(); }, []);

  const updateTier = (index: number, field: keyof ScoringTier, value: any) => {
    setThresholds((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], [field]: value };
      return next;
    });
  };

  const addTier = () => {
    setThresholds((prev) => [
      ...prev,
      {
        min_score: 0,
        label: '自定义档位',
        signal_direction: 'hold',
        signal_strength: 'hold',
        operation_advice: '综合评分 {score}，建议持有观望',
        equity_ratio: 0.5,
      },
    ]);
  };

  const removeTier = (index: number) => {
    setThresholds((prev) => prev.filter((_, i) => i !== index));
  };

  const handleSave = async () => {
    if (thresholds.length < 3) {
      setSnackbar({ open: true, message: '至少需要 3 个档位', severity: 'error' });
      return;
    }
    // 检查降序
    for (let i = 0; i < thresholds.length - 1; i++) {
      if (thresholds[i].min_score <= thresholds[i + 1].min_score) {
        setSnackbar({ open: true, message: '档位必须按 min_score 降序排列', severity: 'error' });
        return;
      }
    }
    setSaving(true);
    try {
      await systemApi.updateScoringConfig({ thresholds });
      setSnackbar({ open: true, message: '评分配置保存成功，下次分析时生效', severity: 'success' });
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || '保存失败';
      setSnackbar({ open: true, message: '保存失败: ' + msg, severity: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Box>
          <Typography variant="h5">评分配置</Typography>
          <Typography variant="body2" color="text.secondary">
            评分范围：-6.0 ~ +6.0，档位按 min_score 降序排列。数值越低的分档越靠后。
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button variant="outlined" startIcon={<AddIcon />} onClick={addTier} disabled={thresholds.length >= 8}>
            新增档位
          </Button>
          <Button variant="contained" startIcon={<SaveIcon />} onClick={handleSave} disabled={saving || loading}>
            {saving ? '保存中...' : '保存配置'}
          </Button>
        </Box>
      </Box>

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>档位</TableCell>
              <TableCell>min_score</TableCell>
              <TableCell>信号方向</TableCell>
              <TableCell>信号强度</TableCell>
              <TableCell>权益仓位</TableCell>
              <TableCell>操作建议模板</TableCell>
              <TableCell>操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {thresholds.map((tier, i) => (
              <TableRow key={i}>
                <TableCell>
                  <TextField size="small" value={tier.label}
                    onChange={(e) => updateTier(i, 'label', e.target.value)} sx={{ minWidth: 100 }} />
                </TableCell>
                <TableCell>
                  <TextField size="small" type="number" value={tier.min_score}
                    inputProps={{ step: 0.1, min: -6, max: 6 }}
                    onChange={(e) => updateTier(i, 'min_score', parseFloat(e.target.value) || 0)}
                    sx={{ width: 90 }} />
                </TableCell>
                <TableCell>
                  <TextField size="small" select value={tier.signal_direction}
                    onChange={(e) => updateTier(i, 'signal_direction', e.target.value)}
                    sx={{ width: 90 }} SelectProps={{ native: true }}>
                    {SIGNAL_DIRECTIONS.map((d) => (
                      <option key={d} value={d}>{d}</option>
                    ))}
                  </TextField>
                </TableCell>
                <TableCell>
                  <TextField size="small" select value={tier.signal_strength}
                    onChange={(e) => updateTier(i, 'signal_strength', e.target.value)}
                    sx={{ width: 130 }} SelectProps={{ native: true }}>
                    {STRENGTH_OPTIONS.map((s) => (
                      <option key={s.value} value={s.value}>{s.label}</option>
                    ))}
                  </TextField>
                </TableCell>
                <TableCell>
                  <TextField size="small" type="number" value={tier.equity_ratio}
                    inputProps={{ step: 0.05, min: 0, max: 1 }}
                    onChange={(e) => updateTier(i, 'equity_ratio', parseFloat(e.target.value) || 0)}
                    sx={{ width: 90 }} />
                </TableCell>
                <TableCell>
                  <TextField size="small" value={tier.operation_advice}
                    onChange={(e) => updateTier(i, 'operation_advice', e.target.value)}
                    sx={{ minWidth: 250 }} />
                </TableCell>
                <TableCell>
                  <IconButton size="small" color="error" onClick={() => removeTier(i)}
                    disabled={thresholds.length <= 3}>
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </TableCell>
              </TableRow>
            ))}
            {thresholds.length === 0 && (
              <TableRow><TableCell colSpan={7} align="center">暂无数据</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Box sx={{ mt: 2, p: 2, bgcolor: 'background.paper', borderRadius: 1 }}>
        <Typography variant="subtitle2" gutterBottom>预览：当前评分档位映射</Typography>
        {thresholds.map((tier, i) => {
          const nextMin = i < thresholds.length - 1 ? thresholds[i + 1].min_score : -Infinity;
          const rangeStr = nextMin > -Infinity
            ? `${tier.min_score} ~ ${nextMin}`
            : `≥ ${tier.min_score}`;
          return (
            <Typography key={i} variant="body2" color="text.secondary">
              {rangeStr} → {tier.label}（权益 {Math.round(tier.equity_ratio * 100)}%）
            </Typography>
          );
        })}
      </Box>

      <Snackbar open={snackbar.open} autoHideDuration={4000} onClose={() => setSnackbar({ ...snackbar, open: false })}>
        <Alert severity={snackbar.severity}>{snackbar.message}</Alert>
      </Snackbar>
    </Box>
  );
};

export default ScoringConfig;
