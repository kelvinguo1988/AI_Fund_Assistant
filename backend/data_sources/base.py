"""抽象数据源接口"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ETF 代码前缀规则（与 fund_service._guess_fund_type 保持一致，共享此常量）
ETF_CODE_PREFIX = re.compile(r"^(51|15|58|159|588|512|513|515|516|517|518|560|561|562|563|588)")


def guess_fund_type(code: str) -> str:
    """根据基金代码前缀推测类型（场内 ETF / 场外 OTC）"""
    return "etf" if ETF_CODE_PREFIX.match(code) else "otc"


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
    benchmark_history: list[float] = field(default_factory=list)  # 基准指数（沪深300）收盘价序列
    # ── 债券收益率 ──
    bond_yield: Optional[float] = None   # 10年国债收益率
    # ── 规模数据 ──
    fund_size_history: list[float] = field(default_factory=list)  # 基金季度规模序列
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
    async def get_fund_data(self, code: str, period: int = 250, fund_type: Optional[str] = None) -> FundData:
        """获取基金数据

        Args:
            code: 基金代码 如 "510300"
            period: 回看天数，默认 250 个交易日（约 1 年）
            fund_type: 基金类型 "etf" / "otc"，用于直接路由到正确数据接口。
                       None 时由适配器根据代码前缀自动判断。

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
