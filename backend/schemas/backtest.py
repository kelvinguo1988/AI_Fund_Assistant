"""信号回测 Pydantic Schema"""

from typing import List, Optional

from pydantic import BaseModel


class BacktestPoint(BaseModel):
    """回测单日数据点"""
    date: str
    nav: float                                # 单位净值（分红复权口径的收益由取数层保证）
    nav_return: float                         # 净值累计收益率 (%) = 满仓买入持有基线
    strategy_return: float                    # 策略累计收益率 (%)
    signal_direction: Optional[str] = None    # buy / sell / hold / None
    signal_strength: Optional[str] = None     # heavy_buy / moderate_buy / ...
    weighted_score: Optional[float] = None    # 因子加权评分
    signal_effectiveness: Optional[float] = None  # 信号有效性评分 (0~100)
    # ── 2026-10-02 第二批 Q6：口径可视化 ──
    position_applied: Optional[float] = None  # 当日实际生效仓位（前一日信号 or 延续）
    baseline_static_half: Optional[float] = None  # 静态半仓基准累计收益 (%，"什么都不做")


class BacktestSummary(BaseModel):
    """回测结果汇总"""
    fund_code: str
    fund_name: str
    period: int                               # 回测天数
    total_nav_return: float                   # 净值总收益 (%)
    total_strategy_return: float              # 策略总收益 (%)
    excess_return: float                      # 策略 − 满仓买入持有 (%)，保留旧字段名
    max_drawdown: float                       # 策略净值最大回撤 (%，负值)
    signal_count: int                         # 有信号（含 hold）的交易日数
    total_days: int                           # 净值序列总天数
    effectiveness_window: int = 5             # 有效性评估窗口 (交易日)
    avg_effectiveness: Optional[float] = None       # 整体平均有效性
    buy_effectiveness: Optional[float] = None       # 买入信号平均有效性
    sell_effectiveness: Optional[float] = None      # 卖出信号平均有效性
    effectiveness_rate: Optional[float] = None      # 有效率 (%)

    # ── 2026-10-02 第二批 Q6：三条基线 / 样本下限 / 选择偏差标注 ──
    baseline_buy_hold: float = 0.0                  # 满仓买入持有 (= total_nav_return)
    baseline_static_half: float = 0.0               # 恒 50% 仓位、零调仓（不计费）
    excess_vs_static_half: float = 0.0              # 头号指标：策略 − 静态半仓
    signal_count_non_hold: int = 0                  # buy/sell 信号天数（不含 hold）
    signal_coverage_ratio: float = 0.0              # 有信号交易日 / 回测总交易日
    low_sample: bool = False                        # 样本不足 → 只出 caveat 不出结论
    caveat: Optional[str] = None
    coverage_start_date: Optional[str] = None       # 首个信号交易日（回测区间实际起点）
    coverage_days: int = 0                          # 首个~末个信号的交易日跨度
    pool_size_at: Optional[int] = None              # coverage_start 时点已入池基金数
    carry_position: bool = True                     # 本轮生效的仓位口径（可回滚）
    points: List[BacktestPoint]


class BacktestBatchItem(BaseModel):
    """自动全量回测的逐基金汇总（不含逐日 points）"""
    model_config = {"protected_namespaces": ()}

    fund_id: int
    fund_code: str
    fund_name: str
    period: int
    effectiveness_window: int
    total_nav_return: Optional[float] = None
    total_strategy_return: Optional[float] = None
    excess_return: Optional[float] = None
    max_drawdown: Optional[float] = None
    signal_count: Optional[int] = None
    avg_effectiveness: Optional[float] = None
    buy_effectiveness: Optional[float] = None
    sell_effectiveness: Optional[float] = None
    effectiveness_rate: Optional[float] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    ok: bool = True

    # ── Q6 新口径字段（旧行 / 旧前端读不到即不显示，天然向后兼容）──
    baseline_buy_hold: Optional[float] = None
    baseline_static_half: Optional[float] = None
    excess_vs_static_half: Optional[float] = None
    signal_count_non_hold: Optional[int] = None
    signal_coverage_ratio: Optional[float] = None
    low_sample: Optional[bool] = None
    caveat: Optional[str] = None
    coverage_start_date: Optional[str] = None
    coverage_days: Optional[int] = None
    pool_size_at: Optional[int] = None
    carry_position: Optional[bool] = None
