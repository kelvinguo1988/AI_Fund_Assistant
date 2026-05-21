因子名称	代码	方向	权重	所需数据	AkShare 接口
PE百分位	pe_percentile	负向	1.2	指数历史 PE	index_value_hist_funddb
股债性价比FED	fed_model	正向	1.2	指数 PE + 10 年国债收益率	同上 + bond_china_yield
动量因子	momentum_6m	正向	1.0	基金净值	fund_em_open_fund_info
波动率倒数	inv_volatility	正向	0.8	基金净值	同上
信息比率	info_ratio	正向	0.8	基金净值 + 基准指数行情	同上 + stock_zh_index_daily
MACD信号	macd_signal	正向	0.5	基金净值	fund_em_open_fund_info
最大回撤	max_drawdown	正向	0.5	基金净值	同上
规模稳定性	size_stability	正向	0.4	基金季度规模	fund_scale_open_sina
权重总和：6.4，与评分体系的 ±6.0 区间兼容，信号阈值无需调整。

二、各因子详细定义
1. PE 百分位（pe_percentile）
方向：负向（低估得分高）

数据：指数每日 PE（默认沪深300）

计算：pe_percentile = 当前 PE 在历史 5 年（1250 个交易日）中的升序排名百分位

评分映射：

≤ 0.2 → +1.0

≤ 0.4 → +0.5

≤ 0.6 → 0

≤ 0.8 → -0.5

0.8 → -1.0

2. 股债性价比 FED（fed_model）
方向：正向（股票性价比高时得分高）

数据：沪深300 PE、10年期国债收益率

计算：FED = (1/指数PE) - 国债收益率，再计算当前值在历史 3 年（756 日）中的分位数。

评分映射：

80% 分位 → +1.0

60% 分位 → +0.5

< 40% 分位 → -0.5

< 20% 分位 → -1.0

其余 → 0

3. 动量因子（momentum_6m）
方向：正向

数据：基金日净值

计算：raw = (今日净值 / 126日前净值 - 1) / (日收益率126日标准差 * sqrt(126))

评分映射：

1.0 → +1.0

0.5 → +0.5

≥ -0.5 且 ≤ 0.5 → 0

≥ -1.0 且 < -0.5 → -0.5

< -1.0 → -1.0

4. 波动率倒数（inv_volatility）
方向：正向（低波动加分）

数据：基金日净值

计算：inv_vol = 1 / (日收益率60日标准差)

评分：在同类型基金中做截面 Z-score 标准化，Z > 1.0 → +1.0，0~1 → +0.5，-1~0 → -0.5，< -1 → -1.0。

5. 信息比率（info_ratio）— 替换原 ROE 稳定性
方向：正向

数据：基金日净值、基准指数日行情（默认沪深300）

计算：

超额收益率序列 = 基金日收益率 - 基准日收益率

年化超额收益 = 超额日均值 × 252

年化跟踪误差 = 超额日标准差 × sqrt(252)

IR = 年化超额收益 / 年化跟踪误差

评分：截面 Z-score 标准化，阈值同上。

6. MACD 信号（macd_signal）
方向：正向

数据：基金日净值

计算：标准 MACD（12,26,9），使用 DIF 与 DEA 关系及柱状线变化。

评分：

DIF > DEA 且柱状线连续扩大 → +1.0

DIF > DEA 但柱缩窄 → +0.5

DIF < DEA 且柱扩大 → -1.0

其余 → 0

7. 最大回撤（max_drawdown）— 替换原量价配合
方向：正向（回撤小得分高）

数据：基金日净值

计算：MDD = 过去252日滚动最高点至最低点的最大跌幅比例

评分：在同类型基金中做截面 Z-score，取负向（即回撤越小 Z 值越高），阈值同上。

8. 规模稳定性（size_stability）
方向：正向

数据：基金季度规模（最近4个季度）

计算：

规模变异系数 = std(4季度规模) / mean(4季度规模)

稳定性得分 = 1 / 规模变异系数

附加绝对规模调整：规模在 2~50 亿之间 +0.2，超过 100 亿 -0.1

最终因子值：稳定性得分 + 调整项，再做截面 Z-score 标准化。

三、默认因子配置 JSON
以下 JSON 数组可直接存入系统数据库，作为“场外基金默认因子配置”，供 Claude Code 实现初始化。

