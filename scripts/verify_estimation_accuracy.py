#!/usr/bin/env python3
"""持仓自算估值模型准确度回测

方法：对每只样本基金，用最新季报 top10 持仓（akshare fund_portfolio_hold_em）
× 个股当日涨跌幅（stock_zh_a_hist），按归一法估算每日涨跌幅，
与基金实际净值涨跌幅（fund_open_fund_info_em）逐日对比。

指标：MAE（平均绝对误差）、RMSE、方向命中率（估算与实际同涨同跌）

用法: python3 scripts/verify_estimation_accuracy.py [基金代码...]
默认样本: 000001 161725 110022
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

import requests as _requests

# macOS 系统代理会被 urllib 读走导致东财行情 ProxyError，回测强制直连
_orig_merge = _requests.Session.merge_environment_settings

def _no_proxy_merge(self, url, proxies, stream, verify, cert):
    settings = _orig_merge(self, url, proxies, stream, verify, cert)
    settings["proxies"] = {}
    return settings

_requests.Session.merge_environment_settings = _no_proxy_merge

from backend.patch.eastmoney_patch import apply_patch

apply_patch()  # 东财接口需 UA/Referer/超时补丁

import akshare as ak


def get_holdings(code: str) -> list[tuple[str, float]]:
    """最新季报 top10 持仓 → [(stock_code, ratio%)]"""
    df = ak.fund_portfolio_hold_em(symbol=code, date="2026")
    if df is None or df.empty:
        return []
    latest_q = df["季度"].iloc[0]
    q = df[df["季度"] == latest_q].head(10)
    return list(zip(q["股票代码"].astype(str), q["占净值比例"].astype(float)))


def get_nav_returns(code: str, days: int = 20) -> pd.DataFrame:
    """基金近 N 日净值涨跌幅 → DataFrame(date, actual_pct)"""
    nav = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    nav = nav.sort_values("净值日期").tail(days + 1).reset_index(drop=True)
    nav["actual_pct"] = nav["单位净值"].pct_change() * 100
    return nav[["净值日期", "actual_pct"]].dropna()


def get_stock_pcts(stock_codes: list[str], days: int = 20) -> dict[str, pd.Series]:
    """个股近 N 日每日涨跌幅 → {code: Series(date→pct%)}"""
    out = {}
    for sc in stock_codes:
        try:
            h = ak.stock_zh_a_hist(symbol=sc, period="daily", adjust="qfq")
            h = h.sort_values("日期").tail(days + 1)
            s = h.set_index("日期")["涨跌幅"].astype(float)
            out[sc] = s
        except Exception as e:
            print(f"  [warn] {sc} 行情失败: {e}")
        time.sleep(0.5)
    return out


def main(codes: list[str], days: int = 20):
    all_results = []
    for code in codes:
        print(f"\n=== 基金 {code} ===")
        holdings = get_holdings(code)
        if not holdings:
            print("  无持仓数据，跳过")
            continue
        cov = sum(r for _, r in holdings)
        print(f"  持仓 {len(holdings)} 只, 覆盖率 {cov:.1f}%")

        nav = get_nav_returns(code, days)
        stock_pcts = get_stock_pcts([c for c, _ in holdings], days)

        est_rows = []
        for _, row in nav.iterrows():
            d = row["净值日期"]
            w_sum, w_tot = 0.0, 0.0
            for sc, ratio in holdings:
                s = stock_pcts.get(sc)
                if s is None or d not in s.index:
                    continue
                w_sum += ratio * s[d]
                w_tot += ratio
            est = w_sum / w_tot if w_tot > 0 else None
            est_rows.append(est)

        nav["est_pct"] = est_rows
        valid = nav.dropna(subset=["est_pct", "actual_pct"])
        if valid.empty:
            print("  无有效对比日")
            continue

        err = valid["est_pct"] - valid["actual_pct"]
        mae = err.abs().mean()
        rmse = float(np.sqrt((err ** 2).mean()))
        hit = ((valid["est_pct"] > 0) == (valid["actual_pct"] > 0)).mean() * 100
        bias = err.mean()
        print(f"  对比天数: {len(valid)}, MAE: {mae:.3f}pp, RMSE: {rmse:.3f}pp, "
              f"方向命中: {hit:.0f}%, 系统偏差: {bias:+.3f}pp")
        all_results.append((code, len(valid), mae, rmse, hit, bias))

        # 展示误差最大的 3 天
        worst = valid.assign(err=err.abs()).nlargest(3, "err")
        for _, w in worst.iterrows():
            print(f"    {w['净值日期']}: est={w['est_pct']:+.2f}% actual={w['actual_pct']:+.2f}%")

    if all_results:
        print("\n=== 汇总 ===")
        df = pd.DataFrame(all_results, columns=["code", "days", "MAE", "RMSE", "hit%", "bias"])
        print(df.to_string(index=False))
        print(f"\n整体 MAE: {df['MAE'].mean():.3f}pp, 整体方向命中: {df['hit%'].mean():.0f}%")


if __name__ == "__main__":
    sample = sys.argv[1:] or ["000001", "161725", "110022"]
    main(sample)
