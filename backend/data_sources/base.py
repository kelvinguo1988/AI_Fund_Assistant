"""抽象数据源接口"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FundData:
    """基金数据统一结构"""
    code: str                            # 基金代码
    name: str = ""                       # 基金名称
    date: str = ""                       # 数据日期 YYYY-MM-DD
    # ── 估值指标 ──
    pe: Optional[float] = None           # 市盈率
    pb: Optional[float] = None           # 市净率
    # ── 价格数据 ──
    close: Optional[float] = None        # 收盘价/净值
    close_history: list[float] = field(default_factory=list)   # 收盘价序列
    # ── 成交量 ──
    volume: Optional[float] = None       # 当日成交量
    volume_history: list[float] = field(default_factory=list)  # 成交量序列
    # ── 指数数据 ──
    index_close: Optional[float] = None  # 关联指数收盘价
    # ── 债券收益率 ──
    bond_yield: Optional[float] = None   # 10年国债收益率
    # ── 日期序列 ──
    date_history: list[str] = field(default_factory=list)      # 日期序列


@dataclass
class MarketIndices:
    """市场指数数据"""
    date: str = ""
    sh_composite: Optional[float] = None   # 上证综指
    sz_component: Optional[float] = None   # 深证成指
    cyb: Optional[float] = None            # 创业板指
    hs300: Optional[float] = None          # 沪深300


class BaseDataSource(ABC):
    """数据源抽象基类"""

    @property
    def available(self) -> bool:
        """数据源当前是否可用（默认 True）"""
        return True

    @abstractmethod
    async def get_fund_data(self, code: str, period: int = 250) -> FundData:
        """获取基金数据

        Args:
            code: 基金代码 如 "510300"
            period: 回看天数，默认 250 个交易日（约 1 年）

        Returns:
            FundData 基金数据对象
        """
        ...

    @abstractmethod
    async def get_market_indices(self) -> MarketIndices:
        """获取市场主要指数数据

        Returns:
            MarketIndices 市场指数对象
        """
        ...

    @abstractmethod
    async def get_bond_yield(self) -> Optional[float]:
        """获取 10 年期国债收益率

        Returns:
            国债收益率，获取失败返回 None
        """
        ...
