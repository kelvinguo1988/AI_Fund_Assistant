import akshare as ak
import pandas as pd
import numpy as np
import time

# ========== 0. 防御：获取基金名称映射（避开 fund_name_em） ==========
print("正在加载基金列表...")
try:
    df_list = ak.fund_open_fund_rank_em(symbol="全部")
    name_map = dict(zip(df_list['基金代码'].astype(str), df_list['基金简称']))
    print(f"✅ 加载完成，共 {len(name_map)} 只基金")
except Exception as e:
    print(f"⚠️ 列表获取失败: {e}")
    name_map = {}

# ========== 1. 获取净值（参数用 symbol，不是 fund） ==========
code = "016874"  
code = "017103" 
code = "018123" 
code = "007491" 
code = "025209" 
print(f"\n正在获取 {code} 净值数据...")

try:
    # ❗ 关键修正：参数名是 symbol，不是 fund
    df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    
    # 防御：空数据
    if df is None or len(df) == 0:
        raise ValueError("返回空数据")
    
    df['净值日期'] = pd.to_datetime(df['净值日期'])
    df = df.sort_values('净值日期').reset_index(drop=True)
    df['returns'] = df['单位净值'].pct_change()
    
    print(f"✅ 获取成功，共 {len(df)} 条记录")
    print(f"   日期范围: {df['净值日期'].min().date()} ~ {df['净值日期'].max().date()}")
    
except Exception as e:
    print(f"❌ 获取失败: {e}")
    print("常见原因：1) 参数名用了 fund 而不是 symbol；2) 基金代码不存在；3) 网络超时")
    exit()

# ========== 2. 数据长度检查 ==========
min_required = 252  # 最少需要252个交易日（1年）
if len(df) < min_required:
    print(f"⚠️ 数据不足 {min_required} 个交易日（仅 {len(df)} 天），因子可能失真")
    # 继续计算，但部分长周期因子会返回 NaN

# ========== 3. 计算因子（全部基于净值序列） ==========
nav = df['单位净值']
ret = df['returns']

factors = {}

# 短期动量 (20日)
factors['short_momentum'] = (nav / nav.shift(20) - 1).iloc[-1]

# 中期动量 (60日)
factors['mid_momentum'] = (nav / nav.shift(60) - 1).iloc[-1]

# 波动率倒数 (60日)
vol = (ret.rolling(60).std() * np.sqrt(252)).iloc[-1]
factors['inv_volatility'] = 1 / (vol + 0.0001) if pd.notna(vol) else np.nan

# 回撤修复度 (252日)
if len(nav) >= 252:
    rolling_max = nav.rolling(252).max()
    factors['drawdown_recovery'] = (nav / rolling_max).iloc[-1]
else:
    factors['drawdown_recovery'] = np.nan

# 收益风险比 (60日)
rr = (ret.rolling(60).mean() / (ret.rolling(60).std() + 0.0001)).iloc[-1]
factors['return_risk_ratio'] = rr if pd.notna(rr) else np.nan

# 动量加速度 (60日)
mom20 = nav / nav.shift(20) - 1
mom60 = nav / nav.shift(60) - 1
factors['momentum_accel'] = (mom20 - mom60).iloc[-1]

# 趋势一致性 (60日)
factors['trend_consistency'] = np.sign(mom20.iloc[-1]) * 0.5 + np.sign(mom60.iloc[-1]) * 0.5

# ========== 4. 输出结果 ==========
print("\n" + "="*60)
fund_name = name_map.get(code, "未知")
print(f"基金: {code} ({fund_name})")
print("="*60)

for k, v in factors.items():
    status = "✅" if pd.notna(v) else "⚠️ NaN"
    print(f"{k:22s}: {v:+.6f}  {status}")

# ========== 5. 综合择时信号 ==========
# 过滤掉 NaN 的因子
valid = {k: v for k, v in factors.items() if pd.notna(v)}
if len(valid) < 4:
    print("\n❌ 有效因子不足，无法生成信号（数据太短或基金太新）")
else:
    score = (
        1.2 * np.sign(valid.get('short_momentum', 0)) +
        1.2 * np.sign(valid.get('mid_momentum', 0)) +
        1.0 * (1 if valid.get('inv_volatility', 0) > 5 else 0) +
        0.8 * (valid.get('drawdown_recovery', 0.9) - 0.9) * 10 +
        0.8 * np.sign(valid.get('return_risk_ratio', 0)) +
        0.5 * np.sign(valid.get('momentum_accel', 0)) +
        0.5 * valid.get('trend_consistency', 0)
    )
    
    print(f"\n{'='*60}")
    print(f"综合得分: {score:.3f}")
    if score >= 0.30:
        print("📌 信号: 买入")
    elif score <= -0.30:
        print("📌 信号: 卖出")
    else:
        print("📌 信号: 观望")
