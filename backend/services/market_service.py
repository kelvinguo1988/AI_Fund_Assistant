"""市场概况服务 — 资金流、板块排行、沪深港通数据"""

import asyncio
import logging
import random
import time
from datetime import date, timedelta
from typing import Optional

import akshare as ak  # type: ignore

from backend.schemas.market import (
    CapitalFlow,
    HSGTFlow,
    MarketAdvDecline,
    MarketCapitalFlow,
    MarketTurnover,
    SectorFlowItem,
    SectorFlowRanking,
)

logger = logging.getLogger(__name__)

# 限流：与 akshare_adapter 风格一致
_last_call_time: float = 0.0
_MIN_INTERVAL = 3.0


_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]


async def _rate_limited_call(func, *args, **kwargs):
    """限流 + UA 轮换 + 指数退避重试（最多2次）"""
    import functools

    # 轮换 UA
    try:
        session = getattr(ak, "_session", None)
        if session is not None:
            session.headers.update({"User-Agent": random.choice(_USER_AGENTS)})
    except Exception:
        pass

    last_exc = None
    for attempt in range(1, 3):
        global _last_call_time
        now = time.time()
        since_last = now - _last_call_time
        if since_last < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - since_last)
        _last_call_time = time.time()

        try:
            partial = functools.partial(func, *args, **kwargs)
            result = await asyncio.wait_for(asyncio.to_thread(partial), timeout=45.0)
            jitter = random.uniform(2.0, 5.0)
            await asyncio.sleep(jitter)
            return result
        except Exception as e:
            last_exc = e
            _last_call_time = 0  # 失败时重置
            if attempt < 2:
                delay = 5.0 + random.uniform(1, 3)
                logger.debug(f"{func.__name__} 失败 (attempt {attempt}/2): {e}, {delay:.1f}s后重试")
                await asyncio.sleep(delay)

    logger.warning(f"{func.__name__} 重试2次后失败: {last_exc}")
    raise last_exc


def _parse_flow_value(val) -> float:
    """解析资金流数值，转为亿元（原始值为元）"""
    if val is None or str(val) in ("", "None", "nan"):
        return 0.0
    try:
        raw = float(val)
        # 原始单位是元，转为亿元
        return round(raw / 100_000_000, 2)
    except (ValueError, TypeError):
        return 0.0


def _parse_pct_value(val) -> float:
    """解析百分比数值（原始值为 %）"""
    if val is None or str(val) in ("", "None", "nan"):
        return 0.0
    try:
        return round(float(val), 2)
    except (ValueError, TypeError):
        return 0.0