json
[
  {
    "code": "pe_percentile",
    "name": "PE百分位",
    "direction": "negative",
    "weight": 1.2,
    "data_fields": ["index_pe"],
    "window": 1250,
    "formula": "percentile_rank(index_pe, 1250)",
    "signal_rules": [
      {"condition": "<= 0.2", "score": 1.0},
      {"condition": "<= 0.4", "score": 0.5},
      {"condition": "<= 0.6", "score": 0.0},
      {"condition": "<= 0.8", "score": -0.5},
      {"condition": "> 0.8", "score": -1.0}
    ],
    "normalization": "none"
  },
  {
    "code": "fed_model",
    "name": "股债性价比FED",
    "direction": "positive",
    "weight": 1.2,
    "data_fields": ["index_pe", "bond_yield_10y"],
    "window": 756,
    "formula": "(1/index_pe) - bond_yield_10y",
    "signal_rules": [
      {"condition": "> percentile_of(756, 0.8)", "score": 1.0},
      {"condition": "> percentile_of(756, 0.6)", "score": 0.5},
      {"condition": "< percentile_of(756, 0.4)", "score": -0.5},
      {"condition": "< percentile_of(756, 0.2)", "score": -1.0},
      {"condition": "else", "score": 0.0}
    ],
    "normalization": "rolling_percentile"
  },
  {
    "code": "momentum_6m",
    "name": "动量因子",
    "direction": "positive",
    "weight": 1.0,
    "data_fields": ["nav"],
    "window": 126,
    "formula": "(nav / shift(nav,126) - 1) / (std(returns,126) * sqrt(126))",
    "signal_rules": [
      {"condition": "> 1.0", "score": 1.0},
      {"condition": "> 0.5", "score": 0.5},
      {"condition": ">= -0.5 and <= 0.5", "score": 0.0},
      {"condition": ">= -1.0 and < -0.5", "score": -0.5},
      {"condition": "< -1.0", "score": -1.0}
    ],
    "normalization": "none"
  },
  {
    "code": "inv_volatility",
    "name": "波动率倒数",
    "direction": "positive",
    "weight": 0.8,
    "data_fields": ["nav"],
    "window": 60,
    "formula": "1 / std(returns, 60)",
    "signal_rules": [],
    "normalization": "cross_sectional_zscore",
    "zscore_thresholds": [1.0, 0, -1.0]
  },
  {
    "code": "info_ratio",
    "name": "信息比率",
    "direction": "positive",
    "weight": 0.8,
    "data_fields": ["nav", "benchmark_nav"],
    "window": 252,
    "formula": "annualize(excess_returns_mean, 252) / (std(excess_returns, 252) * sqrt(252))",
    "signal_rules": [],
    "normalization": "cross_sectional_zscore",
    "zscore_thresholds": [1.0, 0, -1.0]
  },
  {
    "code": "macd_signal",
    "name": "MACD信号",
    "direction": "positive",
    "weight": 0.5,
    "data_fields": ["nav"],
    "window": 26,
    "formula": "ema(12) - ema(26)",
    "signal_rules": [
      {"condition": "dif > dea and macd_hist_delta > 0", "score": 1.0},
      {"condition": "dif > dea and macd_hist_delta <= 0", "score": 0.5},
      {"condition": "dif < dea and macd_hist_delta < 0", "score": -1.0},
      {"condition": "else", "score": 0.0}
    ],
    "normalization": "none",
    "sub_formulas": {
      "dea": "ema(dif, 9)",
      "macd_hist": "2 * (dif - dea)",
      "macd_hist_delta": "macd_hist - shift(macd_hist, 1)"
    }
  },
  {
    "code": "max_drawdown",
    "name": "最大回撤",
    "direction": "positive",
    "weight": 0.5,
    "data_fields": ["nav"],
    "window": 252,
    "formula": "max_drawdown(nav, 252)",
    "signal_rules": [],
    "normalization": "cross_sectional_zscore",
    "zscore_thresholds": [1.0, 0, -1.0],
    "note": "回撤越小Z值越高，评分为正向"
  },
  {
    "code": "size_stability",
    "name": "规模稳定性",
    "direction": "positive",
    "weight": 0.4,
    "data_fields": ["fund_size_quarterly"],
    "window": 4,
    "window_unit": "quarter",
    "formula": "1 / (std(size, 4) / mean(size, 4)) + size_bonus(size)",
    "signal_rules": [],
    "normalization": "cross_sectional_zscore",
    "zscore_thresholds": [1.0, 0, -1.0],
    "size_bonus_rule": "if 2e8 <= size <= 5e9 then 0.2 elif size > 1e10 then -0.1 else 0"
  }
]
四、AkShare 数据接口实现要点
因子	关键接口	备注
pe_percentile	ak.index_value_hist_funddb(symbol="000300")	字段用 pe 或 市盈率，取最近 1250 行
fed_model	同上 + ak.bond_china_yield(start_date="2021-01-01")	国债收益率取 10年期
动量/波动率/最大回撤/MACD	ak.fund_em_open_fund_info(fund="003305", indicator="单位净值走势")	返回日净值表
info_ratio	上述基金净值 + ak.stock_zh_index_daily(symbol="sh000300")	基准用沪深300收盘价
size_stability	ak.fund_scale_open_sina(symbol="003305")	返回中取“总募集规模”或“最近总份额”字段，需连续季度数据
所有接口均免费且无认证门槛，符合 AkShare 全覆盖要求。

