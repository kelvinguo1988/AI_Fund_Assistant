#!/usr/bin/env python3
"""美股择时月度回放（P0 判据）—— 人民币口径 + 真实场外费率 + 配对基线

结论落在 docs/US_TIMING_REPLAY_2026-10.md，这里是复现脚本。

只读本地 CSV 缓存（data/us_timing_cache/），**零上游请求**；缓存怎么来的见 docs 那份
§6。改 START/阈值只动本文件常量。

与四份外部报告（WorkBuddy/2026-10-11）的口径差异，四点都是对本项目致命的：
1. 收益走人民币口径：QDII 持有人看到的是 美元价回 × USDCNY，2022 年两者差 9 个点。
2. 现金按中国货币基金计息（默认 1.5%/年），不是 0%，也不是美元 3M 国债 —— 赎回回款回国内。
3. 调仓成本用本仓 holding_fee 的真实阶梯（申购 0.1% / 赎回 <365天 0.5%、≥365天 0.25%），不是 10bps。
4. 无未来函数：第 t 月末用截至 t 的数据定仓，仓位只作用于 t+1 月收益。

对比纪律：择时策略只在预热后的那段样本上存在，所以基线一律按**同一批月份**配对重算，
否则拿 2004 年起的买入持有去比 2006 年才起步的策略，差额全是样本长度差。

用法: python3 scripts/us_timing_replay.py
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CACHE = Path(os.environ.get("US_TT_CACHE",
                            Path(__file__).resolve().parent.parent / "data" / "us_timing_cache"))

DIV_YIELD = 0.016      # 美股股息率常数（近似总回报）
CASH_ANNUAL = 0.015    # 减仓后人民币现金收益（货币基金）
SUB_FEE = 0.001        # 申购费（一折后典型）
REDEEM_LT365 = 0.005   # 持有<365天赎回费
REDEEM_GE365 = 0.0025  # 持有≥365天赎回费

OUT_OF_DEFENSE = 0.0   # 占位：文档里的"全退"档，用于复现其 whipsaw 结论

NAMES = (("inx", "标普500"), ("ndx", "纳斯达克100"), ("ixic", "纳指综合"))


def monthly(name):
    """日线缓存 → 月末点（美元价 / USDCNY / VIX）+ 人民币口径月收益"""
    px = pd.read_csv(CACHE / f"{name}.csv", parse_dates=["date"])
    px = px.sort_values("date").set_index("date")
    px_close = px["close"].resample("ME").last()
    last_obs = px["close"].resample("ME").apply(lambda s: s.dropna().index.max())

    fx = pd.read_csv(CACHE / "usdcny_all.csv", parse_dates=["日期"])
    fx = fx.rename(columns={"日期": "date"})[["date", "美元"]].dropna()
    fx["美元"] = pd.to_numeric(fx["美元"], errors="coerce")
    fx = fx.dropna().sort_values("date").set_index("date")["美元"].resample("ME").last()

    vix = pd.read_csv(CACHE / "vix_cboe.csv", skiprows=1, header=None,
                      names=["date", "o", "h", "l", "c"])
    vix["date"] = pd.to_datetime(vix["date"], format="%m/%d/%Y")
    vix = vix.dropna().sort_values("date").set_index("date")["c"].resample("ME").last()

    df = pd.concat([px_close.rename("px"), fx.rename("fx"), vix.rename("vix")], axis=1).dropna()
    df = df[df["px"] > 0]
    # 进行中、未收盘的当月不参与（月末最后观察日距月末 >5 天）
    while len(df):
        k = df.index[-1]
        lo = last_obs.get(k, pd.NaT)
        if pd.notna(lo) and (k - lo).days > 5:
            df = df.iloc[:-1]
            continue
        break
    usd = df["px"].pct_change() + DIV_YIELD / 12.0
    df["ret"] = (1 + usd) * (1 + df["fx"].pct_change()) - 1
    df.loc[df.index[0], "ret"] = 0.0
    return df


def positions(df, ma_n, vix_thr=None, defense=0.5, panic_addback=0.75):
    """月末决策 → 下月仓位。vix_thr=None 时退化为两档纯趋势。None = 预热不足"""
    ma = df["px"].rolling(ma_n).mean()
    out = []
    for i in range(len(df)):
        if pd.isna(ma.iloc[i]):
            out.append(None)
            continue
        if df["px"].iloc[i] >= ma.iloc[i]:
            out.append(1.0)
            continue
        if vix_thr is None:
            out.append(defense)
        else:
            out.append(panic_addback if df["vix"].iloc[i] > vix_thr else defense)
    return out


def _series_stats(s, avg_w, trades, fee_pct, defense_months=None):
    n = len(s) / 12.0
    total = float((1 + s).prod() - 1)
    cagr = (1 + total) ** (1 / n) - 1 if n > 0 else float("nan")
    nav = (1 + s).cumprod()
    dd = float((nav / nav.cummax() - 1).min())
    sharpe = float(s.mean() / s.std() * np.sqrt(12)) if s.std() > 1e-12 else float("nan")
    return dict(months=len(s), cagr=round(cagr * 100, 2), total=round(total * 100, 1),
                maxdd=round(dd * 100, 1), sharpe=round(sharpe, 2),
                avg_pos=round(avg_w * 100, 1), trades=trades, fee_pct=fee_pct,
                def_months=defense_months, start=s.index[0], end=s.index[-1])


def simulate(df, pos, with_fee=True):
    """按仓位序列累计净值；仓位 i 作用于第 i+1 月收益 → (月收益, 均仓, 调仓次, 累计费率%, 防御月)"""
    idx = [i for i, p in enumerate(pos) if p is not None and i + 1 < len(df)]
    if not idx:
        return None
    nav_series, prev, last_chg, fee_sum, trades = [], None, idx[0], 0.0, 0
    for i in idx:
        w, r = pos[i], df["ret"].iloc[i + 1]
        cost = 0.0
        if prev is not None and abs(w - prev) > 1e-9:
            trades += 1
            if with_fee:
                days = (df.index[i + 1] - df.index[last_chg]).days
                if w > prev:
                    cost = (w - prev) * SUB_FEE
                else:
                    cost = (prev - w) * (REDEEM_LT365 if days < 365 else REDEEM_GE365)
                fee_sum += cost
            last_chg = i + 1
        nav_series.append(w * r + (1 - w) * CASH_ANNUAL / 12.0 - cost)
        prev = w
    s = pd.Series(nav_series, index=pd.DatetimeIndex([df.index[i + 1] for i in idx]))
    avg_w = float(np.mean([pos[i] for i in idx]))
    def_m = sum(1 for i in idx if pos[i] < 1.0)
    return s, avg_w, trades, round(fee_sum * 100, 2), def_m


def compare(df, label, pos, with_fee=True):
    """把策略与**同一批月份**的买入持有 / 静态同权重配对"""
    got = simulate(df, pos, with_fee)
    if not got:
        return None
    s, avg_w, trades, fee, def_m = got
    win = df.loc[s.index]
    bh = _series_stats(win["ret"], 1.0, 0, 0.0)
    static_ret = avg_w * win["ret"] + (1 - avg_w) * CASH_ANNUAL / 12.0
    stw = _series_stats(static_ret, avg_w, 0, 0.0)
    st = _series_stats(s, avg_w, trades, fee, def_m)
    st.update(
        label=label,
        vs_bh_pp=round(st["cagr"] - bh["cagr"], 2),
        vs_static_pp=round(st["cagr"] - stw["cagr"], 2),
        dd_bh=bh["maxdd"],
        dd_improve=round((1 - abs(st["maxdd"]) / abs(bh["maxdd"])) * 100, 1) if bh["maxdd"] else None,
        bh=bh, static=stw,
    )
    return st


def segment_cmp(df, pos, a, b):
    """分段：只在 [a,b] 内、且策略有仓位的月份上比较"""
    got = simulate(df, pos)
    if not got:
        return None
    s = got[0]
    seg = s[(s.index >= a) & (s.index <= b)]
    if len(seg) < 3:
        return None
    bh = df.loc[seg.index, "ret"]
    nav_s, nav_b = (1 + seg).cumprod(), (1 + bh).cumprod()
    dd = lambda x: float((x / x.cummax() - 1).min())
    return dict(months=len(seg), cum_strat=round((nav_s.iloc[-1] - 1) * 100, 1),
                cum_bh=round((nav_b.iloc[-1] - 1) * 100, 1),
                dd_strat=round(dd(nav_s) * 100, 1), dd_bh=round(dd(nav_b) * 100, 1))


def run(name):
    df = monthly(name)
    rows = []
    for ma_n in (10, 12, 18):
        for vt in (25, 30, 35):
            r = compare(df, f"三档 MA{ma_n} VIX>{vt}", positions(df, ma_n, vt))
            if r:
                rows.append(r)
    for ma_n in (10, 12, 18):
        r = compare(df, f"纯趋势 MA{ma_n} 半仓", positions(df, ma_n, None))
        if r:
            rows.append(r)
    r = compare(df, "纯趋势 MA12 全退(复现文档)", positions(df, 12, None, defense=OUT_OF_DEFENSE))
    if r:
        rows.append(r)
    full_bh = _series_stats(df["ret"].iloc[1:], 1.0, 0, 0.0)
    return df, rows, full_bh


SEGMENTS = [("2008 GFC", "2007-07-31", "2009-06-30"),
            ("2015-16 人民币贬值段", "2015-06-30", "2016-06-30"),
            ("2018-Q4", "2018-09-30", "2019-03-31"),
            ("2020 新冠", "2020-01-31", "2020-09-30"),
            ("2022 加息熊市", "2022-02-28", "2022-12-31"),
            ("2023-2026 AI 牛市", "2023-01-31", "2026-12-31")]

# 预注册判据（跑之前就写在 docs 里，不许事后调）：
#   ① MA12/VIX30 相对静态同权超额 ≥ 0
#   ② 最大回撤改善 ≥ 15%
#   ③ MA10/12/18 三档在 VIX30 上结论同向
# 三条全过才允许出仓位建议；任一不过 → 只做展示层。
GATE_DD_IMPROVE = 15.0
GATE_NOISE_PP = 0.5


def main():
    for name, label in NAMES:
        df, rows, full_bh = run(name)
        print("\n" + "=" * 112)
        print(f"{label} ({name})  人民币口径 · 美元含息{DIV_YIELD * 100:.1f}% · "
              f"现金{CASH_ANNUAL * 100:.1f}%/年 · 场外费率"
              f" | 数据 {df.index[0]:%Y-%m} ~ {df.index[-1]:%Y-%m}（{len(df)} 月末点）")
        print(f"全窗口买入持有参照：CAGR {full_bh['cagr']}% / 最大回撤 {full_bh['maxdd']}% / "
              f"夏普 {full_bh['sharpe']}")
        print("=" * 112)
        print(f"{'方案':<24}{'月数':>5}{'窗口起':>9}{'CAGR%':>8}{'总回%':>8}{'回撤%':>8}"
              f"{'夏普':>6}{'均仓%':>7}{'防御月':>7}{'调仓':>5}{'费率%':>7}{'vs持有pp':>10}{'vs静态pp':>9}{'回撤改善%':>10}")
        for r in sorted(rows, key=lambda x: -x["cagr"]):
            print(f"{r['label']:<24}{r['months']:>5}{r['start'].strftime('%Y-%m'):>9}"
                  f"{r['cagr']:>8}{r['total']:>8}{r['maxdd']:>8}{r['sharpe']:>6}"
                  f"{r['avg_pos']:>7}{r['def_months']:>7}{r['trades']:>5}{r['fee_pct']:>7}"
                  f"{r['vs_bh_pp']:>10}{r['vs_static_pp']:>9}{r['dd_improve']:>10}")

        print("\n-- 分段（相对同窗口买入持有）--")
        pos12 = positions(df, 12, 30)
        pos12_t = positions(df, 12, None)
        print(f"{'区间':<22}{'月数':>5}{'三档累计%':>11}{'持有累计%':>11}{'三档回撤%':>11}"
              f"{'持有回撤%':>11}{'趋势半仓累计%':>15}")
        for lbl, a, b in SEGMENTS:
            s1 = segment_cmp(df, pos12, a, b)
            s2 = segment_cmp(df, pos12_t, a, b)
            if s1:
                t2 = f'{s2["cum_strat"]:>15}' if s2 else f'{"—":>15}'
                print(f"{lbl:<22}{s1['months']:>5}{s1['cum_strat']:>11}{s1['cum_bh']:>11}"
                      f"{s1['dd_strat']:>11}{s1['dd_bh']:>11}{t2}")

        m12 = next((r for r in rows if r["label"] == "三档 MA12 VIX>30"), None)
        grid = [r for r in rows if r["label"].startswith("三档 MA")]
        v30 = [r for r in rows if r["label"] in ("三档 MA10 VIX>30", "三档 MA12 VIX>30",
                                                "三档 MA18 VIX>30")]
        same_dir = bool(v30) and all(r["vs_static_pp"] >= 0 for r in v30)
        if m12:
            c1 = m12["vs_static_pp"] >= 0
            c2 = (m12["dd_improve"] or 0) >= GATE_DD_IMPROVE
            mag = abs(m12["vs_static_pp"])
            print(f"\n判据（MA12/VIX30）：超额≥静态 {c1} (实际 {m12['vs_static_pp']}pp) | "
                  f"回撤改善≥{GATE_DD_IMPROVE}% {c2} (实际 {m12['dd_improve']}%) | "
                  f"MA10/12/18 @VIX30 同向 {same_dir} | 网格 "
                  f"{sum(1 for r in grid if r['vs_static_pp'] >= 0)}/{len(grid)} 为正")
            gate = c1 and c2 and same_dir
            if gate and mag < GATE_NOISE_PP:
                print(f"裁定：判据勉强通过但超额仅 {mag}pp/年（噪声级）→ 只做展示层，不给仓位建议")
            else:
                print(f"裁定：{'可以进入 P1（含仓位建议）' if gate else '只做展示层，不给仓位建议'}")


if __name__ == "__main__":
    main()