class MarketService:
    """市场概况服务"""

    # 简单 TTL 缓存: {cache_key: (timestamp, result)}
    _cache: dict[str, tuple[float, object]] = {}
    _CACHE_TTL = 300  # 5 分钟

    @staticmethod
    def clear_cache() -> None:
        """清空所有缓存，强制下次请求重新获取"""
        MarketService._cache.clear()
        logger.debug("MarketService 缓存已清空")

    @staticmethod
    def _cache_get(key: str) -> object:
        entry = MarketService._cache.get(key)
        if entry is None:
            return None
        ts, val = entry
        if time.time() - ts > MarketService._CACHE_TTL:
            del MarketService._cache[key]
            return None
        return val

    @staticmethod
    def _cache_set(key: str, val: object) -> None:
        MarketService._cache[key] = (time.time(), val)

    async def get_market_capital_flow(self) -> Optional[MarketCapitalFlow]:
        """获取大盘资金流概况"""
        cached = self._cache_get("market_capital_flow")
        if cached is not None:
            return cached  # type: ignore[return-value]
        try:
            df = await _rate_limited_call(ak.stock_market_fund_flow)
            if df is None or df.empty:
                return None

            latest = df.iloc[-1]
            result = MarketCapitalFlow(
                date=str(latest.get("日期", "")),
                sh_index=_parse_pct_value(latest.get("上证-收盘价")),
                sh_change=_parse_pct_value(latest.get("上证-涨跌幅")),
                sz_index=_parse_pct_value(latest.get("深证-收盘价")),
                sz_change=_parse_pct_value(latest.get("深证-涨跌幅")),
                main_flow=CapitalFlow(
                    net_amount=_parse_flow_value(latest.get("主力净流入-净额")),
                    net_ratio=_parse_pct_value(latest.get("主力净流入-净占比")),
                    super_large_net=_parse_flow_value(latest.get("超大单净流入-净额")),
                    large_net=_parse_flow_value(latest.get("大单净流入-净额")),
                    medium_net=_parse_flow_value(latest.get("中单净流入-净额")),
                    small_net=_parse_flow_value(latest.get("小单净流入-净额")),
                ),
            )
            self._cache_set("market_capital_flow", result)
            return result
        except Exception as e:
            logger.warning(f"大盘资金流获取失败: {e}")
            return None

    async def get_sector_flow_rankings(self) -> dict[str, SectorFlowRanking]:
        """获取多时间维度板块资金流排行（THS 同花顺接口）"""
        cached = self._cache_get("sector_flow_rankings")
        if cached is not None:
            return cached  # type: ignore[return-value]
        symbol_map = {
            "当天": "即时",
            "周": "5日排行",
            "月": "10日排行",
        }

        results: dict[str, SectorFlowRanking] = {}
        for tf_label, symbol in symbol_map.items():
            try:
                df = await _rate_limited_call(ak.stock_fund_flow_industry, symbol=symbol)
                ranking = self._parse_ths_sector_df(df, tf_label, symbol)
                results[tf_label] = ranking
            except Exception as e:
                logger.warning(f"板块资金流获取失败 {tf_label}: {e}")
                results[tf_label] = SectorFlowRanking(timeframe=tf_label)

        self._cache_set("sector_flow_rankings", results)
        return results

    def _parse_ths_sector_df(self, df, timeframe: str, symbol: str) -> SectorFlowRanking:
        """解析同花顺行业资金流 DataFrame 为排行"""
        if df is None or df.empty:
            return SectorFlowRanking(timeframe=timeframe)

        is_instant = symbol == "即时"
        items: list[SectorFlowItem] = []
        for _, row in df.iterrows():
            change_pct = 0.0
            pct_raw = row.get("行业-涨跌幅" if is_instant else "阶段涨跌幅", 0)
            if isinstance(pct_raw, str) and "%" in pct_raw:
                change_pct = float(pct_raw.replace("%", ""))
            else:
                change_pct = _parse_pct_value(pct_raw)

            net = _parse_pct_value(row.get("净额", 0))
            top_stock = str(row.get("领涨股", "")) if is_instant else ""

            items.append(SectorFlowItem(
                sector_name=str(row.get("行业", "")),
                change_pct=change_pct,
                main_net_inflow=net,
                main_net_ratio=0.0,  # THS API 不提供净占比
                top_stock=top_stock,
            ))

        # 按净额排序，分别从全量数据取 top 10 流入 / 流出
        items.sort(key=lambda x: x.main_net_inflow, reverse=True)
        inflow = [i for i in items if i.main_net_inflow > 0][:10]
        outflow = [i for i in reversed(items) if i.main_net_inflow < 0][:10]

        return SectorFlowRanking(
            timeframe=timeframe,
            by_inflow=inflow,
            by_outflow=outflow,
        )

    async def get_hsgt_flow(self) -> Optional[HSGTFlow]:
        """获取沪深港通资金流"""
        cached = self._cache_get("hsgt_flow")
        if cached is not None:
            return cached  # type: ignore[return-value]
        try:
            df = await _rate_limited_call(ak.stock_hsgt_fund_flow_summary_em)
            if df is None or df.empty:
                return None

            hsgt = HSGTFlow()
            north_total = 0.0
            south_total = 0.0

            for _, row in df.iterrows():
                direction = str(row.get("资金方向", ""))
                net_buy_val = row.get("成交净买额", 0)
                if net_buy_val is None or str(net_buy_val) in ("", "None", "nan"):
                    net_buy_val = 0.0
                net_buy = round(float(net_buy_val), 2)

                if "北向" in direction:
                    north_total += net_buy
                elif "南向" in direction:
                    south_total += net_buy

                trade_date = str(row.get("交易日", ""))
                if trade_date and trade_date > hsgt.date:
                    hsgt.date = trade_date

            hsgt.north_net_buy = round(north_total, 2)
            hsgt.south_net_buy = round(south_total, 2)

            if not hsgt.date:
                hsgt.date = date.today().isoformat()

            self._cache_set("hsgt_flow", hsgt)
            return hsgt
        except Exception as e:
            logger.warning(f"沪深港通资金流获取失败: {e}")
            return None

    async def get_market_adv_decline(self) -> Optional[MarketAdvDecline]:
        """获取全市场涨跌分布（同花顺行业汇总）"""
        cached = self._cache_get("market_adv_decline")
        if cached is not None:
            return cached  # type: ignore[return-value]
        try:
            df = await _rate_limited_call(ak.stock_board_industry_summary_ths)
            if df is None or df.empty:
                return None

            up = int(df["上涨家数"].sum())
            down = int(df["下跌家数"].sum())
            result = MarketAdvDecline(
                up_count=up,
                down_count=down,
                total_count=up + down,
            )
            self._cache_set("market_adv_decline", result)
            return result
        except Exception as e:
            logger.warning(f"涨跌分布获取失败: {e}")
            return None

    async def get_market_turnover(self) -> Optional[MarketTurnover]:
        """获取两市成交额及较上一日涨跌"""
        cached = self._cache_get("market_turnover")
        if cached is not None:
            return cached  # type: ignore[return-value]
        try:
            # 获取最近交易日
            today_data = await _rate_limited_call(ak.stock_sse_summary)
            if today_data is None or today_data.empty:
                return None
            today_str = today_data[today_data["项目"] == "报告时间"].iloc[0, 1]

            # 沪市成交额
            sse_today = await _rate_limited_call(
                ak.stock_sse_deal_daily, date=today_str
            )
            sse_amount = float(
                sse_today[sse_today["单日情况"] == "成交金额"]["股票"].iloc[0]
            )

            # 深市成交额
            szse_today = await _rate_limited_call(
                ak.stock_szse_summary, date=today_str
            )
            szse_amount = float(
                szse_today[szse_today["证券类别"] == "股票"]["成交金额"].iloc[0]
            ) / 100_000_000

            total = round(sse_amount + szse_amount, 2)

            # 上一交易日
            prev_str = today_str
            prev_total = 0.0
            for _ in range(10):
                dt = date(
                    int(prev_str[:4]), int(prev_str[4:6]), int(prev_str[6:])
                ) - timedelta(days=1)
                prev_str = dt.strftime("%Y%m%d")
                try:
                    sse_prev = await _rate_limited_call(
                        ak.stock_sse_deal_daily, date=prev_str
                    )
                    szse_prev = await _rate_limited_call(
                        ak.stock_szse_summary, date=prev_str
                    )
                    if sse_prev is not None and not sse_prev.empty:
                        sse_amt = float(
                            sse_prev[sse_prev["单日情况"] == "成交金额"]["股票"].iloc[0]
                        )
                        szse_amt = float(
                            szse_prev[szse_prev["证券类别"] == "股票"]["成交金额"].iloc[0]
                        ) / 100_000_000
                        prev_total = round(sse_amt + szse_amt, 2)
                        break
                except Exception:
                    continue

            change_pct = 0.0
            if prev_total > 0:
                change_pct = round((total - prev_total) / prev_total * 100, 2)

            result = MarketTurnover(
                sse_amount=round(sse_amount, 2),
                szse_amount=round(szse_amount, 2),
                total_amount=total,
                prev_total_amount=prev_total,
                change_pct=change_pct,
            )
            self._cache_set("market_turnover", result)
            return result
        except Exception as e:
            logger.warning(f"两市成交额获取失败: {e}")
            return None
