"""基金实时净值预估服务 — 以场外(OTC)基金为主的设计

场外基金没有实时成交价，"实时净值预估" = 盘中估算。数据源降级链
（2026-08-28 实测）：

  OTC 场外（主路径）:
    1. fundgz 天天基金官方估值 JSONP（gsz 估算净值 / gszzl 估算涨跌幅）
       —— 2026-08-28 实测接口已下线（返回 HTML 错误页），冷却 24h 每日探活；
       官方恢复前实际由 2 兜底
    2. holdings_est 持仓×个股快照自算（主力兜底，永远可用）:
         growth = Σ(占净值比ᵢ × 涨跌幅ᵢ) 按覆盖率归一 / 低覆盖时指数混合
       持仓来自库内 fund_holdings 最新季报 top10；个股涨跌幅来自
       stock_zh_a_spot_em 全市场快照（60s 缓存，一次服务全部基金）。

  ETF 场内（透明分支）:
    fund_etf_spot_em 实时行情（真实价格，非估算）。

估值模型（holdings_est）:
  - coverage = Σ占净值比ᵢ（top10 通常 40%~70%）
  - coverage ≥ 0.5 → 归一法: growth = Σwᵢ·pctᵢ / Σwᵢ
    （隐含假设：未披露持仓与已披露持仓同步涨跌）
  - coverage < 0.5 → 指数混合法: growth = Σwᵢ·pctᵢ + (1-cov)·index_pct·0.6
    （未披露部分 60% 视为跟随沪深300 的股票，40% 视为债券/现金零波动）
  est_model 字段标注所用模型，前端可提示估算口径。

缓存策略:
  - 全市场快照（个股/ETF/指数）: 类级 60s TTL + asyncio.Lock 双检
  - 单基金估值结果: 60s TTL
  - fundgz 失败: 5min 冷却防反爬穿透
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

import requests

from backend.data_sources.base import guess_fund_type
from backend.models.fund import Fund

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────

SPOT_CACHE_TTL = 60.0          # 全市场快照缓存（秒）
ESTIMATE_CACHE_TTL = 60.0      # 单基金估值结果缓存（秒）
FUNDGZ_FAIL_COOLDOWN = 86400.0  # fundgz 失败冷却（2026-08-28 实测接口已下线，
                                # 返回 HTML 错误页；冷却升至 24h，每日探活一次，
                                # 若官方恢复可自动生效，平时不再逐请求撞死链）
FUNDGZ_TIMEOUT = 8.0

# 数据源熔断冷却（防封禁）：请求失败后 N 秒内不再尝试该源。
# 没有熔断时，每次页面刷新都会重新撞已被限连的接口（东财 2 次重试 +
# 新浪 70 页分页），请求风暴只会加重封禁。冷却期间直接用缓存/空值。
SOURCE_FAIL_COOLDOWN = 600.0

# 熔断键："eastmoney" 管所有东财实时快照接口（封禁按 IP/域名生效，
# 单接口被拒时其余东财接口也无意义）；"sina" 管新浪分页快照；
# "tencent" 管腾讯行情接口。
EM_SOURCE = "eastmoney"
SINA_SOURCE = "sina"
TENCENT_SOURCE = "tencent"

TENCENT_URL = "http://qt.gtimg.cn/q={codes}"

FUNDGZ_URL = "https://fundgz.1234567.com.cn/js/{code}.js"

# 估值模型参数
LOW_COVERAGE_THRESHOLD = 0.5   # 低于此覆盖率切换指数混合法
INDEX_EQUITY_SHARE = 0.6       # 混合法中未披露部分按 60% 股票仓位跟随指数

_JSONP_RE = re.compile(r"jsonpgz\((.*)\)")


# ── 纯函数（供单元测试） ──────────────────────────────────────────────

def parse_tencent_quotes(text: str) -> dict[str, dict]:
    """解析腾讯行情批量响应（GBK 文本）

    格式: v_sh600308="1~华泰股份~600308~3.34~3.29~...~20260828161437~0.05~1.52~...";
    字段: [1]名称 [3]最新价 [4]昨收 [30]时间 [32]涨跌幅%
    A股/ETF/指数/港股统一兼容（实测 2026-08-29：sh600308/sh510300/hk00981/sh000300）

    Returns:
        {code(纯数字): {name, price, prev_close, pct, time}}
    """
    out: dict[str, dict] = {}
    for line in (text or "").split(";"):
        line = line.strip()
        if not line or "=" not in line or not line.startswith("v_"):
            continue
        var, payload = line.split("=", 1)
        key = var[2:]  # 去 "v_"
        fields = payload.strip().strip('"').split("~")
        if len(fields) <= 32:
            continue
        # key 形如 sh600308 / hk00981 → 取纯数字代码
        code = key[2:] if not key[:2].isdigit() else key
        try:
            pct = float(fields[32])
            price = float(fields[3]) if fields[3] else None
            prev = float(fields[4]) if fields[4] else None
        except (ValueError, TypeError):
            continue
        out[code] = {
            "name": fields[1],
            "price": price,
            "prev_close": prev,
            "pct": pct,
            "time": fields[30] if len(fields) > 30 else "",
        }
    return out


def tencent_code(code: str) -> str:
    """纯数字代码 → 腾讯带市场前缀代码

    - 6 位: 6/5 开头→sh（股票/沪ETF），0/1/3 开头→sz，4/8/9 开头→bj（尽力而为）
    - 5 位: 港股 hk 前缀
    """
    if len(code) == 5 and code.isdigit():
        return f"hk{code}"
    if code and code[0] in ("5", "6", "9"):
        return f"sh{code}"
    if code and code[0] in ("0", "1", "2", "3"):
        return f"sz{code}"
    return f"bj{code}" if code else code


def parse_fundgz_jsonp(text: str) -> Optional[dict]:
    """解析天天基金估值 JSONP 响应

    格式: jsonpgz({"fundcode":"000001","name":"华夏成长","jzrq":"2026-08-27",
                   "dwjz":"1.0","gsz":"1.005","gszzl":"0.49","gztime":"2026-08-28 15:00"});
    反爬页返回 HTML → 正则不匹配 → None
    """
    m = _JSONP_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def compute_holdings_growth(
    holdings: list[tuple[str, Optional[float]]],
    stock_pct: dict[str, float],
    index_pct: Optional[float] = None,
) -> tuple[Optional[float], float, str]:
    """按持仓权重估算场外基金盘中涨跌幅

    Args:
        holdings: [(stock_code, 占净值百分比), ...] 最新季报持仓
        stock_pct: {stock_code: 今日涨跌幅%}
        index_pct: 沪深300 今日实时涨跌幅%（低覆盖混合法用）

    Returns:
        (growth_pct, coverage, est_model)
        - 归一法 "normalized"（coverage ≥ 0.5）
        - 指数混合法 "index_blend"（coverage < 0.5 且 index_pct 可用）
        - 无可用持仓/行情 → (None, 0.0, "")
    """
    weighted_sum = 0.0
    weight_total = 0.0
    for code, ratio in holdings:
        if ratio is None or ratio <= 0:
            continue
        pct = stock_pct.get(code)
        if pct is None:
            continue
        weighted_sum += ratio * pct
        weight_total += ratio

    if weight_total <= 0:
        return None, 0.0, ""

    coverage = weight_total / 100.0
    if coverage >= LOW_COVERAGE_THRESHOLD:
        return weighted_sum / weight_total, coverage, "normalized"

    # 低覆盖：未披露部分 60% 视为股票跟随指数，40% 视为债券/现金零波动
    if index_pct is not None:
        growth = weighted_sum / 100.0 + (1 - coverage) * INDEX_EQUITY_SHARE * index_pct
        return growth, coverage, "index_blend"

    # 无指数数据也退化为归一法（宁可给粗估也不返回空）
    return weighted_sum / weight_total, coverage, "normalized"


class FundRealtimeService:
    """基金实时净值预估 — OTC 主路径(fundgz→持仓自算) + ETF 透明分支"""

    # 类级共享缓存（一次快照服务全部基金）
    _stock_spot_cache: Optional[dict[str, float]] = None
    _etf_spot_cache: Optional[dict[str, dict]] = None
    _hk_spot_cache: Optional[dict[str, float]] = None
    _index_pct_cache: Optional[float] = None
    _spot_ts: float = 0.0
    _spot_lock: Optional[asyncio.Lock] = None

    # 单基金估值缓存 {code: (ts, result_dict)}
    _estimate_cache: dict[str, tuple[float, dict]] = {}
    # fundgz 连续失败冷却时间戳
    _fundgz_fail_until: float = 0.0
    # 数据源熔断冷却 {source: fail_until_ts}
    _source_fail_until: dict[str, float] = {}
    # 最近一次快照的行情时点（"哪一天的涨跌"），随快照成功更新
    _spot_quote_time: str = ""
    _spot_quote_time_ts: Optional[datetime] = None

    # ── 熔断器 ──────────────────────────────────────────────────────────

    @staticmethod
    def _source_available(name: str) -> bool:
        return time.time() >= FundRealtimeService._source_fail_until.get(name, 0.0)

    @staticmethod
    def _mark_source_fail(name: str, reason: str = "") -> None:
        until = time.time() + SOURCE_FAIL_COOLDOWN
        prev = FundRealtimeService._source_fail_until.get(name, 0.0)
        FundRealtimeService._source_fail_until[name] = max(prev, until)
        logger.warning(
            f"数据源 [{name}] 熔断 {SOURCE_FAIL_COOLDOWN:.0f}s（防封禁）"
            + (f": {reason}" if reason else "")
        )

    @staticmethod
    def _mark_source_ok(name: str) -> None:
        FundRealtimeService._source_fail_until.pop(name, None)

    # ── 行情时点（"涨跌是哪一天的"）────────────────────────────────────

    @staticmethod
    def _update_quote_time(raw: str = "", trusted: bool = False) -> None:
        """记录最近一次快照的行情时点

        raw 支持: 腾讯 14 位时间戳(20260828161437) / "YYYY-MM-DD HH:MM[:SS]"；
        trusted=True 表示行情源自带时间（可信，取更新者，周末不回退）；
        trusted=False 为服务器拉取时间（近似，仅在无记录时填写）。
        """
        raw = (raw or "").strip()
        ts = None
        if len(raw) == 14 and raw.isdigit():
            try:
                ts = datetime.strptime(raw, "%Y%m%d%H%M%S")
            except ValueError:
                ts = None
        elif raw:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    ts = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue

        if ts is not None and trusted:
            cur = FundRealtimeService._spot_quote_time_ts
            if cur is None or ts > cur:
                FundRealtimeService._spot_quote_time = ts.strftime("%Y-%m-%d %H:%M")
                FundRealtimeService._spot_quote_time_ts = ts
        elif not FundRealtimeService._spot_quote_time:
            FundRealtimeService._spot_quote_time = datetime.now().strftime(
                "%Y-%m-%d %H:%M"
            )

    @staticmethod
    def get_spot_quote_time() -> str:
        return FundRealtimeService._spot_quote_time

    def __init__(self, db) -> None:
        self.db = db
        if FundRealtimeService._spot_lock is None:
            FundRealtimeService._spot_lock = asyncio.Lock()

    # ── 主入口 ──────────────────────────────────────────────────────────

    async def get_realtime(
        self,
        funds: list[Fund],
        force: bool = False,
    ) -> dict[str, dict]:
        """获取一批基金的实时估值（OTC 优先走 fundgz，失败持仓自算）

        Returns:
            {code: {code,name,source,nav_date,nav,estimated_nav,
                    growth_pct,quote_time,coverage,est_model}}
        """
        now = time.time()
        results: dict[str, dict] = {}
        pending: list[Fund] = []

        for f in funds:
            hit = FundRealtimeService._estimate_cache.get(f.code)
            if not force and hit and now - hit[0] < ESTIMATE_CACHE_TTL:
                results[f.code] = hit[1]
            else:
                pending.append(f)

        if not pending:
            return results

        # ── OTC 场外（主路径）──
        otc_funds = [f for f in pending if guess_fund_type(f.code) != "etf"]
        for f in otc_funds:
            est = await self._fetch_fundgz(f.code)
            if est is not None:
                results[f.code] = {
                    "code": f.code,
                    "name": f.name or est.get("name", ""),
                    "source": "fundgz",
                    "nav_date": est.get("jzrq", ""),
                    "nav": _to_float(est.get("dwjz")),
                    "estimated_nav": _to_float(est.get("gsz")),
                    "growth_pct": _to_float(est.get("gszzl")),
                    "quote_time": est.get("gztime", ""),
                    "coverage": None,
                    "est_model": "official",
                }
                self._cache_estimate(f.code, results[f.code])

        # fundgz 未覆盖的场外基金 → 持仓自算（主力兜底）
        otc_missing = [f for f in otc_funds if f.code not in results]
        if otc_missing:
            await self._fill_from_holdings(otc_missing, results)

        # ── ETF 场内（透明分支，真实价格非估算）──
        etf_funds = [f for f in pending if guess_fund_type(f.code) == "etf"]
        if etf_funds:
            spot = await self._get_etf_spot(codes=[f.code for f in etf_funds])
            if spot is not None:
                for f in etf_funds:
                    row = spot.get(f.code)
                    if row:
                        results[f.code] = {
                            "code": f.code,
                            "name": f.name or row.get("name", ""),
                            "source": "etf_spot",
                            "nav_date": row.get("date", ""),
                            "nav": None,
                            "estimated_nav": row.get("price"),
                            "growth_pct": row.get("pct"),
                            "quote_time": row.get("time", ""),
                            "coverage": 1.0,
                            "est_model": "market_price",
                        }
                        self._cache_estimate(f.code, results[f.code])

        return results

    # ── 数据源实现 ──────────────────────────────────────────────────────

    async def _get_etf_spot(
        self, codes: Optional[list[str]] = None
    ) -> Optional[dict[str, dict]]:
        """ETF 快照 → {code: {name,price,pct,time,date}}

        降级链: 东财 fund_etf_spot_em（全市场）→ 腾讯按需批量（场内基金
        行情与股票同接口，实测 sh510300 可用）。
        """
        now = time.time()
        if (
            FundRealtimeService._etf_spot_cache is not None
            and now - FundRealtimeService._spot_ts < SPOT_CACHE_TTL
        ):
            return FundRealtimeService._etf_spot_cache

        async with FundRealtimeService._spot_lock:
            now = time.time()
            if (
                FundRealtimeService._etf_spot_cache is not None
                and now - FundRealtimeService._spot_ts < SPOT_CACHE_TTL
            ):
                return FundRealtimeService._etf_spot_cache

            if not self._source_available(EM_SOURCE):
                # 东财熔断 → 腾讯按需构造兼容结构
                if codes and self._source_available(TENCENT_SOURCE):
                    quotes = await self._get_tencent_quotes(codes)
                    if quotes:
                        spot = {
                            c: {
                                "name": q.get("name", ""),
                                "price": q.get("price"),
                                "pct": q.get("pct"),
                                "time": q.get("time", ""),
                                "date": "",
                            }
                            for c, q in quotes.items()
                        }
                        FundRealtimeService._etf_spot_cache = spot
                        FundRealtimeService._spot_ts = time.time()
                        logger.info(f"ETF 实时行情(腾讯按需): {len(spot)}/{len(codes)} 只")
                        return spot
                return None

            try:
                import akshare as ak
                from backend.data_sources.akshare_adapter import AKShareAdapter
                adapter = AKShareAdapter()
                df = await adapter._call(ak.fund_etf_spot_em, _max_attempts=2)
                if df is None or df.empty:
                    return None
                spot: dict[str, dict] = {}
                for _, row in df.iterrows():
                    code = str(row.get("代码", ""))
                    if not code:
                        continue
                    spot[code] = {
                        "name": str(row.get("名称", "")),
                        "price": _to_float(row.get("最新价")),
                        "pct": _to_float(row.get("涨跌幅")),
                        "time": str(row.get("更新时间", "")),
                        "date": str(row.get("数据日期", "")),
                    }
                self._mark_source_ok(EM_SOURCE)
                FundRealtimeService._etf_spot_cache = spot
                FundRealtimeService._spot_ts = now
                logger.info(f"ETF 实时快照刷新: {len(spot)} 只")
                return spot
            except Exception as e:
                self._mark_source_fail(EM_SOURCE, str(e)[:80])
                if codes and self._source_available(TENCENT_SOURCE):
                    quotes = await self._get_tencent_quotes(codes)
                    if quotes:
                        spot = {
                            c: {
                                "name": q.get("name", ""),
                                "price": q.get("price"),
                                "pct": q.get("pct"),
                                "time": q.get("time", ""),
                                "date": "",
                            }
                            for c, q in quotes.items()
                        }
                        FundRealtimeService._etf_spot_cache = spot
                        FundRealtimeService._spot_ts = time.time()
                        logger.info(f"ETF 实时行情(腾讯按需): {len(spot)}/{len(codes)} 只")
                        return spot
                return None

    async def _get_stock_spot(
        self, codes: Optional[list[str]] = None
    ) -> Optional[dict[str, float]]:
        """A 股涨跌幅 → {code: 今日涨跌幅%}

        数据源降级链（2026-08-29 实测）:
        1. 东财 stock_zh_a_spot_em（快，一次请求全市场）
        2. 腾讯 qt.gtimg.cn 按需批量（codes 已知时，10 只一次请求，最轻量）
        3. 新浪 stock_zh_a_spot（全市场 70 页分页，最重，放最后）
        腾讯按需结果并入 60s 缓存。
        """
        now = time.time()
        # 快路径：缓存新鲜且覆盖全部所需代码（腾讯按需缓存是部分市场，
        # 必须做覆盖检查，否则 60s 内第二只基金会拿到不完整的 map）
        cache = FundRealtimeService._stock_spot_cache
        if (
            cache is not None
            and now - FundRealtimeService._spot_ts < SPOT_CACHE_TTL
            and all(c in cache for c in (codes or []))
        ):
            return cache

        async with FundRealtimeService._spot_lock:
            now = time.time()
            cache = FundRealtimeService._stock_spot_cache
            if (
                cache is not None
                and now - FundRealtimeService._spot_ts < SPOT_CACHE_TTL
            ):
                missing = [c for c in (codes or []) if c not in cache]
                if not missing:
                    return cache
                # 缓存部分覆盖 → 腾讯按需补缺失代码
                if self._source_available(TENCENT_SOURCE):
                    tmap = await self._get_tencent_pct(missing)
                    if tmap:
                        cache.update(tmap)
                        FundRealtimeService._spot_ts = now
                        logger.info(f"A 股行情(腾讯补缺): +{len(tmap)} 只")
                return cache

            import akshare as ak
            from backend.data_sources.akshare_adapter import AKShareAdapter
            adapter = AKShareAdapter()

            pct_map: Optional[dict[str, float]] = None

            # 1. 东财快照（首选；熔断中直接跳过不发请求）
            if self._source_available(EM_SOURCE):
                try:
                    df = await adapter._call(ak.stock_zh_a_spot_em, _max_attempts=2)
                    if df is not None and not df.empty:
                        pct_map = {}
                        for _, row in df.iterrows():
                            code = str(row.get("代码", ""))
                            pct = _to_float(row.get("涨跌幅"))
                            if code and pct is not None:
                                pct_map[code] = pct
                        self._mark_source_ok(EM_SOURCE)
                        # 东财 A 股快照无时间列，用服务器拉取时间近似（仅空时填）
                        self._update_quote_time()
                        logger.info(f"A 股实时快照刷新(东财): {len(pct_map)} 只")
                except Exception as e:
                    self._mark_source_fail(EM_SOURCE, str(e)[:80])

            # 2. 腾讯按需批量（codes 已知时优先于新浪全市场：快且轻）
            if not pct_map and codes and self._source_available(TENCENT_SOURCE):
                tencent_map = await self._get_tencent_pct(codes)
                if tencent_map:
                    pct_map = tencent_map
                    logger.info(f"A 股实时行情(腾讯按需): {len(pct_map)}/{len(codes)} 只")

            # 3. 新浪快照（全市场分页，最后兜底）
            if not pct_map and self._source_available(SINA_SOURCE):
                try:
                    df = await adapter._call(ak.stock_zh_a_spot, _max_attempts=1)
                    if df is not None and not df.empty:
                        pct_map = {}
                        for _, row in df.iterrows():
                            raw = str(row.get("代码", ""))
                            # sh600000/sz000001/bj920000 → 纯数字
                            code = raw[2:] if len(raw) > 6 else raw
                            pct = _to_float(row.get("涨跌幅"))
                            if code and pct is not None:
                                pct_map[code] = pct
                        self._mark_source_ok(SINA_SOURCE)
                        # 新浪只有 HH:MM:SS 无日期，仅空时填服务器时间近似
                        self._update_quote_time()
                        logger.info(f"A 股实时快照刷新(新浪): {len(pct_map)} 只")
                except Exception as e:
                    self._mark_source_fail(SINA_SOURCE, str(e)[:80])

            if pct_map:
                FundRealtimeService._stock_spot_cache = pct_map
                FundRealtimeService._spot_ts = now
            return pct_map

    async def _get_hk_spot(
        self, codes: Optional[list[str]] = None
    ) -> Optional[dict[str, float]]:
        """港股涨跌幅（60s 缓存，尽力而为）→ {code: 今日涨跌幅%}

        降级链: 东财 stock_hk_spot_em（全市场）→ 腾讯按需批量（实测 hk00981 可用）。
        东财限连时腾讯按需只查所需港股（top10 场景 1~2 只一次请求）。
        """
        now = time.time()
        # 快路径：缓存新鲜且覆盖全部所需代码（腾讯按需缓存是部分市场）
        cache = FundRealtimeService._hk_spot_cache
        if (
            cache is not None
            and now - FundRealtimeService._spot_ts < SPOT_CACHE_TTL
            and all(c in cache for c in (codes or []))
        ):
            return cache

        async with FundRealtimeService._spot_lock:
            now = time.time()
            cache = FundRealtimeService._hk_spot_cache
            if (
                cache is not None
                and now - FundRealtimeService._spot_ts < SPOT_CACHE_TTL
            ):
                missing = [c for c in (codes or []) if c not in cache]
                if missing and self._source_available(TENCENT_SOURCE):
                    tmap = await self._get_tencent_pct(missing)
                    if tmap:
                        cache.update(tmap)
                        FundRealtimeService._spot_ts = now
                        logger.info(f"港股行情(腾讯补缺): +{len(tmap)} 只")
                return cache

            if not self._source_available(EM_SOURCE):
                # 东财熔断 → 腾讯按需
                if codes and self._source_available(TENCENT_SOURCE):
                    tencent_map = await self._get_tencent_pct(codes)
                    if tencent_map:
                        FundRealtimeService._hk_spot_cache = tencent_map
                        FundRealtimeService._spot_ts = time.time()
                        logger.info(f"港股实时行情(腾讯按需): {len(tencent_map)}/{len(codes)} 只")
                        return tencent_map
                return None

            try:
                import akshare as ak
                from backend.data_sources.akshare_adapter import AKShareAdapter
                adapter = AKShareAdapter()
                df = await adapter._call(ak.stock_hk_spot_em, _max_attempts=1)
                if df is None or df.empty:
                    return None
                hk_map: dict[str, float] = {}
                for _, row in df.iterrows():
                    code = str(row.get("代码", "")).zfill(5)
                    pct = _to_float(row.get("涨跌幅"))
                    if code and pct is not None:
                        hk_map[code] = pct
                self._mark_source_ok(EM_SOURCE)
                FundRealtimeService._hk_spot_cache = hk_map
                FundRealtimeService._spot_ts = now
                logger.info(f"港股实时快照刷新: {len(hk_map)} 只")
                return hk_map
            except Exception as e:
                self._mark_source_fail(EM_SOURCE, str(e)[:80])
                # 东财失败 → 腾讯按需
                if codes and self._source_available(TENCENT_SOURCE):
                    tencent_map = await self._get_tencent_pct(codes)
                    if tencent_map:
                        FundRealtimeService._hk_spot_cache = tencent_map
                        FundRealtimeService._spot_ts = time.time()
                        logger.info(f"港股实时行情(腾讯按需): {len(tencent_map)}/{len(codes)} 只")
                        return tencent_map
                return None

    async def _get_index_pct(self) -> Optional[float]:
        """沪深300 实时涨跌幅%（60s 缓存，低覆盖混合法用）"""
        now = time.time()
        if (
            FundRealtimeService._index_pct_cache is not None
            and now - FundRealtimeService._spot_ts < SPOT_CACHE_TTL
        ):
            return FundRealtimeService._index_pct_cache

        async with FundRealtimeService._spot_lock:
            now = time.time()
            if (
                FundRealtimeService._index_pct_cache is not None
                and now - FundRealtimeService._spot_ts < SPOT_CACHE_TTL
            ):
                return FundRealtimeService._index_pct_cache

            if not self._source_available(EM_SOURCE):
                # 东财熔断 → 腾讯
                if self._source_available(TENCENT_SOURCE):
                    pct_map = await self._get_tencent_pct(["000300"])
                    if "000300" in pct_map:
                        FundRealtimeService._index_pct_cache = pct_map["000300"]
                        FundRealtimeService._spot_ts = time.time()
                        return pct_map["000300"]
                return None

            try:
                import akshare as ak
                from backend.data_sources.akshare_adapter import AKShareAdapter
                adapter = AKShareAdapter()
                df = await adapter._call(ak.stock_zh_index_spot_em, symbol="沪深重要指数", _max_attempts=2)
                if df is None or df.empty:
                    return None
                row = df[df["名称"].astype(str).str.contains("沪深300", na=False)]
                if row.empty:
                    return None
                pct = _to_float(row.iloc[0].get("涨跌幅"))
                self._mark_source_ok(EM_SOURCE)
                FundRealtimeService._index_pct_cache = pct
                FundRealtimeService._spot_ts = now
                return pct
            except Exception as e:
                self._mark_source_fail(EM_SOURCE, str(e)[:80])
                if self._source_available(TENCENT_SOURCE):
                    pct_map = await self._get_tencent_pct(["000300"])
                    if "000300" in pct_map:
                        FundRealtimeService._index_pct_cache = pct_map["000300"]
                        FundRealtimeService._spot_ts = time.time()
                        return pct_map["000300"]
                return None

    async def _get_tencent_quotes(self, codes: list[str]) -> dict[str, dict]:
        """腾讯行情按需批量查询（熔断保护）→ {纯数字code: {name,price,pct,...}}

        稳定源：无需鉴权、单次请求、覆盖 A股/ETF/港股/指数（2026-08-29 实测）。
        按需模式只查所需代码（top10 场景 10 只一次请求），比全市场分页轻量得多。
        """
        if not codes:
            return {}
        if not self._source_available(TENCENT_SOURCE):
            return {}
        # 单次批量 ≤ 60 只（腾讯接口上限约 60，稳妥取 50 分批）
        batches = [codes[i:i + 50] for i in range(0, len(codes), 50)]
        all_quotes: dict[str, dict] = {}
        try:
            for batch in batches:
                prefixed = ",".join(tencent_code(c) for c in batch)
                url = TENCENT_URL.format(codes=prefixed)

                def _do_request() -> str:
                    # 腾讯接口无 UA 时是 python-requests 指纹，易被封
                    resp = requests.get(
                        url,
                        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                        timeout=FUNDGZ_TIMEOUT,
                    )
                    resp.encoding = "gbk"
                    return resp.text

                text = await asyncio.to_thread(_do_request)
                all_quotes.update(parse_tencent_quotes(text))
            self._mark_source_ok(TENCENT_SOURCE)
            # 腾讯响应自带行情时间（14 位），是"涨跌是哪天的"最可信来源
            first = next(iter(all_quotes.values()), None)
            if first:
                self._update_quote_time(first.get("time", ""), trusted=True)
            return all_quotes
        except Exception as e:
            self._mark_source_fail(TENCENT_SOURCE, str(e)[:80])
            return all_quotes

    async def _get_tencent_pct(self, codes: list[str]) -> dict[str, float]:
        """腾讯行情涨跌幅简化版 → {code: pct%}"""
        quotes = await self._get_tencent_quotes(codes)
        return {c: q["pct"] for c, q in quotes.items() if q.get("pct") is not None}

    async def _fetch_fundgz(self, code: str) -> Optional[dict]:
        """天天基金估值 JSONP（失败进入 5 分钟冷却，防反爬穿透）

        只在冷却期外逐基金请求；全池 OTC 基金由调用方在冷却时整批跳过。
        """
        now = time.time()
        if now < FundRealtimeService._fundgz_fail_until:
            return None
        try:
            url = FUNDGZ_URL.format(code=code)

            def _do_request() -> Optional[dict]:
                resp = requests.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                        "Referer": "https://fund.eastmoney.com/",
                    },
                    timeout=FUNDGZ_TIMEOUT,
                )
                return parse_fundgz_jsonp(resp.text)

            data = await asyncio.to_thread(_do_request)
            if data and data.get("gszzl") is not None:
                return data
            # 反爬页/空数据 → 触发冷却
            FundRealtimeService._fundgz_fail_until = now + FUNDGZ_FAIL_COOLDOWN
            logger.info(f"fundgz 估值不可用(code={code})，进入 {FUNDGZ_FAIL_COOLDOWN}s 冷却")
            return None
        except Exception as e:
            FundRealtimeService._fundgz_fail_until = now + FUNDGZ_FAIL_COOLDOWN
            logger.warning(f"fundgz 请求失败(code={code}): {e}")
            return None

    async def _fill_from_holdings(
        self, funds: list[Fund], results: dict[str, dict]
    ) -> None:
        """持仓×个股快照自算估值（OTC 主力兜底路径）

        一次查询所有待估基金的最新季度持仓 + 一份全市场个股快照，
        避免 N+1 网络请求。持仓同时覆盖 A 股（6 位代码）与港股（5 位代码），
        两个行情源分别取数后合并，任一源熔断时另一个仍可用于估算。
        """
        from sqlalchemy import select
        from backend.models.fund_holding import FundHolding

        fund_ids = [f.id for f in funds]
        quarter_stmt = (
            select(FundHolding.fund_id, FundHolding.quarter_label)
            .where(FundHolding.fund_id.in_(fund_ids))
            .distinct()
        )
        rows = (await self.db.execute(quarter_stmt)).all()

        latest_q: dict[int, str] = {}
        for fid, qlabel in rows:
            if fid not in latest_q or qlabel > latest_q[fid]:
                latest_q[fid] = qlabel

        if not latest_q:
            logger.info(f"持仓自算: {len(funds)} 只基金无持仓数据，跳过")
            return

        hold_stmt = select(FundHolding).where(
            FundHolding.fund_id.in_(list(latest_q.keys()))
        )
        holdings_rows = (await self.db.execute(hold_stmt)).scalars().all()

        by_fund: dict[int, list[tuple[str, Optional[float]]]] = {}
        for h in holdings_rows:
            if latest_q.get(h.fund_id) == h.quarter_label:
                by_fund.setdefault(h.fund_id, []).append((h.stock_code, h.ratio))

        if not by_fund:
            return

        # 收集所需个股代码 → 快照降级链可用腾讯按需（只查所需，轻量）。
        # 5 位纯数字代码为港股（A 股 6 位），需走港股行情接口；
        # 与 get_top10_changes 的口径保持一致，否则含港股的基金覆盖率被拉低、
        # 纯港股基金直接返回 None。
        needed_codes = sorted({c for holdings in by_fund.values() for c, _ in holdings})
        hk_codes = [c for c in needed_codes if len(c) == 5 and c.isdigit()]
        a_codes = [c for c in needed_codes if c not in hk_codes]

        # 合并 A 股 + 港股行情：任一源熔断时另一个仍可用，
        # 不再因 A 股源失败而整批放弃（原逻辑 `if not stock_pct: return`）
        pct_map: dict[str, float] = {}
        if a_codes:
            stock_pct = await self._get_stock_spot(codes=a_codes)
            if stock_pct:
                pct_map.update(stock_pct)
        if hk_codes:
            hk_pct = await self._get_hk_spot(codes=hk_codes)
            if hk_pct:
                pct_map.update(hk_pct)
        if not pct_map:
            logger.info(
                f"持仓自算: 行情快照为空（A股 {len(a_codes)} 只 / "
                f"港股 {len(hk_codes)} 只均取不到），跳过"
            )
            return
        index_pct = await self._get_index_pct()

        for f in funds:
            holdings = by_fund.get(f.id)
            if not holdings:
                continue
            growth, coverage, model = compute_holdings_growth(
                holdings, pct_map, index_pct
            )
            if growth is None:
                continue
            results[f.code] = {
                "code": f.code,
                "name": f.name or "",
                "source": "holdings_est",
                "nav_date": "",
                "nav": None,
                "estimated_nav": None,
                "growth_pct": round(growth, 2),
                "quote_time": FundRealtimeService._spot_quote_time,
                "coverage": round(coverage, 3),
                "est_model": model,
            }
            self._cache_estimate(f.code, results[f.code])
        # 行情时点兜底填充（腾讯路径已在快照成功时记录可信时间）
        FundRealtimeService._update_quote_time()

    # ── 缓存 ────────────────────────────────────────────────────────────

    @staticmethod
    def _cache_estimate(code: str, data: dict) -> None:
        FundRealtimeService._estimate_cache[code] = (time.time(), data)

    # ── 报告项：前十大持仓涨跌 ──────────────────────────────────────────

    async def get_top10_changes(self, fund_id: int) -> list[dict]:
        """最新季报前十大持仓的当日涨跌（供报告 top10_change 项使用）

        Returns:
            [{"stock_name","stock_code","ratio","pct"}] — pct 为 None 表示
            行情未取到（停牌/港股/数据缺失）
        """
        from backend.services.fund_holding_service import get_latest_holdings

        holdings = await get_latest_holdings(self.db, fund_id, limit=10)
        if not holdings:
            return []

        # 5 位纯数字代码视为港股（A股为 6 位）
        hk_codes = [
            h.stock_code for h in holdings
            if len(h.stock_code) == 5 and h.stock_code.isdigit()
        ]
        a_codes = [h.stock_code for h in holdings if h.stock_code not in hk_codes]

        stock_pct = (await self._get_stock_spot(codes=a_codes)) or {}

        hk_pct: dict[str, float] = {}
        if hk_codes:
            hk_spot = await self._get_hk_spot(codes=hk_codes)
            if hk_spot:
                hk_pct = hk_spot

        def _lookup(code: str) -> Optional[float]:
            if code in stock_pct:
                return stock_pct[code]
            return hk_pct.get(code)

        result = []
        for h in holdings:
            result.append({
                "stock_name": h.stock_name,
                "stock_code": h.stock_code,
                "ratio": h.ratio,
                "pct": _lookup(h.stock_code),
            })
        # 行情时点兜底填充（腾讯路径已在快照成功时记录）
        FundRealtimeService._update_quote_time()
        return result


def _to_float(v) -> Optional[float]:
    """安全转 float（''/'—'/None/NaN → None）"""
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None  # NaN 检查
    except (ValueError, TypeError):
        return None
