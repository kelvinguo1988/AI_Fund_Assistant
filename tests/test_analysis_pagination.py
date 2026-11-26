"""分析结果分页（2026-11-26 性能修复）

## 问题
历史报告页 `/history` 加载数秒。

## 实测定位
| 层 | 耗时 |
|---|---|
| DB 查询 856 行 | 0.03s |
| 服务端处理（查询+批量加载+转换） | 0.42s |
| 网络传输 1.85MB | 0.04s |
| **前端 map 渲染 856 个卡片** | **数秒（真凶）** |
| 冷启动额外 | ~8s（SQLAlchemy 首次反射） |

原先 `/api/analysis` 无分页、无 limit，全表返回 1.85MB，
前端 `results.map()` 一次性渲染全部 856 行。

## 修复
1. 后端加 limit/offset（**不传 limit 时保持全量**，不影响导出/回测等既有调用方）
2. 新增轻量 `/count` 接口（只扫索引，不加载行）
3. 前端加 TablePagination，默认每页 50 条
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestBackendPagination:
    def _src(self) -> str:
        return (ROOT / "backend/routers/analysis.py").read_text(encoding="utf-8")

    def test_limit_offset_params(self):
        src = self._src()
        assert "limit: Optional[int] = Query(" in src
        assert "offset: int = Query(" in src

    def test_limit_is_optional_for_backward_compat(self):
        """不传 limit 必须保持全量——导出、回测等既有调用方依赖这个行为"""
        src = self._src()
        assert "limit: Optional[int] = Query(\n        None" in src or \
               "limit: Optional[int] = Query(None" in src

    def test_applies_only_when_limit_given(self):
        src = self._src()
        assert "if limit is not None:" in src
        assert ".offset(offset).limit(limit)" in src

    def test_order_is_deterministic(self):
        """加了 id 作为次级排序键：同一天多条时顺序稳定，分页才不会重复/漏行"""
        src = self._src()
        assert "AnalysisResult.id.desc()" in src

    def test_count_endpoint_exists(self):
        src = self._src()
        assert '@router.get("/count"' in src
        assert "func.count(AnalysisResult.id)" in src

    def test_limit_upper_bound(self):
        """防滥用：limit 上限 2000"""
        src = self._src()
        assert "le=2000" in src


class TestFrontendPagination:
    def _src(self) -> str:
        return (ROOT / "frontend/src/pages/HistoryReports.tsx").read_text(encoding="utf-8")

    def test_uses_table_pagination(self):
        src = self._src()
        assert "TablePagination" in src

    def test_passes_limit_offset(self):
        src = self._src()
        assert "limit: rowsPerPage" in src
        assert "offset: page * rowsPerPage" in src

    def test_fetches_total_separately(self):
        """总数单独取，不靠拉全量再数长度"""
        src = self._src()
        assert "analysisApi.count(" in src

    def test_resets_page_on_filter_change(self):
        """换筛选条件必须回第一页，否则可能落在空页"""
        src = self._src()
        assert "setPage(0)" in src

    def test_default_page_size_reasonable(self):
        src = self._src()
        assert "useState(50)" in src, "默认每页 50 条"

    def test_api_client_has_count(self):
        api = (ROOT / "frontend/src/api/analysis.ts").read_text(encoding="utf-8")
        assert "count:" in api
        assert "/count" in api
