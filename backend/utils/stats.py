"""共用统计小工具 — 目前只有分位排名

存在理由（Q13）：估值分位在两处各写了一遍，而且定义不同 ——
`index_valuation_service` 用严格 `<`（当前值本身不算在内），`market_regime_service` 用 `<=`。
同一个指数的"分位"在仪表盘显示 62%、在策略侧显示 63%，看不出谁对。
分位这种"到处都要用又到处写错"的量，只留一个定义点。
"""

from typing import Iterable, Optional


def percentile_rank_inclusive(values: Iterable[float], current: float) -> Optional[float]:
    """当前值在样本中的分位（0~1）：返回 **<= current 的占比**（含当前值自身）

    - 采用"含等号"口径：样本里全是同一个值时分位为 1.0 而不是 0.0，
      与业界"现在比历史上多少比例的时候更贵"的直觉一致。
    - 空样本或全非数值 → None（调用方自己决定中性值怎么给）。
    - 要百分数请自行 ×100。
    """
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    return sum(1 for v in vals if v <= current) / len(vals)
