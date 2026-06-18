"""信号回测 Pydantic Schema"""

from typing import List, Optional

from pydantic import BaseModel


class BacktestPoint(BaseModel):
    """回测单日数据点"""
    date: str
    nav: float                                # 单位净值
    nav_return: float                         # 净值累计收益率 (%)
    strategy_return: float                    # 策略累计收益率 (%)
    signal_direction: Optional[str] = None    # buy / sell / hold / None
    signal_strength: Optional[str] = None     # heavy_buy / moderate_buy / ...
    weighted_score: Optional[float] = None    # 因子加权评分
    signal_effectiveness: Optional[float] = None  # 信号有效性评分 (0~100)


class BacktestSummary(BaseModel):
    """回测结果汇总"""
    fund_code: str
    fund_name: str
    period: int                               # 回测天数
    total_nav_return: float                   # 净值总收益 (%)
    total_strategy_return: float              # 策略总收益 (%)
    excess_return: float                      # 超额收益 (%)
    max_drawdown: float                       # 策略最大回撤 (%)
    signal_count: int                         # 有信号的天数
    total_days: int                           # 净值序列总天数
    effectiveness_window: int = 5             # 有效性评估窗口 (交易日)
    avg_effectiveness: Optional[float] = None       # 整体平均有效性
    buy_effectiveness: Optional[float] = None       # 买入信号平均有效性
    sell_effectiveness: Optional[float] = None      # 卖出信号平均有效性
    effectiveness_rate: Optional[float] = None      # 有效率 (%)
    points: List[BacktestPoint]
