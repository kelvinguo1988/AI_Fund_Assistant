/**
 * 基金池管理页面
 */

import React, { useEffect, useState, useRef, useMemo, Fragment } from 'react';
import {
  Box,
  Typography,
  Tooltip,
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
import { Add as AddIcon, Edit as EditIcon, Delete as DeleteIcon, Upload as UploadIcon, Download as DownloadIcon, Refresh as RefreshIcon, Star as StarIcon, StarBorder as StarBorderIcon } from '@mui/icons-material';
import { fundApi } from '../api/fund';
import type { FundOut, FundCreate, FundUpdate } from '../types';
import ConfirmDialog from '../components/ConfirmDialog';

// ── 导入 JSON 解析辅助：兼容多种结构与字段名（防止"导入没反应"）──
// 修复前：只认 data.items + 硬编码 code/name，字段名/结构不匹配时全失败，
// 且成功提示吞掉 errors → 用户看到绿色"导入完成"但列表无变化，误以为没反应。
type RawItem = Record<string, any>;

function _pick(raw: RawItem, keys: string[]): string {
  for (const k of keys) {
    const v = raw[k];
    if (v !== undefined && v !== null && String(v).trim() !== '') return String(v).trim();
  }
  return '';
}

const CODE_KEYS = ['code', 'fund_code', 'fundCode', '基金代码', '代码'];
const NAME_KEYS = ['name', 'fund_name', 'fundName', '基金名称', '名称'];
const TAG_KEYS = ['tags', 'tag', '标签', '基金标签'];

/**
 * 解析导入文本（JSON）。
 * 兼容：顶层数组 / {items} / {funds} / {data}；
 * 字段名兼容 code|fund_code|基金代码|代码 与 name|基金名称|名称 等。
 * 返回规整后的 items 以及解析错误（JSON 非法、结构无法识别）。
 */
function parseImportJson(text: string): {
  items: { code: string; name: string; tags?: string }[];
  errors: string[];
} {
  const errors: string[] = [];
  let data: any;
  try {
    data = JSON.parse(text);
  } catch (e: any) {
    errors.push('JSON 解析失败: ' + (e?.message || '格式错误'));
    return { items: [], errors };
  }
  let raw: any[] = [];
  if (Array.isArray(data)) raw = data;
  else if (data && Array.isArray(data.items)) raw = data.items;
  else if (data && Array.isArray(data.funds)) raw = data.funds;
  else if (data && Array.isArray(data.data)) raw = data.data;
  else {
    errors.push('未识别的 JSON 结构（需为数组，或含 items/funds/data 字段的对象）');
    return { items: [], errors };
  }
  const items = raw
    .map((it: RawItem) => ({
      code: _pick(it, CODE_KEYS),
      name: _pick(it, NAME_KEYS),
      tags: _pick(it, TAG_KEYS) || undefined,
    }))
    .filter((it) => it.code && it.name);
  return { items, errors };
}

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

/** 取基金主标签（逗号分隔标签中的第一个，作为分类依据） */
const primaryTag = (tags?: string | null): string | null => {
  if (!tags) return null;
  const parts = tags.split(',').map((t) => t.trim()).filter(Boolean);
  return parts[0] ?? null;
};

/**
 * 按标签分类分组（与后端 classify_and_sort_funds 同序）：
 * - 主标签出现频率降序（同类多的分类在前），「未分类」永远最后；
 * - 同类内按名称升序；
 * - pinStarred=true 时把星标基金整体置顶为「已星标」分组。
 */
const groupByClassification = (funds: FundOut[], pinStarred: boolean) => {
  const freq = new Map<string, number>();
  funds.forEach((f) => {
    const t = primaryTag(f.tags);
    if (t) freq.set(t, (freq.get(t) ?? 0) + 1);
  });
  const byName = (a: FundOut, b: FundOut) =>
    (a.name || '').localeCompare(b.name || '', 'zh') ||
    (a.code || '').localeCompare(b.code || '');

  const sorted = [...funds].sort((a, b) => {
    const sa = pinStarred && a.starred ? 0 : 1;
    const sb = pinStarred && b.starred ? 0 : 1;
    if (sa !== sb) return sa - sb;
    const ta = primaryTag(a.tags);
    const tb = primaryTag(b.tags);
    const rankA: [number, number, string] = ta === null ? [1, 0, ''] : [0, -(freq.get(ta) ?? 0), ta];
    const rankB: [number, number, string] = tb === null ? [1, 0, ''] : [0, -(freq.get(tb) ?? 0), tb];
    for (let i = 0; i < 3; i++) {
      if (rankA[i] !== rankB[i]) {
        return i === 2
          ? (rankA[i] as string).localeCompare(rankB[i] as string, 'zh')
          : (rankA[i] as number) - (rankB[i] as number);
      }
    }
    return byName(a, b);
  });

  const starred = pinStarred ? sorted.filter((f) => f.starred) : [];
  const rest = pinStarred ? sorted.filter((f) => !f.starred) : sorted;
  const groups: { tag: string | null; funds: FundOut[] }[] = [];
  const seen = new Set<string | null>();
  rest.forEach((f) => {
    const t = primaryTag(f.tags);
    if (!seen.has(t)) {
      seen.add(t);
      groups.push({ tag: t, funds: [] });
    }
    groups.find((g) => g.tag === t)!.funds.push(f);
  });
  return { starred, groups };
};

/** 风格漂移检测：官方定位偏固收/红利/宽基，但当前重仓押注成长赛道 */
const DEFENSIVE = /固收|偏债|稳健|红利|宽基|货币|债券/;
const AGGRESSIVE = /光模块|CPO|算力|半导体|芯片|数字经济|机器人|AI服务器|新能源/;
const isStyleDrift = (f: FundOut): boolean => {
  if (!f.exposure_tags || !f.tags) return false;
  return DEFENSIVE.test(f.tags) && AGGRESSIVE.test(f.exposure_tags);
};

/** 分类分组表头样式 */
const groupHeaderSx = {
  backgroundColor: '#f0f4ff',
  fontWeight: 700,
  color: '#1976D2',
  fontSize: '0.8rem',
};

const FundPool: React.FC = () => {
  const [funds, setFunds] = useState<FundOut[]>([]);
  const [_loading, setLoading] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editFund, setEditFund] = useState<FundOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FundOut | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' as 'success' | 'error' | 'warning' | 'info' });

  // 批量导入状态
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [_importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{ total: number; created: number; skipped: string[]; errors: string[] } | null>(null);
  const [refreshingId, setRefreshingId] = useState<number | null>(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const jsonInputRef = useRef<HTMLInputElement>(null);

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

  /** 根据代码查询名称与类型（优先缓存，必要时走网络） */
  const doLookupName = async (code: string) => {
    if (!/^\d{6}$/.test(code)) return;
    setLookupLoading(true);
    try {
      const res = await fundApi.lookupName(code);
      const d = res.data;
      if (d?.name) {
        setFormName(d.name);
        if (d.fund_type) setFormType(d.fund_type as 'etf' | 'otc');
        setSnackbar({
          open: true,
          message: `已自动填充名称${d.fund_type ? `（${d.fund_type === 'etf' ? 'ETF' : '场外'}）` : ''}`,
          severity: 'success',
        });
      } else {
        setSnackbar({ open: true, message: '未找到该代码名称，请手动填写', severity: 'warning' });
      }
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.message || '获取名称失败', severity: 'error' });
    } finally {
      setLookupLoading(false);
    }
  };

  const handleLookupName = () => doLookupName(formCode);
  const handleCodeBlur = () => {
    if (!formName && /^\d{6}$/.test(formCode)) doLookupName(formCode);
  };

  /** 渲染单行基金（含新增的星标列） */
  const renderFundRow = (fund: FundOut) => (
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
        {/* 副标签：当前持仓赛道暴露（随季报变动）+ 风格漂移提示 */}
        {(fund.exposure_tags || '').split(',').filter(Boolean).slice(0, 2).map((tag) => (
          <Chip key={`ex-${tag}`} label={tag} size="small" variant="outlined"
            sx={{ mr: 0.5, mb: 0.3, fontSize: '0.7rem' }} />
        ))}
        {isStyleDrift(fund) && (
          <Tooltip title={`主标签与持仓暴露不一致——官方定位「${fund.fund_type_official ?? fund.tags}」但当前重仓押注其他赛道，注意风格漂移风险`}>
            <Chip label="漂移" size="small" color="warning"
              sx={{ mr: 0.5, mb: 0.3, fontSize: '0.7rem' }} />
          </Tooltip>
        )}
        {fund.benchmark_text && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontSize: '0.65rem' }}>
            基准: {fund.benchmark_text.length > 28 ? fund.benchmark_text.slice(0, 28) + '…' : fund.benchmark_text}
          </Typography>
        )}
      </TableCell>
      <TableCell>
        <Chip label={fund.status === 'active' ? '启用' : '停用'} size="small"
          color={fund.status === 'active' ? 'success' : 'default'} />
      </TableCell>
      <TableCell>
        <IconButton size="small" title={fund.starred ? '取消星标' : '设为星标'}
          onClick={async () => {
            try {
              await fundApi.update(fund.id, { starred: !fund.starred });
              setSnackbar({ open: true, message: !fund.starred ? '已设为星标' : '已取消星标', severity: 'success' });
              loadFunds();
            } catch {
              setSnackbar({ open: true, message: '星标操作失败', severity: 'error' });
            }
          }}>
          {fund.starred
            ? <StarIcon fontSize="small" sx={{ color: '#F57C00' }} />
            : <StarBorderIcon fontSize="small" />}
        </IconButton>
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
  );

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

  const handleJsonImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = parseImportJson(text);
      if (parsed.errors.length) {
        setSnackbar({ open: true, message: `导入失败: ${parsed.errors.join('；')}`, severity: 'error' });
        return;
      }
      if (!parsed.items.length) {
        setSnackbar({ open: true, message: 'JSON 中未找到有效基金（需含 code/name 或 基金代码/名称 等字段）', severity: 'error' });
        return;
      }
      const res = await fundApi.batchImport(parsed.items);
      const d = res.data || { total: parsed.items.length, created: 0, skipped: [], errors: [] };
      const parts = [`导入完成：新建 ${d.created || 0}`, `跳过 ${(d.skipped || []).length}`];
      if ((d.errors || []).length) parts.push(`失败 ${(d.errors || []).length}`);
      setSnackbar({
        open: true,
        message: parts.join('，'),
        severity: (d.errors || []).length ? 'warning' : 'success',
      });
      loadFunds();
    } catch (err: any) {
      // 优先展示服务器返回的真实错误明细（detail），便于排查；否则用拦截器文案
      const detail = err?.response?.data?.detail;
      const msg = detail ? `导入失败: ${detail}` : (err?.message || '未知错误');
      setSnackbar({ open: true, message: msg, severity: 'error' });
    }
    if (jsonInputRef.current) jsonInputRef.current.value = '';
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

  const { starred, groups } = useMemo(
    () => groupByClassification(funds, true),
    [funds],
  );

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
          <Button variant="outlined" startIcon={<DownloadIcon />} onClick={() => fundApi.exportFunds().catch(() => setSnackbar({ open: true, message: '导出失败', severity: 'error' }))}>导出</Button>
          <Button variant="outlined" startIcon={<UploadIcon />} onClick={() => jsonInputRef.current?.click()}>导入JSON</Button>
          <input type="file" ref={jsonInputRef} accept=".json" style={{ display: 'none' }} onChange={handleJsonImport} />
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
              <TableCell>星标</TableCell>
              <TableCell>操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {funds.length === 0 ? (
              <TableRow><TableCell colSpan={8} align="center">暂无基金数据</TableCell></TableRow>
            ) : (
              <Fragment>
                {starred.length > 0 && (
                  <Fragment key="starred">
                    <TableRow>
                      <TableCell colSpan={8} sx={groupHeaderSx}>★ 已星标（{starred.length}）</TableCell>
                    </TableRow>
                    {starred.map((fund) => renderFundRow(fund))}
                  </Fragment>
                )}
                {groups.map((g) => (
                  <Fragment key={g.tag ?? '__none__'}>
                    <TableRow>
                      <TableCell colSpan={8} sx={groupHeaderSx}>
                        {g.tag ? `标签分类：${g.tag}` : '未分类'}（{g.funds.length}）
                      </TableCell>
                    </TableRow>
                    {g.funds.map((fund) => renderFundRow(fund))}
                  </Fragment>
                ))}
              </Fragment>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* 新增/编辑弹窗 */}
      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editFund ? '编辑基金' : '新增基金'}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
            <TextField label="基金代码" value={formCode} onChange={(e) => setFormCode(e.target.value)}
              onBlur={handleCodeBlur} disabled={!!editFund} placeholder="6位数字代码如510300" sx={{ flexGrow: 1 }} />
            {!editFund && (
              <Button variant="outlined" onClick={handleLookupName}
                disabled={lookupLoading || !/^\d{6}$/.test(formCode)} sx={{ mt: 0.5, whiteSpace: 'nowrap' }}>
                {lookupLoading ? '获取中…' : '获取名称'}
              </Button>
            )}
          </Box>
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
            const trimmed = importText.trim();
            let items: { code: string; name: string; tags?: string }[] = [];
            let parseErr = '';
            // 兼容在文本框直接粘贴 JSON（与"导入JSON"文件入口共用解析）
            if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
              const p = parseImportJson(importText);
              if (p.errors.length) parseErr = p.errors.join('；');
              else items = p.items;
            } else {
              const lines = importText.split('\n').filter(Boolean);
              items = lines.map((line) => {
                const parts = line.trim().split(/\s+/);
                return { code: parts[0], name: parts[1] || '', tags: parts.slice(2).join(',') || undefined };
              }).filter((item) => item.code);
            }
            if (parseErr) {
              setSnackbar({ open: true, message: '批量导入失败: ' + parseErr, severity: 'error' });
              return;
            }
            if (items.length === 0) return;
            setImporting(true);
            try {
              const res = await fundApi.batchImport(items);
              const d = res.data || { total: items.length, created: 0, skipped: [], errors: [] };
              setImportResult(d);
              // 无论新建还是跳过都刷新列表（数据已与后端同步）
              loadFunds();
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
