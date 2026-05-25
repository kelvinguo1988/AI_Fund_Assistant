"""多数据源管理器 — 优先级链 + 自动降级 + 自动恢复

数据源优先级（由高到低）：
  1. AKShare        — 主接口，数据最全
  2. TuShare         — 次接口，需 token
  3. BaoStock        — 备1，免费无需 token
  4. TickFlow        — 备2，末级兜底

降级规则：
- 当前优先源失败 → 自动切换下一级
- 记录降级状态（含时间戳），5 分钟后自动尝试恢复
- 恢复成功 → 重新提升为活跃源
- 恢复失败 → 维持降级状态，继续使用当前稳定源
"""

import logging
import time
from datetime import date, timedelta
from typing import Optional

from backend.data_sources.base import BaseDataSource, FundData, MarketIndices
from backend.data_sources.akshare_adapter import AKShareAdapter

logger = logging.getLogger(__name__)

# 恢复冷却时间（秒）
_RECOVERY_COOLDOWN = 300  # 5 分钟


class _SourceStatus:
    """单个数据源的运行状态"""

    def __init__(self, name: str, adapter: BaseDataSource, priority: int) -> None:
        self.name = name
        self.adapter = adapter
        self.priority = priority
        self.active = True           # 当前是否可用
        self.degraded_at: float = 0  # 降级时间戳
        self.consecutive_failures = 0

    @property
    def should_retry(self) -> bool:
        """是否已过冷却期，可以尝试恢复"""
        return time.time() - self.degraded_at >= _RECOVERY_COOLDOWN

    def mark_degraded(self) -> None:
        self.active = False
        self.degraded_at = time.time()
        self.consecutive_failures += 1
        logger.warning(f"数据源 [{self.name}] 降级（连续失败 {self.consecutive_failures} 次）")

    def mark_recovered(self) -> None:
        self.active = True
        self.degraded_at = 0
        self.consecutive_failures = 0
        logger.info(f"数据源 [{self.name}] 恢复")

    def mark_unavailable(self) -> None:
        self.active = False
        self.consecutive_failures += 1

    def __repr__(self) -> str:
        return f"Source({self.name}, active={self.active}, priority={self.priority})"


class DataSourceManager(BaseDataSource):
    """多数据源管理器 — 实现 BaseDataSource 接口

    对外透明：analysis_service 只需注入此管理器，调用方式不变。
    """

    def __init__(self, tushare_token: str = "") -> None:
        self._sources: list[_SourceStatus] = []
        self._build_chain(tushare_token)

    def _build_chain(self, tushare_token: str) -> None:
        """按优先级构建数据源链"""
        chain: list[tuple[str, BaseDataSource]] = [
            ("AKShare", AKShareAdapter()),
        ]

        # TuShare（初始化失败时标记不可用但不阻断）
        try:
            from backend.data_sources.tushare_adapter import TuShareAdapter
            ts_adapter = TuShareAdapter(token=tushare_token)
            chain.append(("TuShare", ts_adapter))
        except Exception as e:
            logger.warning(f"TuShare 加载失败: {e}")

        # BaoStock
        try:
            from backend.data_sources.baostock_adapter import BaoStockAdapter
            bs_adapter = BaoStockAdapter()
            chain.append(("BaoStock", bs_adapter))
        except Exception as e:
            logger.warning(f"BaoStock 加载失败: {e}")

        # TickFlow（末级保底）
        try:
            from backend.data_sources.tickflow_adapter import TickFlowAdapter
            tf_adapter = TickFlowAdapter()
            chain.append(("TickFlow", tf_adapter))
        except Exception as e:
            logger.warning(f"TickFlow 加载失败: {e}")

        self._sources = [
            _SourceStatus(name, adapter, idx)
            for idx, (name, adapter) in enumerate(chain)
        ]

        active_count = sum(1 for s in self._sources if s.adapter.available)
        logger.info(
            f"数据源链初始化完成: {len(self._sources)} 个源, "
            f"{active_count} 个可用"
        )

    def _current_source(self) -> Optional[_SourceStatus]:
        """获取当前应该使用的活跃源（最高优先级可用源）"""
        for src in sorted(self._sources, key=lambda s: s.priority):
            if src.active:
                return src
        return None

    def _try_recovery(self) -> None:
        """尝试恢复已降级的数据源（冷却期满后）"""
        for src in self._sources:
            if not src.active and src.should_retry:
                logger.info(f"尝试恢复数据源 [{src.name}]...")
                try:
                    # 用一次基础调用测试是否恢复
                    src.adapter.available
                    # 假设成功（具体恢复在 get_fund_data 的实际调用中验证）
                    src.mark_recovered()
                except Exception:
                    src.mark_degraded()

    async def get_fund_data(self, code: str, period: int = 250, fund_type: Optional[str] = None) -> FundData:
        """按优先级链获取基金数据，自动降级

        Args:
            code: 基金代码
            period: 回看天数
            fund_type: "etf"/"otc"，传递给数据源适配器用于接口路由
        """
        self._try_recovery()

        last_error: Optional[Exception] = None
        tried_sources: list[str] = []

        for src in sorted(self._sources, key=lambda s: s.priority):
            if not src.active:
                tried_sources.append(f"{src.name}(已降级)")
                continue
            if not src.adapter.available:
                src.mark_unavailable()
                tried_sources.append(f"{src.name}(不可用)")
                continue

            try:
                data = await src.adapter.get_fund_data(code, period, fund_type=fund_type)
                # 成功获取 → 如果之前降级过，标记恢复
                if not src.active:
                    src.mark_recovered()
                return data
            except Exception as e:
                last_error = e
                src.mark_degraded()
                tried_sources.append(f"{src.name}(失败: {e})")
                continue

        # 所有源都失败
        status_info = " -> ".join(tried_sources)
        logger.error(f"所有数据源获取基金数据失败 code={code}: {status_info}")
        raise last_error or RuntimeError(f"所有数据源均不可用 code={code}")

    async def get_market_indices(self) -> MarketIndices:
        """获取市场指数（从当前活跃源获取）"""
        src = self._current_source()
        if src and src.adapter.available:
            try:
                return await src.adapter.get_market_indices()
            except Exception as e:
                logger.warning(f"当前源 {src.name} 获取指数失败: {e}")

        # 当前源失败，遍历所有可用源
        for src in sorted(self._sources, key=lambda s: s.priority):
            if src.adapter.available:
                try:
                    return await src.adapter.get_market_indices()
                except Exception:
                    continue

        logger.warning("所有数据源获取市场指数失败")
        return MarketIndices()

    async def get_bond_yield(self) -> Optional[float]:
        """获取债券收益率（从当前活跃源获取）"""
        for src in sorted(self._sources, key=lambda s: s.priority):
            if src.adapter.available:
                try:
                    result = await src.adapter.get_bond_yield()
                    if result is not None:
                        return result
                except Exception:
                    continue
        return None

    @property
    def source_status(self) -> list[dict]:
        """返回各数据源当前状态（用于监控/调试）"""
        return [
            {
                "name": s.name,
                "active": s.active,
                "priority": s.priority,
                "consecutive_failures": s.consecutive_failures,
                "degraded_seconds_ago": round(time.time() - s.degraded_at, 1) if s.degraded_at else 0,
            }
            for s in self._sources
        ]
