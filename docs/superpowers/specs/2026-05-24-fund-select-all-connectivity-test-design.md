# 基金池全选 & 数据源连通性测试 设计

## 1. 基金池全选/反选

### 范围
仅前端 `frontend/src/pages/FundPool.tsx`

### UI 行为
- 表头 checkbox 列增加全选 Checkbox
- 三态：全选 (checked) / 部分选中 (indeterminate) / 全不选 (unchecked)
- 点击全选：若已全选则反选清空，否则选中当前全部基金
- 与现有批量启用/停用按钮联动

### 实现要点
- 使用 `selected.length` 与当前基金列表 `funds.length` 比较
- `indeterminate = selected.length > 0 && selected.length < funds.length`
- `checked = selected.length === funds.length && funds.length > 0`
- 不涉及后端改动

## 2. 数据源连通性测试

### 范围
后端新增端点 + 前端系统设置页

### 后端

#### 新增端点
`GET /api/system/connectivity`

#### 测试项
| 测试项 | 目标 | 方式 |
|--------|------|------|
| fund.eastmoney.com | 基金数据 | HTTP HEAD/GET |
| push2.eastmoney.com | 行情数据 | HTTP HEAD/GET |
| anonflow2.eastmoney.com | NID 授权 | HTTP HEAD/GET |
| push2his.eastmoney.com | 历史行情 | HTTP HEAD/GET |
| datacenter-web.eastmoney.com | 数据中心 | HTTP HEAD/GET |
| AI API | LLM 服务 | 如已配置则测试 |

#### 超时策略
- 单次超时 10s
- 总超时 30s（3s 连接超时 + 7s 读取超时 per host）

#### 返回格式
```json
{
  "status": "ok",
  "results": [
    {"name": "fund.eastmoney.com", "reachable": true, "latency_ms": 123, "error": null},
    {"name": "push2.eastmoney.com", "reachable": false, "latency_ms": null, "error": "timeout"}
  ],
  "summary": {"total": 6, "reachable": 4, "unreachable": 2}
}
```

#### 实现位置
`backend/routers/system_config.py` 追加端点，测试逻辑放在 `backend/services/connectivity_service.py`（新建）

### 前端

#### 位置
系统设置页（`frontend/src/pages/Settings.tsx` 或对应设置组件）

#### UI
- "连通性测试" 面板
- 点击"开始测试"按钮，逐项显示测试进度和结果
- 每项显示：名称、状态图标（绿色勾/红色叉）、延迟、错误信息
- 顶部汇总：X/Y 项可达

### 不影响现有功能
- 两个功能互不依赖
- 不影响现有 API、数据流
- 连通性测试失败不影响主流程（仅展示状态）
