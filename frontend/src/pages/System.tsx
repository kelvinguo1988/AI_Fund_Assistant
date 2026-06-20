/**
 * 系统设置页面 — AI 配置 + 数据源连通性测试
 */
import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Button,
  Card,
  CardContent,
  LinearProgress,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Alert,
  Snackbar,
  TextField,
  MenuItem,
  Switch,
  FormControlLabel,
  Grid,
} from '@mui/material';
import {
  Refresh as RefreshIcon,
  CheckCircle,
  Error as ErrorIcon,
  Save as SaveIcon,
} from '@mui/icons-material';
import { systemApi } from '../api/system';
import type { ConnectivityResult, AIConfigOut } from '../types';

const SystemPage: React.FC = () => {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ConnectivityResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // AI 配置状态
  const [aiConfig, setAiConfig] = useState<AIConfigOut | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiSaving, setAiSaving] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState('');
  const [aiModel, setAiModel] = useState('');
  const [aiApiKey, setAiApiKey] = useState('');
  const [aiBaseUrl, setAiBaseUrl] = useState('');
  const [aiEnabled, setAiEnabled] = useState(true);

  // 加载 AI 配置
  useEffect(() => {
    setAiLoading(true);
    systemApi.getConfig()
      .then((res) => {
        const cfg = res.data;
        if (cfg) {
          setAiConfig(cfg);
          setAiEnabled(cfg.ai_enabled);
          setAiModel(cfg.ai_model);
          setAiBaseUrl(cfg.ai_base_url);
          // 匹配预设
          const matched = cfg.presets?.find((p) => p.key === cfg.ai_model);
          if (matched) setSelectedPreset(matched.key);
        }
      })
      .catch(() => setError('加载 AI 配置失败'))
      .finally(() => setAiLoading(false));
  }, []);

  // 选择预设模型时自动填充
  const handlePresetChange = (key: string) => {
    setSelectedPreset(key);
    const preset = aiConfig?.presets?.find((p) => p.key === key);
    if (preset) {
      setAiModel(preset.model_name);
      setAiBaseUrl(preset.base_url);
    }
  };

  // 保存 AI 配置
  const handleSaveAI = async () => {
    setAiSaving(true);
    try {
      const res = await systemApi.updateConfig({
        ai_enabled: aiEnabled,
        ai_model: aiModel,
        ai_api_key: aiApiKey || undefined,
        ai_base_url: aiBaseUrl,
      });
      if (res.data) {
        setAiConfig(res.data);
        setError(null);
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || '保存 AI 配置失败');
    } finally {
      setAiSaving(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setError(null);
    try {
      const res = await systemApi.testConnectivity();
      if (res.data) {
        setResult(res.data);
      } else {
        setError('服务器返回了空数据');
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || '连通性测试失败');
      setResult(null);
    } finally {
      setTesting(false);
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h5" sx={{ mb: 3 }}>系统设置</Typography>

      {/* ── AI 配置卡片 ── */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 2 }}>AI 模型配置</Typography>

          {aiLoading && <LinearProgress sx={{ mb: 2 }} />}

          <Box sx={{ mb: 2 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={aiEnabled}
                  onChange={(e) => setAiEnabled(e.target.checked)}
                  color="primary"
                />
              }
              label={aiEnabled ? 'AI 功能已启用' : 'AI 功能已停用'}
            />
          </Box>

          <Grid container spacing={2} sx={{ mb: 2 }}>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                select
                label="模型供应商"
                value={selectedPreset}
                onChange={(e) => handlePresetChange(e.target.value)}
                fullWidth
                size="small"
              >
                {aiConfig?.presets?.map((p) => (
                  <MenuItem key={p.key} value={p.key}>
                    {p.label}
                  </MenuItem>
                ))}
                <MenuItem value="">自定义</MenuItem>
              </TextField>
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                label="模型名称"
                value={aiModel}
                onChange={(e) => setAiModel(e.target.value)}
                placeholder="如 deepseek-chat"
                fullWidth
                size="small"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                label="API Key"
                type="password"
                value={aiApiKey}
                onChange={(e) => setAiApiKey(e.target.value)}
                placeholder="留空表示不修改"
                fullWidth
                size="small"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <TextField
                label="Base URL"
                value={aiBaseUrl}
                onChange={(e) => setAiBaseUrl(e.target.value)}
                placeholder="https://api.example.com/v1"
                fullWidth
                size="small"
              />
            </Grid>
          </Grid>

          <Button
            variant="contained"
            startIcon={aiSaving ? <LinearProgress sx={{ width: 18 }} /> : <SaveIcon />}
            onClick={handleSaveAI}
            disabled={aiSaving || aiLoading}
          >
            {aiSaving ? '保存中...' : '保存配置'}
          </Button>
        </CardContent>
      </Card>

      {/* ── 连通性测试 ── */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
            <Typography variant="h6">数据源连通性测试</Typography>
            <Button
              variant="contained"
              startIcon={<RefreshIcon />}
              onClick={runTest}
              disabled={testing}
            >
              {testing ? '测试中...' : '开始测试'}
            </Button>
          </Box>

          {testing && <LinearProgress sx={{ mb: 2 }} />}

          {result && (
            <>
              <Alert
                severity={result.status === 'ok' ? 'success' : result.status === 'partial' ? 'warning' : 'error'}
                sx={{ mb: 2 }}
              >
                测试完成：{result.summary.reachable}/{result.summary.total} 项可达
                {result.summary.unreachable > 0 && `，${result.summary.unreachable} 项不可达`}
              </Alert>

              <TableContainer component={Paper} variant="outlined">
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>数据源</TableCell>
                      <TableCell>状态</TableCell>
                      <TableCell>延迟</TableCell>
                      <TableCell>错误信息</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {result.results.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={4} align="center">暂无数据源配置</TableCell>
                      </TableRow>
                    )}
                    {result.results.map((item) => (
                      <TableRow key={item.name}>
                        <TableCell>{item.name}</TableCell>
                        <TableCell>
                          <Chip
                            size="small"
                            icon={item.reachable ? <CheckCircle /> : <ErrorIcon />}
                            label={item.reachable ? '可达' : '不可达'}
                            color={item.reachable ? 'success' : 'error'}
                          />
                        </TableCell>
                        <TableCell>
                          {item.latency_ms != null ? `${item.latency_ms} ms` : '-'}
                        </TableCell>
                        <TableCell sx={{ color: item.error ? 'error.main' : 'text.secondary' }}>
                          {item.error || '-'}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </>
          )}

          {!result && !testing && (
            <Typography variant="body2" color="text.secondary">
              点击"开始测试"检测各数据源的网络连通状态
            </Typography>
          )}
        </CardContent>
      </Card>

      <Snackbar open={!!error} autoHideDuration={5000} onClose={() => setError(null)}>
        <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>
      </Snackbar>
    </Box>
  );
};

export default SystemPage;
