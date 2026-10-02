"""批量信号回测结果 ORM 模型 — 自动回测任务落库"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class BacktestResult(Base):
    """自动全量回测的逐基金汇总结果（不含逐日 points，详情走单基金实时回测）

    以 fund_id 为唯一键逐行覆盖：每只基金回测完成即更新本行，
    前端批量结果页可实时看到逐只完成时间；下一轮周期性覆盖。
    """
    __tablename__ = "backtest_results"
    __table_args__ = (
        UniqueConstraint("fund_id", name="uq_backtest_result_fund"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fund_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    fund_code: Mapped[str] = mapped_column(String(20), nullable=False)
    fund_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    period: Mapped[int] = mapped_column(Integer, nullable=False, default=365)
    effectiveness_window: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    total_nav_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_strategy_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    excess_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    signal_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    avg_effectiveness: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    buy_effectiveness: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sell_effectiveness: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    effectiveness_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── 2026-10-02 第二批 Q6：三条基线 / 样本下限 / 选择偏差标注 ──
    # 三条基线与两个超额口径（excess_return 仍是对满仓持有的差值，保留兼容）
    baseline_buy_hold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_static_half: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    excess_vs_static_half: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # 非 hold 信号天数 + 信号覆盖交易日占比
    signal_count_non_hold: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    signal_coverage_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # 样本不足 → 只有 caveat，没有结论
    low_sample: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    caveat: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 回测区间真实起点（= 首次被分析的交易日）与该时点池子规模
    coverage_start_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    coverage_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pool_size_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 本行结果使用的仓位口径（1=仓位延续 / 0=旧行为），便于跨轮比较时识别口径
    carry_position: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # 该基金回测完成时间（逐只更新，批量页展示）
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 单只失败原因（成功为空）
    error: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 结果是否有效（净值拉取失败等 → False，保留错误信息）
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
