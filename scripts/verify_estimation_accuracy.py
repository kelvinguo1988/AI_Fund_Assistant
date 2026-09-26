#!/usr/bin/env python3
"""持仓自算估值模型准确度回测（旧口径 vs 仓位感知新口径）

持仓权重与股票仓位都直接读本地库（即线上估值用的同一份输入），
个股日涨跌幅取 akshare 日线、基金实际涨跌幅取单位净值序列，逐日配对比较：

- 旧口径：覆盖率 ≥50% 归一法（Σwᵢ·pctᵢ/Σwᵢ）；覆盖率 <50% 指数混合法
  （未披露部分按 60% 股票仓位跟随沪深300）
- 新口径 position_aware：Σ(wᵢ·pctᵢ)/100 + max(0, 股票仓位 − 覆盖率) × 沪深300
  即未披露的股票部分按季报**真实披露仓位**跟随指数，债券/现金记零波动

指标：MAE、RMSE、方向命中率（同涨同跌）、系统偏差、|误差|>1pp 天数。
逐日配对明细同时写 /tmp/estimation_pairs.csv 便于复查。

用法: python3 scripts/verify_estimation_accuracy.py [基金代码...]
默认样本按覆盖率分层取 8 只；持仓含港股/美股的基金取不到个股日线，
会被完整度过滤掉（这类基金只能靠单日配对验证）。
"""

import sqlite3
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

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "fund_quant.db"
PAIRS_CSV = Path("/tmp/estimation_pairs.csv")
HIST_DIR = Path("/tmp/estimation_hist")
_HIST_CACHE: dict[str, pd.Series] = {}
INDEX_SYMBOL = "000300"
INDEX_EQUITY_SHARE = 0.6      # 旧口径低覆盖分支在 fund_realtime_service 里的拍板值
LOW_COVERAGE_THRESHOLD = 0.5
MIN_QUOTE_RATIO = 0.9          # 个股日线匹配到的权重需 ≥ 披露权重的 90%，否则该样本日剔除
DEFAULT_SAMPLE = [
    "161725", "007491", "024203", "011452",   # 披露权重 64%~89%
    "017103", "015989", "004815", "025657",   # 披露权重 18%~55%（后两只含港股）
]


def load_estimation_inputs(code: str) -> tuple[list[tuple[str, float]], float]:
    """本地库取估值输入：最新季度持仓 (股票代码, 占净值比%) + 最新已生效季报股票仓位%

    与 FundRealtimeService 完全同源：持仓取该基金最大 quarter_label 的明细，
    仓位只认 effective_date <= 今天 的最后一个报告期（尚未披露的不能用）。
    """
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute("select id from funds where code = ?", (code,)).fetchone()
        if row is None:
            return [], 0.0
        fid = row[0]
        latest_q = con.execute(
            "select max(quarter_label) from fund_holdings where fund_id = ?", (fid,)
        ).fetchone()[0]
        holdings = []
        if latest_q:
            holdings = [
                (str(c), float(r)) for c, r in con.execute(
                    "select stock_code, ratio from fund_holdings "
                    "where fund_id = ? and quarter_label = ? and ratio is not null",
                    (fid, latest_q),
                )
            ]
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        ratios = con.execute(
            "select stock_position_ratio from fund_quarterly "
            "where fund_id = ? and stock_position_ratio is not null and effective_date <= ? "
            "order by report_date",
            (fid, today),
        ).fetchall()
        return holdings, (float(ratios[-1][0]) if ratios else 0.0)
    finally:
        con.close()


def get_nav_returns(code: str, days: int = 20) -> pd.DataFrame:
    """基金近 N 日净值涨跌幅 → DataFrame(date, actual_pct)"""
    nav = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    nav = nav.sort_values("净值日期").tail(days + 1).reset_index(drop=True)
    nav["actual_pct"] = nav["单位净值"].pct_change() * 100
    nav = nav[["净值日期", "actual_pct"]].dropna()
    nav["净值日期"] = nav["净值日期"].astype(str).str[:10]  # date对象/str 统一
    return nav


