"""基金评审团 — 多视角规则引擎 + 严格 Signal 输出（2026-11-22）

借鉴 UZI-Skill（github.com/wbh604/UZI-Skill）investor-panel 的评审团架构，
但有三处**有意的差异**：

1. 评委是**确定性规则引擎**而非 LLM role-play：可测试、零 LLM 成本、
   每个 pass/fail 都引用具体数据；AI 只负责最终叙事（skill guidance 层）。
2. 评委回答"基金"而非个股：个股财务指标（ROE/PEG）不适用，规则全部引用
   基金可观测字段（估值分位/温度/回撤/覆盖率/规模/资金流/赛道暴露）。
3. Signal 可落 advice_log(source='panel')，与调仓引擎进**同一套命中率考核
   闭环** —— 30 个净值日后能对比"评审团共识 vs 调仓引擎"谁更准，这是
   advice 自进化闭环的扩展。

数据纪律：全部输入来自库内落库数据或已有类缓存，**零新增上游请求**；
数据缺失的评委显式弃权（skip），绝不给"数据暂无，参考同业"式假结论。
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.analysis_result import AnalysisResult
from backend.models.backtest_result import BacktestResult
from backend.models.fund import Fund
from backend.models.fund_holding import FundHolding
from backend.models.fund_quarterly import FundQuarterly
from backend.utils.timezone import now_beijing

logger = logging.getLogger(__name__)

# ── 派系定义（权重按基金类型差异化，STYLE-WEIGHTING 简化版）──────────
# G=宽基配置派 Q=风险控制派 A=主动质量派 R=赛道结构派 M=资金面观察员
GROUP_WEIGHTS: dict[str, dict[str, float]] = {
    # 股票/混合型：配置与选股并重，赛道结构次之，资金流只作观察
    "equity": {"G": 0.30, "A": 0.30, "Q": 0.25, "R": 0.10, "M": 0.05},
    # 债券/固收+：风控与规模健康占大头，赛道/选股权重压到最低
    "bond": {"G": 0.20, "A": 0.10, "Q": 0.55, "R": 0.05, "M": 0.10},
    # QDII/其他：配置视角（温度/格雷厄姆对海外仅弱参考）+ 风控为主
    "overseas": {"G": 0.20, "A": 0.20, "Q": 0.45, "R": 0.05, "M": 0.10},
    "default": {"G": 0.25, "A": 0.25, "Q": 0.30, "R": 0.10, "M": 0.10},
}

# Signal → advice_log.action 映射（共识层用）
CONSENSUS_TO_ACTION = {
    "加码倾向": "buy",
    "持有观望": "hold",
    "减配倾向": "sell",
}


def fund_group_kind(fund_type_official: Optional[str], name: str) -> str:
    """基金类型 → 派系权重表键（与 advice 的 benchmark_kind 判定同源思路）"""
    t = (fund_type_official or "") + (name or "")
    if "债" in t or "固收" in t:
        return "bond"
    if any(k in t for k in ("QDII", "海外", "纳斯达克", "标普", "全球", "美元")):
        return "overseas"
    if "股票" in t or "混合" in t:
        return "equity"
    return "default"


def _signal(
    judge_id: str, name: str, group: str,
    direction: str, score: float, confidence: int,
    verdict: str, reasoning: str,
    passes: list[str], fails: list[str],
) -> dict:
    """统一 Signal 结构（借鉴 UZI 的 Pydantic Signal，去掉个股专属字段）"""
    return {
        "id": judge_id,
        "name": name,
        "group": group,
        "signal": direction,          # bullish | neutral | bearish | skip
        "score": round(score, 1),     # -100 ~ +100，与 direction 同向
        "confidence": int(confidence),
        "verdict": verdict,
        "reasoning": reasoning,
        "pass": passes,
        "fail": fails,
    }


def _skip(judge_id: str, name: str, group: str, why: str) -> dict:
    return _signal(judge_id, name, group, "skip", 0.0, 0, "弃权",
                   why, [], [])


@dataclass
class PanelContext:
    """评审输入快照 —— 全部来自库内/缓存，缺什么评委就弃什么权"""
    fund: Fund
    analysis: Optional[AnalysisResult] = None
    backtest: Optional[BacktestResult] = None
    fund_size: Optional[float] = None          # 亿元（最新季度）
    temperature: Optional[dict] = None          # 市场温度 payload
    valuation_hint: Optional[dict] = None       # match_fund_hint 结果
    pool_total: int = 0                         # 活跃基金数
    same_exposure_count: int = 0                # 池内同首赛道基金数（含自己）
    primary_exposure: Optional[str] = None      # 首赛道名
    exposure_ratio: Optional[float] = None      # 首赛道持仓占比 0~1
    style_drift: bool = False                   # 双标签风格漂移警示
    inflow_available: bool = False              # 资金面可用（ETF或持仓个股）
    inflow_weighted_pct: Optional[float] = None  # 加权净流入（%）>0 流入
    inflow_basis: str = ""                      # 数据依据描述
    factors: dict = field(default_factory=dict)  # factor_scores 解析结果


# ── 评委规则（确定性，每条 pass/fail 引用具体数据）────────────────────

def judge_temp_discipline(ctx: PanelContext) -> dict:
    """G1 温度纪律官 — 市场层位置定投节奏（温度是市场层，对单基金只给节奏参考）"""
    t = ctx.temperature
    temp = (t or {}).get("temperature")
    if temp is None:
        return _skip("temp_discipline", "温度纪律官", "G", "温度计数据不可用（乐咕缓存缺失）")
    zone = (t or {}).get("zone", "")
    if temp < 30.0:
        return _signal("temp_discipline", "温度纪律官", "G", "bullish", 60.0, 80,
                       "加码参考", f"市场温度 {temp}°（{zone}）——定投加码的市场层窗口",
                       [f"温度 {temp}° < 30 低估线"], [])
    if temp >= 70.0:
        return _signal("temp_discipline", "温度纪律官", "G", "bearish", -60.0, 80,
                       "减配参考", f"市场温度 {temp}°（{zone}）——分批止盈窗口，新增买入应收缩",
                       [], [f"温度 {temp}° ≥ 70 高估线"])
    return _signal("temp_discipline", "温度纪律官", "G", "neutral", 0.0, 70,
                   "按计划执行", f"市场温度 {temp}°（{zone}）——正常持有节奏，不做额外动作",
                   [], [])


def judge_graham_cross(ctx: PanelContext) -> dict:
    """G2 格雷厄姆交叉官 — 股债性价比四象限（利率扭曲必须点破）"""
    g = ((ctx.temperature or {}).get("graham") or {})
    cross = (g.get("cross") or {}) if g.get("available") else {}
    st = cross.get("state")
    if not st:
        return _skip("graham_cross", "格雷厄姆交叉官", "G",
                     "格雷厄姆块不可用（国债序列缺失或样本不足）")
    g_pct = g.get("percentile_10y")
    if st == "true_cheap":
        return _signal("graham_cross", "格雷厄姆交叉官", "G", "bullish", 70.0, 85,
                       "加码参考", f"双指标同向低估（格雷厄姆分位 {g_pct}%）——{cross.get('note','')}",
                       ["温度与格雷厄姆分位双低"], [])
    if st == "rate_distorted_cheap":
        return _signal("graham_cross", "格雷厄姆交叉官", "G", "neutral", 0.0, 75,
                       "维持节奏", f"（格雷厄姆分位 {g_pct}%）{cross.get('note','')}",
                       [], ["格雷厄姆分位偏高系利率扭曲，不作为加码依据"])
    if st == "rare_review":
        return _signal("graham_cross", "格雷厄姆交叉官", "G", "bearish", -40.0, 60,
                       "人工复核", f"（格雷厄姆分位 {g_pct}%）{cross.get('note','')}",
                       [], ["温度偏高且股债性价比异常，规则引擎不给出自动结论"])
    return _signal("graham_cross", "格雷厄姆交叉官", "G", "bearish", -70.0, 85,
                   "减配参考", f"双指标同向偏高（格雷厄姆分位 {g_pct}%）——{cross.get('note','')}",
                   [], ["温度与格雷厄姆分位双高"])


def judge_index_valuation(ctx: PanelContext) -> dict:
    """G3 指数估值官 — 基准指数 PE 分位（名称/基准近似映射）"""
    hint = ctx.valuation_hint
    if not hint:
        return _skip("index_valuation", "指数估值官", "G",
                     "基金名称/基准不在支持指数内（沪深300/中证500/上证50/中证1000/中证800）")
    level = hint.get("level")
    conf = 70 if not hint.get("approximate") else 55
    if level == "positive":
        return _signal("index_valuation", "指数估值官", "G", "bullish", 60.0, conf,
                       "低估窗口", hint.get("message", ""),
                       ["基准指数 PE 分位进入低估区"], ["近似映射" if hint.get("approximate") else ""])
    if level == "warning":
        return _signal("index_valuation", "指数估值官", "G", "bearish", -60.0, conf,
                       "高估警示", hint.get("message", ""),
                       [], ["基准指数 PE 分位进入高估区"])
    return _signal("index_valuation", "指数估值官", "G", "neutral", 0.0, conf,
                   "合理区间", hint.get("message", ""), [], [])


def judge_drawdown(ctx: PanelContext) -> dict:
    """Q1 回撤纪律官 — 用落库回测的 max_drawdown（零请求）"""
    bt = ctx.backtest
    if bt is None or bt.max_drawdown is None:
        return _skip("drawdown_discipline", "回撤纪律官", "Q",
                     "无回测落库数据（请先在回测页跑一次或等周日自动回测）")
    dd = float(bt.max_drawdown)
    conf = 70
    notes_cov = []
    cov = bt.signal_coverage_ratio
    if cov is not None and cov < 0.30:
        conf -= 20
        notes_cov.append(f"信号覆盖率 {cov:.0%} < 30%，回撤样本可信度打折")
    if dd >= 25.0:
        return _signal("drawdown_discipline", "回撤纪律官", "Q", "bearish", -55.0, conf,
                       "高波动资产", f"历史最大回撤 {dd:.1f}% ≥ 25%——持仓前先确认自己能拿住",
                       notes_cov, [f"最大回撤 {dd:.1f}%"])
    if dd >= 15.0:
        return _signal("drawdown_discipline", "回撤纪律官", "Q", "neutral", 0.0, conf,
                       "中等波动", f"历史最大回撤 {dd:.1f}%（15~25% 中间带）",
                       notes_cov, [])
    return _signal("drawdown_discipline", "回撤纪律官", "Q", "bullish", 30.0, max(55, conf),
                   "回撤温和", f"历史最大回撤 {dd:.1f}% < 15%——低回撤不等于会涨，仅持有体验好",
                   [f"最大回撤 {dd:.1f}%"] + notes_cov, [])


def judge_volatility(ctx: PanelContext) -> dict:
    """Q2 波动率官 — inv_volatility 截面因子（池内相对）"""
    v = ctx.factors.get("inv_volatility")
    if v is None:
        return _skip("volatility_guard", "波动率官", "Q", "因子分缺失（无分析数据或该基金未入截面池）")
    if v >= 0.5:
        return _signal("volatility_guard", "波动率官", "Q", "bullish", 40.0, 65,
                       "波动受控", f"波动率因子 {v:+.2f}（池内波动偏低侧）",
                       [f"inv_volatility {v:+.2f}"], [])
    if v <= -0.5:
        return _signal("volatility_guard", "波动率官", "Q", "bearish", -40.0, 65,
                       "高波动", f"波动率因子 {v:+.2f}（池内波动偏高侧）",
                       [], [f"inv_volatility {v:+.2f}"])
    return _signal("volatility_guard", "波动率官", "Q", "neutral", 0.0, 60,
                   "波动中性", f"波动率因子 {v:+.2f}（池内中间带）", [], [])


def judge_size_health(ctx: PanelContext) -> dict:
    """Q3 规模健康官 — 最新季度规模（迷你基金清盘/大额赎回冲击风险）

    fund_size 存储单位是**元**（pingzhongdata 亿元 × 1e8），判定阈值按亿换算。
    """
    s_raw = ctx.fund_size
    if s_raw is None:
        return _skip("size_health", "规模健康官", "Q", "无季度规模数据（季报未同步）")
    s = s_raw / 1e8  # 元 → 亿
    if s < 2.0:
        return _signal("size_health", "规模健康官", "Q", "bearish", -65.0, 75,
                       "迷你规模", f"最新规模 {s:.2f} 亿 < 2 亿——存在清盘/大额赎回冲击净值风险",
                       [], [f"规模 {s:.2f} 亿"])
    if s < 10.0:
        return _signal("size_health", "规模健康官", "Q", "neutral", 0.0, 60,
                       "规模适中", f"最新规模 {s:.2f} 亿（2~10 亿）", [], [])
    return _signal("size_health", "规模健康官", "Q", "bullish", 25.0, 55,
                   "规模充裕", f"最新规模 {s:.2f} 亿 > 10 亿（规模不是买点，仅排除一类风险）",
                   [f"规模 {s:.2f} 亿"], [])


def judge_alpha_quality(ctx: PanelContext) -> dict:
    """A1 信号质量官 — 最新分析信号方向与强度"""
    a = ctx.analysis
    if a is None:
        return _skip("alpha_quality", "信号质量官", "A", "无分析数据（请先跑一次分析）")
    score = float(a.weighted_score or 0.0)
    direction = a.signal_direction or ""
    pool = None
    try:
        fs = json.loads(a.factor_scores or "{}")
        pool = fs.get("_meta", {}).get("pool_size")
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    conf = 70
    pool_note = ""
    if pool is not None and pool < 20:
        conf -= 15
        pool_note = f"；截面池仅 {pool} 只，标准化不稳"
    # 信号五档的动态阈值已在 analysis 里，这里用阈值兜底判断
    buy_th = a.dynamic_buy_threshold if a.dynamic_buy_threshold is not None else 1.5
    sell_th = -(a.dynamic_sell_threshold if a.dynamic_sell_threshold is not None else 3.0)
    if score >= buy_th:
        return _signal("alpha_quality", "信号质量官", "A", "bullish", score * 20, conf,
                       "信号看多", f"加权分 {score:+.2f} ≥ 动态买点 {buy_th}（{a.signal_strength}）{pool_note}",
                       [f"weighted_score {score:+.2f}"], [])
    if score <= sell_th:
        return _signal("alpha_quality", "信号质量官", "A", "bearish", score * 20, conf,
                       "信号看空", f"加权分 {score:+.2f} ≤ 动态卖点 {sell_th}（{a.signal_strength}）{pool_note}",
                       [], [f"weighted_score {score:+.2f}"])
    return _signal("alpha_quality", "信号质量官", "A", "neutral", 0.0, conf,
                   "观望区", f"加权分 {score:+.2f} 在动态阈值之间{pool_note}", [], [])


def judge_excess_return(ctx: PanelContext) -> dict:
    """A2 超额收益官 — 回测落库的相对基线超额"""
    bt = ctx.backtest
    if bt is None or bt.excess_return is None:
        return _skip("excess_return", "超额收益官", "A", "无回测落库数据")
    ex = float(bt.excess_return)
    conf = 60
    notes = []
    if bt.low_sample:
        conf -= 20
        notes.append("回测样本不足（low_sample），超额数字可信度打折")
    if ex >= 3.0:
        return _signal("excess_return", "超额收益官", "A", "bullish", 45.0, conf,
                       "有超额", f"回测期相对基线超额 {ex:+.1f}pp{('；' + notes[0]) if notes else ''}",
                       [f"超额 {ex:+.1f}pp"] + notes, [])
    if ex <= -3.0:
        return _signal("excess_return", "超额收益官", "A", "bearish", -45.0, conf,
                       "跑输基线", f"回测期相对基线超额 {ex:+.1f}pp——持有体验差于无脑持有{('；' + notes[0]) if notes else ''}",
                       [], [f"超额 {ex:+.1f}pp"] + notes)
    return _signal("excess_return", "超额收益官", "A", "neutral", 0.0, conf,
                   "超额中性", f"回测期相对基线超额 {ex:+.1f}pp（±3pp 内≈噪声）", notes, [])


def judge_signal_consistency(ctx: PanelContext) -> dict:
    """A3 一致性官 — 信号方向 vs 最大正/负因子方向是否自洽（矛盾必须呈现）"""
    a = ctx.analysis
    if a is None:
        return _skip("signal_consistency", "一致性官", "A", "无分析数据")
    try:
        fs = json.loads(a.factor_scores or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return _skip("signal_consistency", "一致性官", "A", "因子分解析失败")
    items = [(k, float(v)) for k, v in fs.items()
             if isinstance(v, (int, float)) and not k.startswith("_")]
    if not items:
        return _skip("signal_consistency", "一致性官", "A", "因子分为空")
    items.sort(key=lambda x: -abs(x[1]))
    top_k, top_v = items[0]
    direction = a.signal_direction or ""
    direction_score = 1.0 if direction in ("buy", "强烈买入") else \
        -1.0 if direction in ("sell", "强烈卖出") else 0.0
    if direction_score == 0.0:
        return _signal("signal_consistency", "一致性官", "A", "neutral", 0.0, 60,
                       "观望信号", f"信号方向为观望；最大因子 {top_k} {top_v:+.2f}", [], [])
    consistent = (top_v > 0) == (direction_score > 0)
    if consistent:
        return _signal("signal_consistency", "一致性官", "A",
                       "bullish" if direction_score > 0 else "bearish",
                       top_v * 15, 75, "信号自洽",
                       f"信号 {direction} 与最大因子 {top_k}({top_v:+.2f}) 方向一致",
                       ["信号与主导因子同向"], [])
    return _signal("signal_consistency", "一致性官", "A", "neutral", 0.0, 65,
                   "内部分歧",
                   f"信号 {direction} 但最大因子 {top_k}({top_v:+.2f}) 反向——指标打架，本身就是信息",
                   [], ["信号与主导因子反向"])


def judge_exposure_focus(ctx: PanelContext) -> dict:
    """R1 赛道集中官 — 首赛道持仓占比"""
    exp, ratio = ctx.primary_exposure, ctx.exposure_ratio
    if not exp or ratio is None:
        return _skip("exposure_focus", "赛道集中官", "R", "无赛道暴露数据（季报持仓未解析）")
    if ratio >= 0.40:
        return _signal("exposure_focus", "赛道集中官", "R", "bearish", -40.0, 60,
                       "单一赛道集中", f"首赛道「{exp}」占比 {ratio:.0%} ≥ 40%——基金涨跌将高度绑定单一赛道",
                       [], [f"{exp} {ratio:.0%}"])
    if ratio >= 0.15:
        return _signal("exposure_focus", "赛道集中官", "R", "neutral", 0.0, 55,
                       "赛道适中", f"首赛道「{exp}」占比 {ratio:.0%}", [], [])
    return _signal("exposure_focus", "赛道集中官", "R", "neutral", 0.0, 55,
                   "敞口分散", f"首赛道「{exp}」占比仅 {ratio:.0%}", [], [])


def judge_style_drift(ctx: PanelContext) -> dict:
    """R2 风格漂移官 — 双标签的官方定位 vs 当前暴露"""
    if ctx.style_drift:
        return _signal("style_drift", "风格漂移官", "R", "bearish", -35.0, 65,
                       "风格漂移", "基金官方类型与当前持仓暴露不一致（风格漂移警示）——历史业绩对未来的参考价值下降",
                       [], ["官方定位与实际暴露不符"])
    return _signal("style_drift", "风格漂移官", "R", "neutral", 0.0, 55,
                   "风格一致", "官方类型与当前暴露一致，无漂移警示", ["无风格漂移"], [])


def judge_pool_crowding(ctx: PanelContext) -> dict:
    """R3 池内拥挤官 — 同赛道基金在自选池中的聚集度（用户自己的组合视角）"""
    exp, n, total = ctx.primary_exposure, ctx.same_exposure_count, ctx.pool_total
    if not exp or total <= 0:
        return _skip("pool_crowding", "池内拥挤官", "R", "池内无赛道暴露数据可比")
    if n >= 6:
        return _signal("pool_crowding", "池内拥挤官", "R", "bearish", -40.0, 60,
                       "自选池拥挤", f"自选池 {total} 只中有 {n} 只同属「{exp}」——同涨同跌，分散是假的",
                       [], [f"{exp} × {n} 只"])
    if n <= 2:
        return _signal("pool_crowding", "池内拥挤官", "R", "bullish", 25.0, 55,
                       "赛道稀缺", f"自选池内「{exp}」仅 {n} 只——该赛道敞口在组合里稀缺",
                       [f"{exp} × {n} 只"], [])
    return _signal("pool_crowding", "池内拥挤官", "R", "neutral", 0.0, 55,
                   "集中度正常", f"自选池内「{exp}」{n} 只（池共 {total} 只）", [], [])


def judge_money_flow(ctx: PanelContext) -> dict:
    """M1 资金流观察官 — ETF 用快照主力净占比；场外用持仓个股聚合（零新增请求）"""
    if not ctx.inflow_available or ctx.inflow_weighted_pct is None:
        return _skip("money_flow", "资金流观察官", "M",
                     "资金面数据不可用（东财快照熔断/降级源无此列/持仓未同步）")
    v = ctx.inflow_weighted_pct
    basis = ctx.inflow_basis
    # 观察员权重低、置信度保守：盘中资金流噪音大，只给方向观察
    if v > 0.5:
        return _signal("money_flow", "资金流观察官", "M", "bullish", 30.0, 55,
                       "资金流入", f"加权主力净流入 {v:+.2f}%（{basis}）——流入是滞后确认指标，不是领先信号",
                       [f"净流入 {v:+.2f}%"], [])
    if v < -0.5:
        return _signal("money_flow", "资金流观察官", "M", "bearish", -30.0, 55,
                       "资金流出", f"加权主力净流入 {v:+.2f}%（{basis}）",
                       [], [f"净流出 {v:+.2f}%"])
    return _signal("money_flow", "资金流观察官", "M", "neutral", 0.0, 55,
                   "资金中性", f"加权主力净流入 {v:+.2f}%（{basis}）——噪声带内", [], [])


ALL_JUDGES = [
    judge_temp_discipline, judge_graham_cross, judge_index_valuation,
    judge_drawdown, judge_volatility, judge_size_health,
    judge_alpha_quality, judge_excess_return, judge_signal_consistency,
    judge_exposure_focus, judge_style_drift, judge_pool_crowding,
    judge_money_flow,
]


# ── 输入快照收集 ─────────────────────────────────────────────────────

async def _gather_context(db: AsyncSession, fund_id: int) -> PanelContext:
    fund = await db.get(Fund, fund_id)
    if fund is None:
        raise ValueError(f"基金 {fund_id} 不存在")

    # 最新分析结果
    analysis = (await db.execute(
        select(AnalysisResult).where(AnalysisResult.fund_id == fund_id)
        .order_by(AnalysisResult.analysis_date.desc())
    )).scalars().first()

    # 最新回测落库
    backtest = (await db.execute(
        select(BacktestResult).where(BacktestResult.fund_id == fund_id)
    )).scalars().first()

    # 最新季度规模
    quarterly = (await db.execute(
        select(FundQuarterly).where(FundQuarterly.fund_id == fund_id)
        .order_by(FundQuarterly.report_date.desc())
    )).scalars().first()
    ctx_fund_size = quarterly.fund_size if quarterly else None

    factors: dict = {}
    if analysis is not None:
        try:
            factors = json.loads(analysis.factor_scores or "{}")
        except (json.JSONDecodeError, TypeError, ValueError):
            factors = {}

    # 市场温度 + 指数估值（类缓存 1h；miss 时会各发一次已有链路请求）
    from backend.services.market_temperature_service import MarketTemperatureService
    from backend.services.index_valuation_service import IndexValuationService
    try:
        temperature = await MarketTemperatureService.get_temperature()
    except Exception as e:
        logger.warning(f"评审团取温度失败: {e}")
        temperature = None
    try:
        await IndexValuationService.get_valuations()
        valuation_hint = IndexValuationService.match_fund_hint(
            fund.name or "", fund.benchmark_text or ""
        )
    except Exception as e:
        logger.warning(f"评审团取指数估值失败: {e}")
        valuation_hint = None

    # 暴露标签（exposure_tags JSON 字符串：[{"tag": 名, "ratio": 占比}, ...]）
    primary_exposure, exposure_ratio, style_drift = None, None, False
    try:
        exps = json.loads(fund.exposure_tags or "[]")
        if exps and isinstance(exps, list):
            first = exps[0]
            if isinstance(first, dict):
                primary_exposure = first.get("tag") or first.get("name")
                r = first.get("ratio")
                exposure_ratio = float(r) if r is not None else None
        drift_flags = json.loads(fund.tags or "[]") if (fund.tags or "").startswith("[") else []
        style_drift = any("漂移" in str(x) for x in (drift_flags or [])) or \
            "漂移" in (fund.tags or "")
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # 池内同赛道计数
    pool_total = 0
    same_exposure_count = 0
    if primary_exposure:
        rows = (await db.execute(select(Fund.exposure_tags, Fund.tags)
                                 .where(Fund.status == "active"))).all()
        pool_total = len(rows)
        for et, tg in rows:
            if primary_exposure in (et or ""):
                same_exposure_count += 1

    # 资金面（零新增请求：ETF 快照缓存 / 东财个股 inflow 衍生缓存）
    inflow_available, inflow_weighted_pct, inflow_basis = False, None, ""
    from backend.services.fund_realtime_service import FundRealtimeService
    is_etf_like = (fund.code or "")[:2] in ("51", "15", "56", "58", "15")
    if is_etf_like:
        etf_map = FundRealtimeService.get_etf_inflow([fund.code])
        if etf_map:
            inflow_available = True
            inflow_weighted_pct = etf_map[fund.code]
            inflow_basis = "ETF 快照主力净占比（当日）"
    else:
        # 场外基金：最新季报持仓 × 个股主力净流入聚合（权重加权）
        holds = (await db.execute(
            select(FundHolding).where(FundHolding.fund_id == fund_id)
            .order_by(FundHolding.report_date.desc().nullslast(),
                      FundHolding.ratio.desc())
        )).scalars().all()
        latest_q = holds[0].quarter_label if holds else None
        top = [h for h in holds if h.quarter_label == latest_q][:10]
        codes = [h.stock_code for h in top if h.stock_code]
        inflow_map = FundRealtimeService.get_stock_inflow(codes) if codes else None
        if inflow_map and top:
            matched = [h for h in top if h.stock_code in inflow_map]
            total_ratio = sum(h.ratio or 0 for h in matched)
            if matched and total_ratio > 0:
                # 持仓占比加权净流入：Σ(占净值比% × 个股净额) / 100 → 百万口径
                # 除以最新规模折算成"占基金净资产 %"——观察员只报方向
                size = ctx_fund_size
                if size:
                    weighted = sum(
                        (h.ratio or 0) * inflow_map[h.stock_code] for h in matched
                    ) / 100.0
                    inflow_available = True
                    inflow_weighted_pct = weighted / (size * 1e6) * 100.0
                    inflow_basis = (
                        f"季报[{latest_q}] top{len(top)} 持仓 × 东财个股主力净额"
                        f"（覆盖持仓占比 {total_ratio:.0f}%，等权近似）"
                    )

    return PanelContext(
        fund=fund,
        analysis=analysis,
        backtest=backtest,
        fund_size=ctx_fund_size,
        temperature=temperature,
        valuation_hint=valuation_hint,
        pool_total=pool_total,
        same_exposure_count=same_exposure_count,
        primary_exposure=primary_exposure,
        exposure_ratio=exposure_ratio,
        style_drift=style_drift,
        inflow_available=inflow_available,
        inflow_weighted_pct=inflow_weighted_pct,
        inflow_basis=inflow_basis,
        factors={k: float(v) for k, v in factors.items()
                 if isinstance(v, (int, float)) and not str(k).startswith("_")},
    )


# ── 共识与汇总 ───────────────────────────────────────────────────────

def _consensus(signals: list[dict], weights: dict[str, float]) -> dict:
    """按派系权重把投票折算成共识（弃权不进分子，进分母说明）"""
    voted = [s for s in signals if s["signal"] in ("bullish", "neutral", "bearish")]
    skipped = [s for s in signals if s["signal"] == "skip"]
    wsum = 0.0
    acc = 0.0
    bull = bear = 0
    for s in voted:
        w = weights.get(s["group"], 0.05)
        contribution = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}[s["signal"]]
        acc += contribution * w
        wsum += w
        if s["signal"] == "bullish":
            bull += 1
        elif s["signal"] == "bearish":
            bear += 1
    consensus_score = (acc / wsum) if wsum > 0 else 0.0
    if consensus_score >= 0.25:
        verdict = "加码倾向"
    elif consensus_score <= -0.25:
        verdict = "减配倾向"
    else:
        verdict = "持有观望"
    return {
        "score": round(consensus_score, 3),
        "verdict": verdict,
        "bull": bull,
        "bear": bear,
        "neutral": len(voted) - bull - bear,
        "abstain": len(skipped),
        "split": round(max(0.0, 1.0 - abs(consensus_score)), 3),
        "split_note": (
            "评审团分歧显著——分歧本身就是信息，建议读每位评委的 pass/fail 再决定"
            if abs(consensus_score) < 0.15 and bull + bear >= 4 else ""
        ),
    }


def summary_md(panel: dict) -> str:
    """评审结果 → markdown 摘要（AI 叙事与前端展示共用）"""
    c = panel["consensus"]
    lines = [
        f"### {panel['fund_name']}（{panel['fund_code']}）评审团",
        f"**共识：{c['verdict']}**（得分 {c['score']:+.2f} ｜ 看多 {c['bull']} · 中性 {c['neutral']} · "
        f"看空 {c['bear']} · 弃权 {c['abstain']} ｜ 分歧度 {c['split']:.0%}）",
        "",
        "| 评委 | 派 | 结论 | 置信 | 依据 |",
        "|---|---|---|---|---|",
    ]
    for s in panel["signals"]:
        reason = (s["reasoning"] or "")[:60]
        lines.append(
            f"| {s['name']} | {s['group']} | {s['verdict']} | {s['confidence']}% | {reason} |"
        )
    if c.get("split_note"):
        lines.append("")
        lines.append(f"> ⚠️ {c['split_note']}")
    return "\n".join(lines)


class FundPanelService:
    """评审团入口：单基金 convene + 可选落库进命中率闭环"""

    @staticmethod
    async def convene(db: AsyncSession, fund_id: int) -> dict:
        ctx = await _gather_context(db, fund_id)
        signals = [fn(ctx) for fn in ALL_JUDGES]
        kind = fund_group_kind(
            getattr(ctx.fund, "fund_type_official", None), ctx.fund.name or ""
        )
        weights = GROUP_WEIGHTS.get(kind, GROUP_WEIGHTS["default"])
        consensus = _consensus(signals, weights)
        panel = {
            "fund_id": ctx.fund.id,
            "fund_code": ctx.fund.code,
            "fund_name": ctx.fund.name,
            "fund_kind": kind,
            "weights": weights,
            "signals": signals,
            "consensus": consensus,
            "generated_at": now_beijing().strftime("%Y-%m-%d %H:%M:%S"),
        }
        panel["summary_md"] = summary_md(panel)
        return panel

    @staticmethod
    def log_consensus(panel: dict) -> Optional[int]:
        """共识 verdict 落 advice_log（source='panel'）→ 进命中率闭环

        与调仓引擎共用去重（同基金同方向一天一条）；reasons 用"[评审团]"前缀
        + 分歧摘要，方便在 advice 列表里区分来源。
        """
        from backend.services.advice_learning_service import AdviceLearningStore
        c = panel["consensus"]
        action = CONSENSUS_TO_ACTION.get(c["verdict"])
        if not action:
            return None
        reasons = (
            f"[评审团] {c['verdict']}：看多{c['bull']} 中性{c['neutral']} 看空{c['bear']} "
            f"弃权{c['abstain']}，分歧度 {c['split']:.0%}"
        )
        score = c["score"] * 100.0
        return AdviceLearningStore().log_advice(
            panel["fund_code"], action, reasons, score, source="panel"
        )
