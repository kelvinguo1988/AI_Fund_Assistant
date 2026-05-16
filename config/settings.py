"""
基金量化交易系统 - 配置文件
所有可配置参数集中管理，修改这里即可调整系统行为
"""
import os
from dataclasses import dataclass, field
from typing import List, Dict

# ============================================================
# 目标基金池（按自选基金填写）
# ============================================================
TARGET_FUNDS: List[Dict] = [
    # 格式: {"code": "基金代码", "name": "基金简称", "type": "主动/指数/ETF", "benchmark": "基准指数代码"}
    {"code": "110022", "name": "易方达消费行业", "type": "主动", "benchmark": "000300"},
    {"code": "519674", "name": "银河创新成长", "type": "主动", "benchmark": "000905"},
    {"code": "161725", "name": "招商中证白酒ETF", "type": "ETF", "benchmark": "399997"},
    {"code": "000961", "name": "天弘沪深300", "type": "指数", "benchmark": "000300"},
    {"code": "001643", "name": "汇添富中证500", "type": "指数", "benchmark": "000905"},
    # 添加更多目标基金...
]

# ============================================================
# 市场基准指数配置
# ============================================================
MARKET_BENCHMARKS: Dict[str, str] = {
    "000300": "沪深300",
    "000905": "中证500",
    "000001": "上证综指",
    "399001": "深证成指",
    "399006": "创业板指",
}

# 宽基ETF代码（用于获取实时指数估算）
BENCHMARK_ETF_MAP: Dict[str, str] = {
    "000300": "510300",   # 华泰沪深300ETF
    "000905": "510500",   # 南方中证500ETF
    "000001": "510210",   # 上证综指ETF
}

# ============================================================
# 量化因子权重配置（两层体系）
# ============================================================

# 第一层：市场对比因子（反映宏观市场买入时机）
MARKET_FACTOR_WEIGHTS: Dict[str, float] = {
    "pe_percentile":       0.30,   # PE历史百分位（越低越好）
    "equity_bond_ratio":   0.30,   # 股债性价比ERP（越高越好）
    "market_momentum":     0.20,   # 市场动量（20日均线偏离）
    "breadth_indicator":   0.20,   # 涨跌比（市场广度）
}

# 第二层：基金自身因子（反映基金当前状态）
FUND_FACTOR_WEIGHTS: Dict[str, float] = {
    "drawdown_from_high":  0.25,   # 距历史高点回撤幅度（越大越好买）
    "momentum_60d":        0.20,   # 近60日动量（趋势追踪）
    "momentum_20d":        0.15,   # 近20日动量（短线动量）
    "sharpe_ratio":        0.20,   # 近90日夏普比率（风险调整收益）
    "relative_strength":   0.20,   # 相对基准强度（alpha能力）
}

# 两层因子的最终整合权重
LAYER_WEIGHTS: Dict[str, float] = {
    "market_layer": 0.45,   # 市场层权重
    "fund_layer":   0.55,   # 基金层权重
}

# ============================================================
# 交易信号阈值
# ============================================================
SIGNAL_THRESHOLDS = {
    "strong_buy":   4.0,    # 强烈加仓（综合评分≥4.0）
    "buy":          3.5,    # 加仓（综合评分≥3.5）
    "hold":         2.5,    # 观望（综合评分2.5~3.5）
    "reduce":       2.0,    # 减仓（综合评分2.0~2.5）
    "sell":         0.0,    # 清仓（综合评分<2.0）
}

# 分档买入策略配置
POSITION_TIERS = {
    "strong_buy": {"suggested_pct": 0.10, "label": "加仓10%"},   # 强买：建议加仓10%
    "buy":        {"suggested_pct": 0.05, "label": "加仓5%"},    # 普通买：建议加仓5%
    "hold":       {"suggested_pct": 0.00, "label": "观望不动"},   # 观望
    "reduce":     {"suggested_pct": -0.05, "label": "减仓5%"},   # 减仓5%
    "sell":       {"suggested_pct": -0.10, "label": "减仓10%"},  # 大幅减仓
}

# ============================================================
# 数据采集配置
# ============================================================
DATA_CONFIG = {
    "tushare_token": os.getenv("TUSHARE_TOKEN", ""),       # TuShare Pro Token
    "akshare_delay": 0.5,    # AKShare请求间隔（秒）防封禁
    "retry_times":   3,      # 失败重试次数
    "retry_delay":   2,      # 重试间隔（秒）
    "timeout":       15,     # 请求超时（秒）
    "history_days":  252,    # 历史数据拉取天数（约1年）
    "pe_history_years": 5,   # PE历史百分位计算年数
}

# ============================================================
# 飞书推送配置
# ============================================================
FEISHU_CONFIG = {
    "webhook_url": os.getenv("FEISHU_WEBHOOK_URL", ""),
    "secret":      os.getenv("FEISHU_WEBHOOK_SECRET", ""),  # 可选签名
    "retry_times": 3,
    # 盘中推送时间点（24h格式，交易日触发）
    "intraday_push_times": ["09:35", "11:30", "14:00", "15:05"],
    # 重点关注分数阈值（超过此分才发单独卡片）
    "highlight_score_threshold": 3.5,
}

# ============================================================
# 调度配置
# ============================================================
SCHEDULER_CONFIG = {
    "timezone": "Asia/Shanghai",
    "trading_hours_start": "09:15",
    "trading_hours_end":   "15:30",
    # 盘中数据刷新间隔（分钟）
    "intraday_refresh_interval": 30,
    # 收盘后最终分析时间
    "close_analysis_time": "15:10",
}

# ============================================================
# 系统路径配置
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR  = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "data")
