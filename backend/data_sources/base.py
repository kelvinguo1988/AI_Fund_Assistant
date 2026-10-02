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


class NoDataError(Exception):
    """该代码在本数据源确实没有记录（确定性空结果），不是数据源故障

    与网络/风控类异常的区别必须保留：降级链过去把任何异常都当成源坏了，
    于是池子里一只已清盘的代码就能让 AKShare 整源降级 5 分钟，后续所有
    基金改打备源并触发告警（2026-10-01 审查 P1）。管理器见到本异常时
    不降级、不换源（两家源覆盖的是同一个公募基金全集，空结果是代码的属性），
    直接上抛交调用方按"无数据"跳过。
    """


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
    benchmark_date_history: list[str] = field(default_factory=list)  # 基准日期序列（与收盘价一一对应，用于按日期对齐）
    # ── 债券收益率 ──
    bond_yield: Optional[float] = None   # 10年国债收益率
    # ── 规模数据 ──
    fund_size_history: list[float] = field(default_factory=list)  # 基金季度规模序列
    # ── 日期序列 ──
    date_history: list[str] = field(default_factory=list)      # 日期序列
    # ── 季度扩展数据（标的质量过滤用）──
    # 每个元素: {"report_date": "2025-03-31", "effective_date": "2025-05-01",
    #            "fund_size": 1e9, "stock_position_ratio": 85.0,
    #            "institution_holding_ratio": 30.5, "insider_holding_shares": 1000.0}
    quarterly_history: list[dict] = field(default_factory=list)


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
        """数据源当前是否可用（默认 True）

        合约：本属性**必须是纯内存判断**，不得发起网络请求 —— 它在 async
        路径上被高频读取（降级链 / manager 每次取数）。真实连通性探测放
        `async probe()`（2026-09-29 审查 P0：JoinQuant 曾在 property 里同步
        调 is_auth() 阻塞事件循环）。
        """
        return True

    async def probe(self) -> bool:
        """冷却期后的主动恢复探测（默认沿用 available）

        子类实现要点：**单次、轻量、自带失败冷却**，不得成为新的请求风暴源
        （保留既有防封禁预算，不放松限流/退避）。返回 False 时 manager 会
        重置降级计时继续冷却，而非立即复原。
        """
        return self.available

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

        Raises:
            NoDataError: 该代码在本源确认无记录（不得当作源故障）
            Exception: 请求级失败（超时/风控/认证），降级链据此切换数据源
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