def get_stock_pcts(stock_codes: list[str], days: int = 20) -> dict[str, pd.Series]:
    """个股近 N 日每日涨跌幅 → {code: Series(date→pct%)}

    降级链: 东财 stock_zh_a_hist → 腾讯 stock_zh_a_hist_tx（东财限连时可用）。
    同一批回测里多只基金常共享持仓股，故按代码缓存（含跨次运行的 /tmp 磁盘缓存），
    避免对同一标的重复请求。
    """
    out = {}
    for sc in stock_codes:
        if sc in _HIST_CACHE:
            out[sc] = _HIST_CACHE[sc]
            continue
        s = None
        try:
            h = ak.stock_zh_a_hist(symbol=sc, period="daily", adjust="qfq")
            h = h.sort_values("日期").tail(days + 1)
            s = h.set_index("日期")["涨跌幅"].astype(float)
        except Exception:
            pass
        if s is None and not (len(sc) == 5 and sc.isdigit()):
            # 腾讯日线降级（close 差分算涨跌幅）；注意仅支持 A 股，港股会卡死
            try:
                from backend.services.fund_realtime_service import tencent_code
                end = pd.Timestamp.now().strftime("%Y%m%d")
                start = (pd.Timestamp.now() - pd.Timedelta(days=days * 2 + 15)).strftime("%Y%m%d")
                h = ak.stock_zh_a_hist_tx(symbol=tencent_code(sc), start_date=start, end_date=end)
                if h is not None and not h.empty:
                    h = h.sort_values("date").tail(days + 1)
                    s = (h.set_index("date")["close"].astype(float).pct_change() * 100).dropna()
                    s.index = s.index.astype(str)
            except Exception as e:
                print(f"  [warn] {sc} 行情失败(东财+腾讯): {e}")
        if s is not None:
            _HIST_CACHE[sc] = s
            out[sc] = s
        else:
            cache = HIST_DIR / f"{sc}.csv"
            if cache.exists():  # 网络全挂时退回上次抓到的日线
                df = pd.read_csv(cache)
                _HIST_CACHE[sc] = s = df.set_index(df.columns[0])[df.columns[1]].astype(float)
                out[sc] = s
                print(f"  [warn] {sc} 用磁盘缓存日线（{cache}）")
        if s is not None:
            HIST_DIR.mkdir(parents=True, exist_ok=True)
            s.rename("pct").to_csv(HIST_DIR / f"{sc}.csv", header=True)
        time.sleep(0.5)
    return out


def get_index_pcts(days: int = 20) -> pd.Series:
    """沪深300 近 N 日每日涨跌幅 → Series(date→pct%)

    降级链: 东财 index_zh_a_hist → 腾讯 stock_zh_index_daily_tx（周末/限连时可用）
    """
    try:
        h = ak.index_zh_a_hist(symbol=INDEX_SYMBOL, period="daily")
        h = h.sort_values("日期").tail(days + 1)
        s = h.set_index("日期")["涨跌幅"].astype(float)
        s.index = s.index.astype(str).str[:10]
        return s
    except Exception as e:
        print(f"  [warn] 沪深300 东财日线失败: {e}，改走腾讯")
    try:
        h = ak.stock_zh_index_daily_tx(symbol=f"sh{INDEX_SYMBOL}")
        h = h.sort_values("date").tail(days + 1)
        s = (h.set_index("date")["close"].astype(float).pct_change() * 100).dropna()
        s.index = s.index.astype(str).str[:10]
        return s
    except Exception as e:
        print(f"  [warn] 沪深300 腾讯日线也失败: {e}")
        return pd.Series(dtype=float)


def summarize(err: pd.Series, est: pd.Series, actual: pd.Series) -> dict:
    return {
        "MAE": round(float(err.abs().mean()), 3),
        "RMSE": round(float(np.sqrt((err ** 2).mean())), 3),
        "中位绝对误差": round(float(err.abs().median()), 3),
        "方向命中%": round(float(((est > 0) == (actual > 0)).mean() * 100), 1),
        "偏差": round(float(err.mean()), 3),
        "误差>1pp天数": int((err.abs() > 1.0).sum()),
    }


