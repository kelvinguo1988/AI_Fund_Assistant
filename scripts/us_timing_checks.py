#!/usr/bin/env python3
"""P0 补充检验：信号基准币种 + VIX 事件研究 + 收益归因

三个问题都是 P1 编码前必须定死的口径，结论落在 docs/US_TIMING_REPLAY_2026-10.md §4-§5。
同样只读本地 CSV 缓存，零上游请求。

用法: python3 scripts/us_timing_checks.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from us_timing_replay import (CASH_ANNUAL, NAMES, _series_stats, monthly,  # noqa: E402
                              positions, simulate)


def signal_basis(df):
    """同一批仓位规则，分别用美元价和人民币价做趋势判断，看翻档率"""
    out = {}
    for basis, series in (("USD px", df["px"]), ("RMB px", df["px"] * df["fx"])):
        tmp = df.copy()
        tmp["px"] = series
        out[basis] = positions(tmp, 12, 30)
    a, b = out["USD px"], out["RMB px"]
    both = [i for i in range(len(a)) if a[i] is not None and b[i] is not None]
    diff = [i for i in both if a[i] != b[i]]
    return out, len(both), len(diff)


def vix_event(df, thr=30.0):
    """VIX>thr 的决策月之后 1/3/6 个月的人民币累计收益，对比全样本"""
    cum = (1 + df["ret"]).cumprod()
    res = {}
    for horizon in (1, 3, 6):
        fwd = cum.shift(-horizon) / cum - 1
        ev, allm = fwd[df["vix"] > thr].dropna(), fwd.dropna()
        wr = float((ev > 0).mean() * 100) if len(ev) else float("nan")
        res[horizon] = dict(n=int(len(ev)), ev_cum=round(float(ev.mean() * 100), 2),
                            all_cum=round(float(allm.mean() * 100), 2), win=round(wr, 1))
    return res


def vix_cells(df, thr=30.0):
    """把 VIX>30 按'是否回落''是否已破位'切格子 —— 决定事件提示要不要带方向条件"""
    cum = (1 + df["ret"]).cumprod()
    d = pd.DataFrame({"vix": df["vix"], "prev": df["vix"].shift(1), "f1": cum.shift(-1) / cum - 1,
                      "px": df["px"], "ma12": df["px"].rolling(12).mean()}, index=df.index).dropna()
    d = d[d["f1"].notna()]
    panic = d["vix"] > thr
    falling = d["vix"] < d["prev"]
    trend_ok = d["px"] >= d["ma12"]
    cells = [("全样本基准", pd.Series(True, index=d.index)),
             ("VIX>30 全部", panic),
             ("VIX>30 且回落", panic & falling),
             ("VIX>30 且仍上行", panic & ~falling),
             ("VIX>30 且趋势完好", panic & trend_ok),
             ("VIX>30 且已破位", panic & ~trend_ok),
             ("破位+回落(矩阵加仓格)", panic & ~trend_ok & falling),
             ("破位+仍上行(最危险格)", panic & ~trend_ok & ~falling)]
    base = float(d["f1"].mean() * 100), float((d["f1"] > 0).mean() * 100)
    return base, [(lbl, d[m]) for lbl, m in cells if m.sum()]


def attribution(df, ma_n=12, vix_thr=30):
    """四段加法拆解，全部落在同一批月份上：
    买入持有 → 静态同权(纯现金拖累) → 同权择时(择时技巧，无费率) → 实盘择时(扣费率)
    """
    pos = positions(df, ma_n, vix_thr)
    s, avg_w, trades, fee, _ = simulate(df, pos, with_fee=True)
    strat = _series_stats(s, avg_w, trades, fee)
    idx = [i for i, p in enumerate(pos) if p is not None and i + 1 < len(df)]
    ideal = pd.Series([pos[i] * df["ret"].iloc[i + 1] + (1 - pos[i]) * CASH_ANNUAL / 12.0 for i in idx],
                      index=pd.DatetimeIndex([df.index[i + 1] for i in idx]))
    r_win = df.loc[ideal.index, "ret"]
    base = _series_stats(r_win, 1.0, 0, 0.0)
    idl = _series_stats(ideal, avg_w, 0, 0.0)
    stw = _series_stats(avg_w * r_win + (1 - avg_w) * CASH_ANNUAL / 12.0, avg_w, 0, 0.0)
    return dict(bh=base["cagr"], static=stw["cagr"], ideal=idl["cagr"], final=strat["cagr"],
                cash_drag=round(stw["cagr"] - base["cagr"], 2),
                timing_alpha=round(idl["cagr"] - stw["cagr"], 2),
                fee_cost=round(strat["cagr"] - idl["cagr"], 2),
                dd_bh=base["maxdd"], dd_strat=strat["maxdd"])


def main():
    print("=" * 100)
    print("① 趋势信号的基准币种：MA12/VIX30 在美元价 vs 人民币价上的翻档")
    print("=" * 100)
    for name, label in NAMES:
        df = monthly(name)
        out, n, d = signal_basis(df)
        s_usd = simulate(df, out["USD px"])[0]
        s_rmb = simulate(df, out["RMB px"])[0]
        common = s_usd.index.intersection(s_rmb.index)
        cum = lambda s: float((1 + s.loc[common]).prod() - 1)
        print(f"{label:<14} 可比 {n} 月 | 翻档 {d} 次 ({d / n * 100:.1f}%) | "
              f"总回报 美元基准 {cum(s_usd) * 100:.1f}% vs 人民币基准 {cum(s_rmb) * 100:.1f}%")

    print("\n" + "=" * 100)
    print("② VIX>30 事件研究（人民币口径 · 决策月后 1/3/6 月累计）—— 加仓档的唯一依据")
    print("=" * 100)
    print(f"{'标的':<14}{'窗口':<12}{'期':>4}{'触发次数':>9}{'事件后均值%':>13}{'全样本均值%':>13}{'正收益占比%':>13}")
    for name, label in NAMES:
        df = monthly(name)
        win = f"{df.index[0]:%Y-%m}~{df.index[-1]:%Y-%m}"
        for h, r in vix_event(df).items():
            print(f"{label:<14}{win:<12}{h:>4}{r['n']:>9}{r['ev_cum']:>13}{r['all_cum']:>13}{r['win']:>13}")

    print("\n-- 格子切分：VIX>30 里真正有增量的是'已回落'那部分 --")
    for name, label in NAMES:
        df = monthly(name)
        base, cells = vix_cells(df)
        print(f"{label}  基准 1月均值 {base[0]:+.2f}% / 胜率 {base[1]:.0f}%")
        for lbl, sub in cells:
            f1 = sub["f1"]
            print(f"  {lbl:<24}n={len(sub):>3}  均值 {f1.mean() * 100:+6.2f}%  "
                  f"胜率 {(f1 > 0).mean() * 100:>5.1f}%  最差 {f1.min() * 100:+6.1f}%")

    print("\n" + "=" * 100)
    print("③ 收益归因（MA12/VIX30）：持有 → 静态同权 → 无费择时 → 实盘")
    print("=" * 100)
    print(f"{'标的':<14}{'买入持有%':>10}{'静态同权%':>10}{'无费择时%':>10}{'实盘择时%':>10}"
          f"{'现金拖累pp':>11}{'择时alpha pp':>13}{'费率pp':>8}{'回撤 持有→实盘':>16}")
    for name, label in NAMES:
        df = monthly(name)
        a = attribution(df)
        dd_txt = "%s → %s" % (a["dd_bh"], a["dd_strat"])
        print(f"{label:<14}{a['bh']:>10}{a['static']:>10}{a['ideal']:>10}{a['final']:>10}"
              f"{a['cash_drag']:>11}{a['timing_alpha']:>13}{a['fee_cost']:>8}{dd_txt:>16}")


if __name__ == "__main__":
    main()
