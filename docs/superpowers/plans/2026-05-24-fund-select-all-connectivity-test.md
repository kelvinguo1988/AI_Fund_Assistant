# 基金池全选 & 数据源连通性测试 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基金池表头增加全选/反选复选框；系统设置页增加数据源连通性测试面板

**Architecture:** 两个独立功能：全选纯前端改动；连通性测试需新建后端 service + 端点 + 前端页面

**Tech Stack:** FastAPI (Python) + React/TypeScript + Material UI + akshare/httpx

---

### Task 1: 后端 - 连通性测试 Service

**Files:**
- Create: `backend/services/connectivity_service.py`
- Modify: `backend/schemas/system_config.py:43` (在文件末尾追加新 Schema)

- [ ] **Step 1: 新增连通性测试 Schema**

在 `backend/schemas/system_config.py` 末尾追加：

```python
class ConnectivityItem(BaseModel):
    """单个连通性测试结果"""
    name: str = Field(..., description="测试目标名称/域名")
    reachable: bool = Field(..., description="是否可达")
    latency_ms: Optional[float] = Field(None, description="延迟(毫秒)")
    error: Optional[str] = Field(None, description="错误信息")


class ConnectivityResult(BaseModel):
    """连通性测试汇总结果"""
    status: str = Field(..., description="整体状态: ok / partial / fail")
    results: list[ConnectivityItem] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict, description="{total, reachable, unreachable}")
```

- [ ] **Step 2: 新建 `backend/services/connectivity_service.py`**

```python
"""数据源连通性测试服务"""

import time
import logging
from typing import Optional

import httpx

from backend.schemas.system_config import ConnectivityItem, ConnectivityResult

logger = logging.getLogger(__name__)

# 必要数据源域名列表
TARGETS: list[dict[str, str]] = [
    {"name": "fund.eastmoney.com", "url": "https://fund.eastmoney.com"},
    {"name": "push2.eastmoney.com", "url": "https://push2.eastmoney.com"},
    {"name": "anonflow2.eastmoney.com", "url": "https://anonflow2.eastmoney.com"},
    {"name": "push2his.eastmoney.com", "url": "https://push2his.eastmoney.com"},
    {"name": "datacenter-web.eastmoney.com", "url": "https://datacenter-web.eastmoney.com"},
]

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 5.0


async def _test_single(client: httpx.AsyncClient, name: str, url: str) -> ConnectivityItem:
    """测试单个目标的连通性"""
    start = time.monotonic()
    try:
        resp = await client.head(url, follow_redirects=True, timeout=CONNECT_TIMEOUT)
        latency = (time.monotonic() - start) * 1000
        # 2xx/3xx 视为可达
        if 200 <= resp.status_code < 400:
            return ConnectivityItem(name=name, reachable=True, latency_ms=round(latency, 1))
        else:
            return ConnectivityItem(
                name=name, reachable=True, latency_ms=round(latency, 1),
                error=f"HTTP {resp.status_code}"
            )
    except httpx.TimeoutException:
        latency = (time.monotonic() - start) * 1000
        return ConnectivityItem(name=name, reachable=False, latency_ms=round(latency, 1), error="timeout")
    except httpx.ConnectError:
        latency = (time.monotonic() - start) * 1000
        return ConnectivityItem(name=name, reachable=False, latency_ms=round(latency, 1), error="network unreachable")
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        return ConnectivityItem(name=name, reachable=False, latency_ms=round(latency, 1), error=str(e))


async def test_all_connectivity(
    ai_base_url: Optional[str] = None,
    ai_enabled: bool = False,
) -> ConnectivityResult:
    """测试所有必要数据源的连通性

    Args:
        ai_base_url: AI API 地址 (若配置)
        ai_enabled: AI 是否启用 (ai_enabled 为 True 且 ai_base_url 有值时测试 AI 连通性)
    """
    targets = list(TARGETS)

    if ai_enabled and ai_base_url:
        targets.append({"name": "AI API", "url": ai_base_url.rstrip("/")})

    results: list[ConnectivityItem] = []
    async with httpx.AsyncClient(timeout=READ_TIMEOUT) as client:
        for t in targets:
            item = await _test_single(client, t["name"], t["url"])
            results.append(item)

    reachable = sum(1 for r in results if r.reachable)
    total = len(results)
    unreachable = total - reachable

    if unreachable == 0:
        status = "ok"
    elif reachable == 0:
        status = "fail"
    else:
        status = "partial"

    return ConnectivityResult(
        status=status,
        results=results,
        summary={"total": total, "reachable": reachable, "unreachable": unreachable},
    )
```

- [ ] **Step 3: 验证 Service 文件语法**

```bash
python -m py_compile backend/services/connectivity_service.py
```

---

### Task 2: 后端 - 连通性测试 API 端点

**Files:**
- Modify: `backend/routers/system_config.py:177` (在文件末尾追加路由)

- [ ] **Step 1: 在 `backend/routers/system_config.py` 中追加连通性测试端点**

在文件末尾 (import 区域) 添加:
```python
from backend.services.connectivity_service import test_all_connectivity
from backend.schemas.system_config import ConnectivityResult
```

在文件末尾追加路由:
```python
@router.get("/connectivity", response_model=ApiResponse[ConnectivityResult])
async def test_connectivity(
    db: AsyncSession = Depends(get_db),
):
    """测试所有数据源的连通性（东方财富系列域名 + AI API）"""
    config_map = await _get_config_map(db)
    ai_enabled = config_map.get("ai_enabled", "true").lower() == "true"
    ai_base_url = config_map.get("ai_base_url", "")

    result = await test_all_connectivity(
        ai_base_url=ai_base_url if ai_enabled else None,
        ai_enabled=ai_enabled,
    )
    return ApiResponse(data=result)
```

