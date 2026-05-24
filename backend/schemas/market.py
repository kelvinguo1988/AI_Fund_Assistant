"""市场概况 Schema — 资金流、板块排行、信号汇总"""

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel
from backend.schemas.analysis import AnalysisResultOut


class CapitalFlow(BaseModel):
    """资金流数据项（净额单位：亿元）"""
    net_amount: float = 0.0        # 净流入额
    net_ratio: float = 0.0         # 净占比 (%)
    super_large_net: float = 0.0   # 超大单净流入
    large_net: float = 0.0         # 大单净流入
    medium_net: float = 0.0        # 中单净流入
    small_net: float = 0.0         # 小单净流入


class MarketCapitalFlow(BaseModel):
    """大盘资金流概况"""
    date: str = ""
    sh_index: Optional[float] = None   # 上证指数
    sh_change: Optional[float] = None  # 上证涨跌幅
    sz_index: Optional[float] = None   # 深证指数
    sz_change: Optional[float] = None  # 深证涨跌幅
    main_flow: CapitalFlow = CapitalFlow()  # 主力资金流


class SectorFlowItem(BaseModel):
    """板块资金流排行项"""
    sector_name: str = ""
    change_pct: float = 0.0         # 涨跌幅
    main_net_inflow: float = 0.0    # 主力净流入额（亿元）
    main_net_ratio: float = 0.0     # 主力净占比(%)
    top_stock: str = ""             # 主力净流入最大股


class SectorFlowRanking(BaseModel):
    """板块资金流排行"""
    timeframe: str = ""              # 当天 / 周 / 月 / 季
    by_inflow: list[SectorFlowItem] = []     # 流入TOP
    by_outflow: list[SectorFlowItem] = []    # 流出TOP


class HSGTFlow(BaseModel):
    """沪深港通资金流"""
    north_net_buy: float = 0.0   # 北向净买入（亿元）
    south_net_buy: float = 0.0   # 南向净买入（亿元）
    date: str = ""


class SignalSummary(BaseModel):
    """信号汇总"""
    total: int = 0
    buy_count: int = 0
    sell_count: int = 0
    hold_count: int = 0
    top_buy: list[AnalysisResultOut] = []
    top_sell: list[AnalysisResultOut] = []


class MarketAdvDecline(BaseModel):
    """涨跌分布"""
    up_count: int = 0        # 上涨家数
    down_count: int = 0      # 下跌家数
    total_count: int = 0     # 总计数


class MarketTurnover(BaseModel):
    """两市成交额"""
    sse_amount: float = 0.0      # 沪市成交额（亿元）
    szse_amount: float = 0.0     # 深市成交额（亿元）
    total_amount: float = 0.0    # 合计（亿元）
    prev_total_amount: float = 0.0   # 上一交易日合计（亿元）
    change_pct: float = 0.0      # 较上一日涨跌幅


class MarketSummaryOut(BaseModel):
    """市场概况完整输出"""
    date: str
    signals: SignalSummary
    market_flow: Optional[MarketCapitalFlow] = None
    sector_flow: list[SectorFlowRanking] = []
    hsgt_flow: Optional[HSGTFlow] = None
    adv_decline: Optional[MarketAdvDecline] = None
    turnover: Optional[MarketTurnover] = None
    updated_at: Optional[str] = None  # 缓存数据时间
