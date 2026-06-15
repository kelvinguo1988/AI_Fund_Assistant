/**
 * 质量过滤配置页面 — 第零层参数可调
 */

import React, { useEffect, useState, useMemo } from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Grid,
  IconButton,
  InputAdornment,
  Snackbar,
  Alert,
  TextField,
  Tooltip,
  Typography,
  Divider,
} from '@mui/material';
import {
  Refresh as ResetIcon,
  Save as SaveIcon,
  RestartAlt as ResetAllIcon,
} from '@mui/icons-material';
import { systemApi } from '../api/system';
import type { QualityConfigParam } from '../types';

/* ── 类别顺序与中文映射 ── */
const CATEGORY_ORDER = [
  '前置否决-棺材钉',
  '前置否决-心电图',
  '前置否决-清盘',
  '因子修正',
  '动态阈值',
  '固定偏置',
];

const CATEGORY_ICONS: Record<string, string> = {
  '前置否决-棺材钉': '⚰️',
  '前置否决-心电图': '💓',
  '前置否决-清盘': '⚠️',
  '因子修正': '✏️',
  '动态阈值': '📐',
  '固定偏置': '➕',
};

/* ── 格式化数值显示 ── */
const fmtNum = (v: number): string => {
  if (Number.isInteger(v)) return String(v);
  // 保留至多4位小数，去掉尾部零
  return parseFloat(v.toFixed(4)).toString();
};

const QualityConfig: React.FC = () => {
  const [params, setParams] = useState<QualityConfigParam[]>([]);
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error',
  });

  /* ── 加载配置 ── */
  const loadConfig = async () => {
    setLoading(true);
    try {
      const res = await systemApi.getQualityConfig();
      if (res.data?.parameters) {
        setParams(res.data.parameters);
        const initVals: Record<string, string> = {};
        res.data.parameters.forEach((p) => {
          initVals[p.key] = fmtNum(p.value);
        });
        setEditValues(initVals);
        setUpdatedAt(res.data.updated_at ?? null);
      }
    } catch (err: any) {
      setSnackbar({ open: true, message: '加载配置失败', severity: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadConfig(); }, []);

  /* ── 是否有改动 ── */
  const dirtyKeys = useMemo(() => {
    const dirty = new Set<string>();
    params.forEach((p) => {
      const current = editValues[p.key] ?? fmtNum(p.value);
      if (current !== fmtNum(p.value)) dirty.add(p.key);
    });
    return dirty;
  }, [params, editValues]);

  const hasChanges = dirtyKeys.size > 0;

  /* ── 更新单个字段 ── */
  const handleChange = (key: string, raw: string) => {
    setEditValues((prev) => ({ ...prev, [key]: raw }));
  };

  /* ── 重置单个字段 ── */
  const handleResetOne = (p: QualityConfigParam) => {
    setEditValues((prev) => ({ ...prev, [p.key]: fmtNum(p.default_value) }));
  };

  /* ── 重置所有 ── */
  const handleResetAll = () => {
    const vals: Record<string, string> = {};
    params.forEach((p) => { vals[p.key] = fmtNum(p.default_value); });
    setEditValues(vals);
  };

  /* ── 保存 ── */
  const handleSave = async () => {
    setSaving(true);
    try {
      // 只发送 dirty 的参数
      const updates = Array.from(dirtyKeys).map((key) => {
        const raw = editValues[key] ?? '';
        const v = parseFloat(raw);
        return { key, value: isNaN(v) ? 0 : v };
      });

      await systemApi.updateQualityConfig({ parameters: updates });
      setSnackbar({ open: true, message: `已保存 ${updates.length} 个参数`, severity: 'success' });
      await loadConfig();
    } catch (err: any) {
      setSnackbar({ open: true, message: '保存失败: ' + (err.message || ''), severity: 'error' });
    } finally {
      setSaving(false);
    }
  };

  /* ── 分组参数 ── */
  const grouped = useMemo(() => {
    const map: Record<string, QualityConfigParam[]> = {};
    params.forEach((p) => {
      if (!map[p.category]) map[p.category] = [];
      map[p.category].push(p);
    });
    return map;
  }, [params]);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ p: 3 }}>
      {/* ── 页头 ── */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Typography variant="h5">质量过滤配置</Typography>
          <Chip
            size="small"
            label={updatedAt ? `上次更新: ${updatedAt.slice(0, 19)}` : '从未修改'}
            variant="outlined"
            sx={{ fontSize: '0.75rem' }}
          />
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant="outlined"
            startIcon={<ResetAllIcon />}
            onClick={handleResetAll}
          >
            全部恢复默认
          </Button>
          <Button
            variant="contained"
            startIcon={saving ? <CircularProgress size={14} /> : <SaveIcon />}
            onClick={handleSave}
            disabled={!hasChanges || saving}
          >
            {saving ? '保存中...' : `保存改动 (${dirtyKeys.size})`}
          </Button>
        </Box>
      </Box>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        修改以下参数后点击"保存改动"，新配置将在下次分析时生效。所有参数均有硬编码默认值，可随时恢复。
      </Typography>

      {/* ── 6 个分类卡片 ── */}
      <Grid container spacing={3}>
        {CATEGORY_ORDER.map((cat) => {
          const items = grouped[cat];
          if (!items) return null;
          return (
            <Grid item xs={12} md={6} key={cat}>
              <Card variant="outlined">
                <CardContent sx={{ pb: '16px !important' }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 'bold', mb: 1.5 }}>
                    {CATEGORY_ICONS[cat] ?? ''} {cat}
                  </Typography>
                  <Divider sx={{ mb: 2 }} />
                  {items.map((p) => {
                    const isDirty = dirtyKeys.has(p.key);
                    const currentVal = editValues[p.key] ?? fmtNum(p.value);
                    const isDefault = currentVal === fmtNum(p.default_value);
                    return (
                      <Box key={p.key} sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5 }}>
                        <TextField
                          size="small"
                          label={p.description}
                          value={currentVal}
                          onChange={(e) => handleChange(p.key, e.target.value)}
                          type="number"
                          inputProps={{ step: 'any' }}
                          sx={{ width: 160 }}
                          error={isDirty}
                          helperText={isDirty ? '已修改' : `默认: ${fmtNum(p.default_value)}`}
                          InputProps={{
                            endAdornment: !isDefault ? (
                              <InputAdornment position="end">
                                <Tooltip title="恢复默认值">
                                  <IconButton size="small" onClick={() => handleResetOne(p)}>
                                    <ResetIcon fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                              </InputAdornment>
                            ) : undefined,
                          }}
                        />
                      </Box>
                    );
                  })}
                </CardContent>
              </Card>
            </Grid>
          );
        })}
      </Grid>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={3000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
      >
        <Alert severity={snackbar.severity}>{snackbar.message}</Alert>
      </Snackbar>
    </Box>
  );
};

export default QualityConfig;