def main(codes: list[str], days: int = 20):
    index_pcts = get_index_pcts(days)
    if index_pcts.empty:
        print("取不到沪深300 日线，无法回测新口径")
        return
    frames = []
    for code in codes:
        holdings, r_stock = load_estimation_inputs(code)
        if not holdings:
            print(f"\n=== 基金 {code}：本地无持仓，跳过 ===")
            continue
        print(f"\n=== 基金 {code}（持仓 {len(holdings)} 只 / 披露权重合计 "
              f"{sum(r for _, r in holdings):.1f}%，季报股票仓位 {r_stock:.1f}%）===")

        nav = get_nav_returns(code, days)
        stock_pcts = get_stock_pcts([c for c, _ in holdings], days)

        rows = []
        full_tot = sum(r for _, r in holdings)
        incomplete = 0
        for _, row in nav.iterrows():
            d = row["净值日期"]
            w_sum = w_tot = 0.0
            for sc, ratio in holdings:
                s = stock_pcts.get(sc)
                if s is None or d not in s.index:
                    continue
                w_sum += ratio * float(s[d])
                w_tot += ratio
            if w_tot <= 0:
                continue
            # 个股日线缺太多时，"当日覆盖率"会远低于该基金真实披露权重，
            # 残差被虚高放大成纯指数行情 —— 这类样本日对两个模型都不公平，剔除
            if full_tot and w_tot / full_tot < MIN_QUOTE_RATIO:
                incomplete += 1
                continue
            cov = w_tot / 100.0
            idx = float(index_pcts[d]) if d in index_pcts.index else None
            if cov >= LOW_COVERAGE_THRESHOLD or idx is None:
                est_old = w_sum / w_tot          # 归一法（无指数数据时旧口径也退化为它）
            else:
                est_old = w_sum / 100.0 + (1 - cov) * INDEX_EQUITY_SHARE * idx
            if r_stock > 0 and idx is not None:
                est_new = w_sum / 100.0 + max(0.0, r_stock / 100.0 - cov) * idx
            else:
                est_new = est_old
            rows.append({"code": code, "date": d, "cov": round(cov, 3),
                         "r_stock": r_stock, "index_pct": idx,
                         "actual_pct": float(row["actual_pct"]),
                         "est_old": est_old, "est_new": est_new})

        if incomplete:
            print(f"  （剔除 {incomplete} 个行情不完整日：持仓股日线缺失 >10%）")
        if not rows:
            # 常见成因：持仓含港股/美股，两个日线源都只支持 A 股
            print("  无满足完整度要求的样本日，该基金只能靠单日配对验证（跳过）")
            continue
        df = pd.DataFrame(rows).dropna(subset=["actual_pct"])
        df = df[df["actual_pct"].abs() < 20]  # 剔除大额申赎/数据异常造成的净值跳变日
        if df.empty:
            print("  无有效对比日")
            continue
        df.to_csv(PAIRS_CSV, mode="a", index=False,
                  header=not PAIRS_CSV.exists(), float_format="%.4f")
        frames.append(df)
        e_old, e_new = df["est_old"] - df["actual_pct"], df["est_new"] - df["actual_pct"]
        print(f"  对比 {len(df)} 天：MAE 旧 {e_old.abs().mean():.3f} / 新 {e_new.abs().mean():.3f}pp，"
              f"方向命中 旧 {((df['est_old']>0)==(df['actual_pct']>0)).mean()*100:.0f}% / "
              f"新 {((df['est_new']>0)==(df['actual_pct']>0)).mean()*100:.0f}%")
        for _, w in df.assign(err=(df["est_new"] - df["actual_pct"]).abs()) \
                       .nlargest(3, "err").iterrows():
            print(f"    {w['date']}: 新 {w['est_new']:+.2f}% 旧 {w['est_old']:+.2f}% "
                  f"实际 {w['actual_pct']:+.2f}%（当日覆盖率 {w['cov']*100:.0f}%）")

    if not frames:
        return
    all_df = pd.concat(frames, ignore_index=True)
    print(f"\n=== 汇总：{all_df['code'].nunique()} 只基金 / {len(all_df)} 个基金日 ===")
    table = pd.DataFrame({
        "旧口径（归一/指数混合）": summarize(all_df["est_old"] - all_df["actual_pct"],
                                            all_df["est_old"], all_df["actual_pct"]),
        "新口径 position_aware": summarize(all_df["est_new"] - all_df["actual_pct"],
                                          all_df["est_new"], all_df["actual_pct"]),
    })
    print(table.to_string())
    for lo, hi, name in ((0, 0.5, "覆盖率<50%"), (0.5, 1.01, "覆盖率≥50%")):
        sub = all_df[(all_df["cov"] >= lo) & (all_df["cov"] < hi)]
        if sub.empty:
            continue
        eo = (sub["est_old"] - sub["actual_pct"]).abs().mean()
        en = (sub["est_new"] - sub["actual_pct"]).abs().mean()
        print(f"  {name}（{len(sub)} 个基金日）：MAE 旧 {eo:.3f} → 新 {en:.3f}pp"
              f"（{'改善' if en < eo else '变差'} {abs(eo-en):.3f}）")
    print(f"\n  相关系数：旧 {all_df['est_old'].corr(all_df['actual_pct']):.3f} / "
          f"新 {all_df['est_new'].corr(all_df['actual_pct']):.3f}")
    print(f"  逐日明细：{PAIRS_CSV}")


if __name__ == "__main__":
    PAIRS_CSV.unlink(missing_ok=True)
    main(sys.argv[1:] or DEFAULT_SAMPLE)
