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
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';
import {
  Refresh as RefreshIcon,
  Delete as DeleteIcon,
  Add as AddIcon,
  Upload as UploadIcon,
  CheckCircle,
  Error as ErrorIcon,
  Save as SaveIcon,
} from '@mui/icons-material';
import { systemApi } from '../api/system';
import { errorLogApi, CATEGORY_META, type ErrorLogItem } from '../api/errorLog';
import { conceptMapApi, type ConceptMapProgress } from '../api/fund';
import { aiSkillApi, SKILL_EXAMPLE, type AISkillPayload } from '../api/aiSkill';
import type { AISkill } from '../types';
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
  const [aiModelId, setAiModelId] = useState('');

  // 功能开关状态
  const [etfHintsOn, setEtfHintsOn] = useState(true);
  const [otcHintsOn, setOtcHintsOn] = useState(true);

  const saveFlags = async (patch: { etf_hints_enabled?: boolean; otc_hints_enabled?: boolean }) => {
    try {
      const res = await systemApi.updateFeatureFlags(patch);
      if (res.data) {
        setEtfHintsOn(res.data.etf_hints_enabled);
        setOtcHintsOn(res.data.otc_hints_enabled);
      }
      setError(null);
    } catch (err: any) {
      setError(err?.message || '保存开关失败');
    }
  };

  // 概念映射状态
  const [cmProgress, setCmProgress] = useState<ConceptMapProgress | null>(null);
  const [cmFetching, setCmFetching] = useState(false);

  const loadCmProgress = async () => {
    try {
      const res = await conceptMapApi.progress();
      setCmProgress(res.data || null);
    } catch { /* 静默 */ }
  };

  const fetchCmBatch = async () => {
    setCmFetching(true);
    try {
      await conceptMapApi.fetchBatch();
      await loadCmProgress();
      setError(null);
    } catch (err: any) {
      setError(err?.message || '概念抓取失败');
    } finally {
      setCmFetching(false);
    }
  };

  // 错误日志状态
  const [errLogs, setErrLogs] = useState<ErrorLogItem[]>([]);
  const [errCategory, setErrCategory] = useState<string>('');
  const [errLoading, setErrLoading] = useState(false);

  const loadErrLogs = async (category = errCategory) => {
    setErrLoading(true);
    try {
      const res = await errorLogApi.list(200, category || undefined);
      setErrLogs(res.data || []);
    } catch {
      setErrLogs([]);
    } finally {
      setErrLoading(false);
    }
  };

  useEffect(() => {
    loadCmProgress();
    loadErrLogs();
    systemApi.getFeatureFlags()
      .then((r) => {
        if (r.data) {
          setEtfHintsOn(r.data.etf_hints_enabled);
          setOtcHintsOn(r.data.otc_hints_enabled);
        }
      })
      .catch(() => { /* 开关加载失败保持默认 */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // AI Skills 状态
  const [skills, setSkills] = useState<AISkill[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillDialogOpen, setSkillDialogOpen] = useState(false);
  const [skillJson, setSkillJson] = useState('');
  const [skillSaving, setSkillSaving] = useState(false);

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
          setAiModelId(cfg.ai_model_id || '');
          // 匹配预设
          const matched = cfg.presets?.find((p) => p.key === cfg.ai_model);
          if (matched) setSelectedPreset(matched.key);
        }
      })
      .catch(() => setError('加载 AI 配置失败'))
      .finally(() => setAiLoading(false));
  }, []);

  // 加载 Skill 列表
  const loadSkills = async () => {
    setSkillsLoading(true);
    try {
      const res = await aiSkillApi.list();
      setSkills(res.data || []);
    } catch {
      // 静默（Skill 区块失败不影响主配置）
    } finally {
      setSkillsLoading(false);
    }
  };

  useEffect(() => {
    loadSkills();
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
        ai_model_id: aiModelId || '',
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
                label="模型 ID 覆盖（可选）"
                value={aiModelId}
                onChange={(e) => setAiModelId(e.target.value)}
                placeholder="如 glm-4-plus / qwen-max，空=用预设默认"
                fullWidth
                size="small"
                helperText="留空使用所选供应商的默认模型"
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

      {/* ── 提示功能开关 ── */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1 }}>提示功能开关</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            关闭后对应模块不再生成提示且不再发起其数据源请求（省资源）；关闭立即生效。
          </Typography>
          <FormControlLabel
            control={
              <Switch
                checked={etfHintsOn}
                onChange={async (e) => {
                  setEtfHintsOn(e.target.checked);
                  await saveFlags({ etf_hints_enabled: e.target.checked });
                }}
              />
            }
            label={etfHintsOn ? '场内提示与扫描：已开启（ETF 溢价/流动性/量价提示 + 潜力 ETF 扫描）' : '场内提示与扫描：已关闭'}
            sx={{ display: 'block', mb: 1 }}
          />
          <FormControlLabel
            control={
              <Switch
                checked={otcHintsOn}
                onChange={async (e) => {
                  setOtcHintsOn(e.target.checked);
                  await saveFlags({ otc_hints_enabled: e.target.checked });
                }}
              />
            }
            label={otcHintsOn ? '场外提示与高低估：已开启（申购状态/费率/高低估区间/15 点择时）' : '场外提示与高低估：已关闭'}
            sx={{ display: 'block' }}
          />
        </CardContent>
      </Card>

      {/* ── 概念板块映射（THS 渐进获取）── */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1, flexWrap: 'wrap', gap: 1 }}>
            <Typography variant="h6">概念板块映射</Typography>
            <Button
              size="small" variant="contained"
              disabled={cmFetching}
              onClick={fetchCmBatch}
            >
              {cmFetching ? '抓取中…' : '抓取一批（20 个板块）'}
            </Button>
            <Button size="small" onClick={async () => {
              try { await conceptMapApi.export(); } catch (err: any) {
                setError(err?.message || '导出失败');
              }
            }}>
              导出映射
            </Button>
            <Button size="small" color="error" onClick={async () => {
              if (!window.confirm('清空全部概念映射？（建议先导出备份）')) return;
              try {
                await conceptMapApi.clear();
                await loadCmProgress();
              } catch (err: any) {
                setError(err?.message || '清空失败');
              }
            }}>
              清空
            </Button>
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            同花顺概念板块成分映射（基金副标签的真概念来源）。渐进获取：每轮抓 20 个最久未更新的板块（防封禁），
            也可启动时自动导入种子数据。映射越多，副标签的概念覆盖越全。
          </Typography>
          {cmProgress && (
            <Box sx={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
              <Typography variant="body2">
                已映射概念：<strong>{cmProgress.concepts_mapped}</strong> / {cmProgress.concepts_total}
              </Typography>
              <Typography variant="body2">
                已映射股票：<strong>{cmProgress.stocks_mapped}</strong>
              </Typography>
              <Typography variant="body2" color="text.secondary">
                最近更新: {cmProgress.latest_update ?? '—'}
              </Typography>
            </Box>
          )}
        </CardContent>
      </Card>

      {/* ── 错误日志 ── */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1, flexWrap: 'wrap', gap: 1 }}>
            <Typography variant="h6">错误日志</Typography>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
              <TextField
                select size="small" label="分类" sx={{ minWidth: 120 }}
                value={errCategory}
                onChange={(e) => { setErrCategory(e.target.value); loadErrLogs(e.target.value); }}
              >
                <MenuItem value="">全部</MenuItem>
                {Object.entries(CATEGORY_META).map(([k, v]) => (
                  <MenuItem key={k} value={k}>{v.label}</MenuItem>
                ))}
              </TextField>
              <Button size="small" onClick={() => loadErrLogs()}>刷新</Button>
              <Button size="small" variant="contained" onClick={async () => {
                try { await errorLogApi.download(); } catch (err: any) {
                  setError(err?.message || '下载失败');
                }
              }}>
                下载日志
              </Button>
              <Button size="small" color="error" onClick={async () => {
                if (!window.confirm('清空全部错误日志？')) return;
                try {
                  await errorLogApi.clear();
                  loadErrLogs();
                } catch (err: any) {
                  setError(err?.message || '清空失败');
                }
              }}>
                清空
              </Button>
            </Box>
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            系统功能报错自动记录（最多保留 2000 条）。<strong style={{ color: '#d32f2f' }}>限流/封禁</strong>类错误标识数据源被限制，
            可据此排查是哪个模块触发限制；右上角铃铛会实时告警。
          </Typography>
          {errLoading && <LinearProgress sx={{ mb: 1 }} />}
          <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 400 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ width: 150 }}>时间</TableCell>
                  <TableCell sx={{ width: 90 }}>分类</TableCell>
                  <TableCell sx={{ width: 130 }}>模块</TableCell>
                  <TableCell>消息</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {errLogs.map((it) => {
                  const meta = CATEGORY_META[it.category] || CATEGORY_META.other;
                  return (
                    <TableRow key={it.id} hover>
                      <TableCell>{it.ts}</TableCell>
                      <TableCell>
                        <Chip size="small" label={meta.label} color={meta.color}
                          sx={{ height: 20, fontSize: '0.7rem' }} />
                      </TableCell>
                      <TableCell>{it.module}</TableCell>
                      <TableCell sx={{ fontSize: '0.78rem' }}>
                        {it.message}
                        {it.detail && (
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                            {it.detail.length > 80 ? it.detail.slice(0, 80) + '…' : it.detail}
                          </Typography>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
                {errLogs.length === 0 && !errLoading && (
                  <TableRow>
                    <TableCell colSpan={4} align="center">暂无错误记录 — 系统运行正常</TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>

      {/* ── AI Skills 分析技能管理 ── */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
            <Typography variant="h6">AI 分析技能（Skill）</Typography>
            <Box>
              <Button
                size="small"
                startIcon={<UploadIcon />}
                onClick={() => { setSkillJson(''); setSkillDialogOpen(true); }}
                sx={{ mr: 1 }}
              >
                导入 JSON
              </Button>
              <Button
                size="small"
                startIcon={<AddIcon />}
                onClick={() => { setSkillJson(SKILL_EXAMPLE); setSkillDialogOpen(true); }}
              >
                新建（填入示例）
              </Button>
            </Box>
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            启用的 Skill 会注入 AI 对话的系统提示词，占位符 {'{{fund_pool}}'} / {'{{market_regime}}'} / {'{{fund:<id>}}'} 自动渲染数据上下文。
          </Typography>
          {skillsLoading && <LinearProgress sx={{ mb: 2 }} />}
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>名称</TableCell>
                  <TableCell>描述</TableCell>
                  <TableCell>状态</TableCell>
                  <TableCell align="right">操作</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {skills.map((sk) => (
                  <TableRow key={sk.id}>
                    <TableCell>{sk.name}</TableCell>
                    <TableCell sx={{ maxWidth: 380, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {sk.description || '-'}
                    </TableCell>
                    <TableCell>
                      <Switch
                        checked={sk.enabled}
                        onChange={async (e) => {
                          try {
                            const res = await aiSkillApi.toggle(sk.id, e.target.checked);
                            setSkills((prev) => prev.map((x) => (x.id === sk.id && res.data ? res.data : x)));
                          } catch (err: any) {
                            setError(err?.message || '切换失败');
                          }
                        }}
                        size="small"
                      />
                    </TableCell>
                    <TableCell align="right">
                      <IconButton
                        size="small"
                        onClick={async () => {
                          if (!window.confirm(`确认删除 Skill「${sk.name}」？`)) return;
                          try {
                            await aiSkillApi.remove(sk.id);
                            setSkills((prev) => prev.filter((x) => x.id !== sk.id));
                          } catch (err: any) {
                            setError(err?.message || '删除失败');
                          }
                        }}
                      >
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                ))}
                {skills.length === 0 && !skillsLoading && (
                  <TableRow>
                    <TableCell colSpan={4} align="center">
                      暂无 Skill — 点击「新建（填入示例）」快速创建，或导入 JSON
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>

      {/* ── Skill 新建/导入对话框 ── */}
      <Dialog open={skillDialogOpen} onClose={() => setSkillDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>导入 / 新建 AI Skill</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1, mb: 1 }}>
            粘贴 JSON 数组（按名称 upsert：已存在则更新，不存在则创建）。占位符支持 {'{{fund_pool}}'} / {'{{market_regime}}'} / {'{{fund:<id>}}'}。
          </Typography>
          <TextField
            label="Skill JSON"
            value={skillJson}
            onChange={(e) => setSkillJson(e.target.value)}
            multiline
            rows={14}
            fullWidth
            sx={{ fontFamily: 'monospace' }}
            InputProps={{ style: { fontFamily: 'monospace', fontSize: 13 } }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSkillJson(SKILL_EXAMPLE)}>填入示例</Button>
          <Button onClick={() => setSkillDialogOpen(false)}>取消</Button>
          <Button
            variant="contained"
            disabled={skillSaving}
            onClick={async () => {
              setSkillSaving(true);
              try {
                const parsed = JSON.parse(skillJson);
                const items: AISkillPayload[] = Array.isArray(parsed) ? parsed : [parsed];
                const res = await aiSkillApi.import(items);
                const r = res.data!;
                setError(null);
                setSkillDialogOpen(false);
                await loadSkills();
                // eslint-disable-next-line no-alert
                window.alert(`导入完成：新建 ${r.created}，更新 ${r.updated}${r.errors.length ? '，错误 ' + r.errors.length : ''}`);
              } catch (err: any) {
                setError(err?.response?.data?.detail || err?.message || '导入失败（JSON 格式错误？）');
              } finally {
                setSkillSaving(false);
              }
            }}
          >
            导入
          </Button>
        </DialogActions>
      </Dialog>

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
