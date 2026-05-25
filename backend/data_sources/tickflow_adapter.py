"""TickFlow 数据适配器 — 末级备用数据源

数据源级别：备用 2（当前 3 个主数据源全部失败时的最终保底）
TickFlow 是逐笔成交/盘口数据工具，并非为基金净值分析设计。
此处仅获取最简单的日线 close 数据，作为极端情况下的兜底方案。
"""

import logging
from datetime import date, timedelta
from typing import Optional

from backend.data_sources.base import BaseDataSource, FundData, MarketIndices

logger = logging.getLogger(__name__)


class TickFlowAdapter(BaseDataSource):
    """TickFlow 适配器（保底级）

    注意事项：
    - tickflow 主要提供逐笔成交数据，用于计算主力资金流向等指标
    - 本适配器仅提取简化的日线 close 数据
    - 若未安装 tickflow 或网络不可达，会快速失败并向上层传播
    """

    MAX_RETRIES = 1  # 保底源只试一次，不浪费时间

    def __init__(self) -> None:
        self._available = False
        try:
            import tickflow as tf  # type: ignore  # noqa: F401
            self._available = True
        except ImportError:
            logger.info("tickflow 未安装，跳过此数据源")
        except Exception as e:
            logger.warning(f"tickflow 初始化失败: {e}")

    @property
    def available(self) -> bool:
        return self._available

    async def get_fund_data(self, code: str, period: int = 250, fund_type: Optional[str] = None) -> FundData:
        if not self._available:
            raise RuntimeError("tickflow 不可用")

        import tickflow as tf

        fund_data = FundData(code=code)

        try:
            df = tf.kline(symbol=code, freq="1d", limit=period + 10)
            if df is None or df.empty:
                raise ValueError(f"tickflow kline 为空 code={code}")

            fund_data.close_history = df["close"].astype(float).tolist()[-period:]
            fund_data.date_history = df["date"].astype(str).tolist()[-period:] if "date" in df.columns else []
            if "volume" in df.columns:
                fund_data.volume_history = df["volume"].astype(float).tolist()[-period:]

            last = df.iloc[-1]
            fund_data.close = float(last["close"])
            fund_data.date = str(df.iloc[-1].get("date", ""))
        except Exception as e:
            logger.warning(f"tickflow 获取数据失败 code={code}: {e}")
            raise

        return fund_data

    async def get_market_indices(self) -> MarketIndices:
        return MarketIndices()

    async def get_bond_yield(self) -> Optional[float]:
        return None

