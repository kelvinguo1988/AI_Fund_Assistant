"""pytest 全局配置 — 测试防限流防线

2026-08-31：测试中任何真实重量级数据源调用都可能触发数据源限流
（用户红线：测试可以慢但不要触发限流）。本文件全局屏蔽两个
"单次请求量大"的调用源：

- OtcTradeStatusService.get_status_map  真实调用 = 拉全市场 24056 只申购状态
- IndexValuationService.get_valuations  真实调用 = 拉乐咕 4 指数 PE 历史
  （乐咕已实测对短时重复请求限流）

需要验证真实网络行为的测试应显式覆盖（monkeypatch 覆盖本 fixture 的补丁）。
其余轻量接口（ETF spot/个股快照/fundgz 单只）由各测试自行 mock。
"""

import pytest


@pytest.fixture(autouse=True)
def _block_heavy_network(monkeypatch):
    import backend.services.index_valuation_service as _iv

    async def _empty_map(self, force: bool = False):
        return {}

    async def _empty_vals(force: bool = False):
        return []

    monkeypatch.setattr(_iv.OtcTradeStatusService, "get_status_map", _empty_map)
    monkeypatch.setattr(_iv.IndexValuationService, "get_valuations", _empty_vals)