- [ ] **Step 2: 验证路由文件语法**

```bash
python -m py_compile backend/routers/system_config.py
```

---

### Task 3: 前端 - 类型定义 & API 方法

**Files:**
- Modify: `frontend/src/types/index.ts` (追加连通性类型)
- Modify: `frontend/src/api/system.ts` (追加 `testConnectivity` 方法)

- [ ] **Step 1: 在 `frontend/src/types/index.ts` 末尾追加连通性类型**

```typescript
export interface ConnectivityItem {
  name: string;
  reachable: boolean;
  latency_ms: number | null;
  error: string | null;
}

export interface ConnectivityResult {
  status: 'ok' | 'partial' | 'fail';
  results: ConnectivityItem[];
  summary: { total: number; reachable: number; unreachable: number };
}
```

- [ ] **Step 2: 在 `frontend/src/api/system.ts` 末尾追加方法**

在 `systemApi` 对象末尾追加:
```typescript
  testConnectivity: () =>
    apiClient.get<ApiResponse<ConnectivityResult>>(`${BASE}/connectivity`).then((r) => r.data),
```

同时更新 import 添加 `ConnectivityResult`:
```typescript
import type { ApiResponse, AIConfigUpdate, AIConfigOut, ScoringConfigOut, ScoringConfigUpdate, ConnectivityResult } from '../types';
```

- [ ] **Step 3: 验证前端编译**

```bash
cd frontend && npx tsc --noEmit --skipLibCheck 2>&1 | head -20
```

---

### Task 4: 前端 - 系统设置页面 (连通性测试面板)

**Files:**
- Create: `frontend/src/pages/System.tsx`
- Modify: `frontend/src/App.tsx:40-47,65-74,175-185` (import + 导航 + 路由)

- [ ] **Step 1: 创建 `frontend/src/pages/System.tsx`**

```tsx
/**
 * 系统设置页面
 */
import React, { useState } from 'react';
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
} from '@mui/material';
import { Refresh as RefreshIcon, CheckCircle, Error as ErrorIcon } from '@mui/icons-material';
import { systemApi } from '../api/system';
import type { ConnectivityResult } from '../types';

const SystemPage: React.FC = () => {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ConnectivityResult | null>(null);

  const runTest = async () => {
    setTesting(true);
    try {
      const res = await systemApi.testConnectivity();
      setResult(res.data!);
    } catch {
      setResult(null);
    } finally {
      setTesting(false);
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h5" sx={{ mb: 3 }}>系统设置</Typography>

      {/* 连通性测试 */}
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
    </Box>
  );
};

export default SystemPage;
```

- [ ] **Step 2: 在 `App.tsx` 中添加系统设置页面的路由和导航**

在 import 区域 (line 40-47 之间) 添加:
```typescript
import SystemPage from './pages/System';
```

在 NAV_ITEMS (line 74 的 `];` 之前) 添加:
```typescript
  { key: 'system', label: '系统设置', icon: <SettingsIcon />, path: '/system' },
```

在 icon imports (line 24-35) 添加 `Settings`:
```typescript
  Settings as SettingsIcon,
```

在 Routes 区域 (line 183 的 `</Route>` 之后) 添加:
```tsx
          <Route path="/system" element={<SystemPage />} />
```

- [ ] **Step 3: 验证前端编译**

```bash
cd frontend && npx tsc --noEmit --skipLibCheck 2>&1 | head -20
```

---

### Task 5: 前端 - 基金池全选/反选

**Files:**
- Modify: `frontend/src/pages/FundPool.tsx:156-158` (表头) 和 `:134-136` (toggleSelect 逻辑)

- [ ] **Step 1: 替换表头复选框 + 增加全选逻辑**

在 `toggleSelect` 函数后面 (line 136 之后) 追加:
```typescript
  const allSelected = funds.length > 0 && funds.every((f) => selected.includes(f.id));
  const someSelected = selected.length > 0 && !allSelected;

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelected([]);
    } else {
      setSelected(funds.map((f) => f.id));
    }
  };
```

将表头 `<TableCell padding="checkbox"></TableCell>` (line 158) 替换为:
```tsx
              <TableCell padding="checkbox">
                <Checkbox
                  checked={allSelected}
                  indeterminate={someSelected}
                  onChange={toggleSelectAll}
                  size="small"
                />
              </TableCell>
```

- [ ] **Step 2: 验证前端编译**

```bash
cd frontend && npx tsc --noEmit --skipLibCheck 2>&1 | head -20
```

---

### Task 6: 验证

- [ ] **Step 1: 后端语法检查**

```bash
cd backend && python -m py_compile services/connectivity_service.py && python -m py_compile routers/system_config.py && python -m py_compile schemas/system_config.py && echo "ALL OK"
```

- [ ] **Step 2: 前端类型检查 + 构建**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

- [ ] **Step 3: 运行后端 CI 门禁**

```bash
./scripts/ci_gate.sh
```

---

### Task 7: 代码审查

- [ ] **Step 1: 完整代码审查**

按 CLAUDE.md 验证矩阵逐项检查：
- Python 后端：语法通过、端点不破坏现有 API
- 前端：类型通过、构建通过、路由不冲突
- 无密钥硬编码、无环境假设
