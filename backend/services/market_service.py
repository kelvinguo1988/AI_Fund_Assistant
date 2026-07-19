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
from backend.utils.concurrency import run_with_timeout, random_ua, USER_AGENTS

logger = logging.getLogger(__name__)

# 市场数据单次调用超时（秒）
_MARKET_TIMEOUT: float = 25.0


async def _rate_limited_call(func, *args, **kwargs):
    """带超时 + UA 轮换 + 指数退避重试（最多2次）

    修复说明：
    - 原实现使用全局 `_last_call_time` + `sleep(3)` 串行限流，导致 5+ 个市场数据
      调用串行排队，每次 3s 限流 + 2-5s jitter = 5-8s/次，总计 30-60s 纯等待。
    - 改为使用 `run_with_timeout` 内置的全局信号量（并发 5）限流，允许并发只限并发数。
    - 移除成功后的 `sleep(jitter)`，保留失败后的指数退避重试。
    - 超时从 45s 降为 25s（配合 patch 的 20s requests 超时），快速失败快速重试。
    """
    last_exc = None
    func_name = getattr(func, "__name__", repr(func))
    for attempt in range(1, 3):
        try:
            result = await run_with_timeout(
                func, *args, timeout=_MARKET_TIMEOUT, **kwargs
            )
            return result
        except Exception as e:
            last_exc = e
            if attempt < 2:
                delay = 3.0 + random.uniform(0, 2)
                logger.debug(
                    "%s 失败 (attempt %d/2): %s: %s, %.1fs后重试",
                    func_name, attempt, type(e).__name__, e, delay,
                )
                await asyncio.sleep(delay)

    logger.warning("%s 重试2次后失败: %s: %s", func_name, type(last_exc).__name__, last_exc)
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
                raise ValueError("empty dataframe")

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
        except Exception as orig_e:
            logger.warning(f"大盘资金流(akshare)获取失败: {type(orig_e).__name__}: {orig_e}，尝试 push2his 直连API")

        # 降级: push2his 历史K线直连 API
        result = await self._fetch_capital_flow_push2his()
        if result is not None:
            self._cache_set("market_capital_flow", result)
            return result

        # 备选: 东方财富 datacenter-web API 获取大盘资金流
        try:
            import requests as _req

            def _fetch_index_flow(index_code: str) -> dict:
                r = _req.get(
                    "https://datacenter-web.eastmoney.com/api/data/v1/get",
                    params={
                        "reportName": "RPT_MARKET_CAPITALFLOW",
                        "columns": "ALL",
                        "filter": f'(INDEX_CODE="{index_code}")(BONDTYPE="AB\u80a1")',
                        "pageSize": 1,
                        "sortColumns": "TRADE_DATE",
                        "sortTypes": -1,
                    },
                    headers={
                        "User-Agent": random_ua(),
                        "Referer": "https://data.eastmoney.com/",
                    },
                    timeout=15,
                )
                r.raise_for_status()
                items = r.json().get("result", {}).get("data", [])
                return items[0] if items else {}

            sh_data = await run_with_timeout(
                _fetch_index_flow, "000001.SH", timeout=20.0
            )
            sz_data = await run_with_timeout(
                _fetch_index_flow, "399001.SZ", timeout=20.0
            )
            if not sh_data:
                return None

            YI = 10_000  # 万元→亿 (1亿 = 10000万元)

            sh_main = (sh_data.get("MAIN_INFLOW") or 0) - (sh_data.get("MAIN_OUTFLOW") or 0)
            sz_main = (sz_data.get("MAIN_INFLOW") or 0) - (sz_data.get("MAIN_OUTFLOW") or 0) if sz_data else 0
            main_net = round((sh_main + sz_main) / YI, 2)

            super_net = round(
                ((sh_data.get("SUPERDEAL_NET") or 0) + ((sz_data.get("SUPERDEAL_NET") or 0) if sz_data else 0)) / YI, 2
            )
            large_net = round(
                ((sh_data.get("BIGDEAL_NET") or 0) + ((sz_data.get("BIGDEAL_NET") or 0) if sz_data else 0)) / YI, 2
            )
            medium_net = round(
                ((sh_data.get("MIDDEAL_NET") or 0) + ((sz_data.get("MIDDEAL_NET") or 0) if sz_data else 0)) / YI, 2
            )
            small_net = round(
                ((sh_data.get("SMALLDEAL_NET") or 0) + ((sz_data.get("SMALLDEAL_NET") or 0) if sz_data else 0)) / YI, 2
            )

            trade_date = (sh_data.get("TRADE_DATE") or "")[:10]
            main_ratio = round(main_net / (main_net + abs(medium_net) + abs(small_net)) * 100, 2) if (abs(medium_net) + abs(small_net)) > 0 else 0.0

            result = MarketCapitalFlow(
                date=trade_date,
                sh_index=None,  # datacenter API 不返回指数点位
                sh_change=round(sh_data.get("CHANGERATE") or 0, 2),
                sz_index=None,
                sz_change=round(sz_data.get("CHANGERATE") or 0, 2) if sz_data else None,
                main_flow=CapitalFlow(
                    net_amount=main_net,
                    net_ratio=main_ratio,
                    super_large_net=super_net,
                    large_net=large_net,
                    medium_net=medium_net,
                    small_net=small_net,
                ),
            )
            self._cache_set("market_capital_flow", result)
            return result
        except Exception as e:
            logger.warning(f"大盘资金流(datacenter-web API)获取失败: {type(e).__name__}: {e}，尝试 push2 实时API")

        # 最终兜底: push2 实时 API
        result = await self._fetch_capital_flow_push2_realtime()
        if result is not None:
            self._cache_set("market_capital_flow", result)
            return result

        return None

    async def _fetch_capital_flow_push2his(self) -> Optional[MarketCapitalFlow]:
        """通过 push2his 历史K线 API 获取大盘资金流（akshare 降级方案）"""
        import requests as _req

        def _do_fetch() -> str:
            r = _req.get(
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                params={
                    "lmt": 1,
                    "klt": 101,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                    "secid": "1.000001",
                    "secid2": "0.399001",
                },
                headers={
                    "User-Agent": random_ua(),
                    "Referer": "https://data.eastmoney.com/",
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            klines = data.get("data", {}).get("klines", [])
            if not klines:
                raise ValueError("push2his klines 为空")
            return klines[0]

        try:
            kline_str = await run_with_timeout(
                _do_fetch, timeout=20.0
            )
            parts = kline_str.split(",")
            if len(parts) < 15:
                raise ValueError(f"push2his kline 字段不足: {len(parts)}")

            YI = 100_000_000  # 元→亿元

            return MarketCapitalFlow(
                date=parts[0],
                sh_index=_parse_pct_value(parts[11]),
                sh_change=_parse_pct_value(parts[12]),
                sz_index=_parse_pct_value(parts[13]),
                sz_change=_parse_pct_value(parts[14]),
                main_flow=CapitalFlow(
                    net_amount=round(float(parts[1]) / YI, 2),
                    net_ratio=_parse_pct_value(parts[6]),
                    super_large_net=round(float(parts[5]) / YI, 2),
                    large_net=round(float(parts[4]) / YI, 2),
                    medium_net=round(float(parts[3]) / YI, 2),
                    small_net=round(float(parts[2]) / YI, 2),
                ),
            )
        except Exception as e:
            logger.warning(f"大盘资金流(push2his API)获取失败: {type(e).__name__}: {e}")
            return None

    async def _fetch_capital_flow_push2_realtime(self) -> Optional[MarketCapitalFlow]:
        """通过 push2 实时 API 获取大盘资金流（最终兜底方案）"""
        import requests as _req

        def _do_fetch() -> list:
            r = _req.get(
                "https://push2.eastmoney.com/api/qt/ulist.np/get",
                params={
                    "secids": "1.000001,0.399001",
                    "fields": "f62,f184,f66,f69,f72,f75,f78,f81,f84,f87",
                },
                headers={
                    "User-Agent": random_ua(),
                    "Referer": "https://data.eastmoney.com/",
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("data", {}).get("diff", [])
            if not items:
                raise ValueError("push2 realtime diff 为空")
            return items

        try:
            items = await run_with_timeout(
                _do_fetch, timeout=20.0
            )

            YI = 100_000_000
            main_net = 0.0
            super_net = 0.0
            large_net = 0.0
            medium_net = 0.0
            small_net = 0.0
            main_ratio = 0.0
            item_count = 0

            for item in items:
                main_net += float(item.get("f62", 0) or 0)
                super_net += float(item.get("f66", 0) or 0)
                large_net += float(item.get("f72", 0) or 0)
                medium_net += float(item.get("f78", 0) or 0)
                small_net += float(item.get("f84", 0) or 0)
                r = item.get("f184")
                if r is not None:
                    main_ratio += float(r)
                    item_count += 1

            if item_count > 0:
                main_ratio = round(main_ratio / item_count, 2)

            return MarketCapitalFlow(
                date=date.today().isoformat(),
                sh_index=None,
                sh_change=None,
                sz_index=None,
                sz_change=None,
                main_flow=CapitalFlow(
                    net_amount=round(main_net / YI, 2),
                    net_ratio=main_ratio,
                    super_large_net=round(super_net / YI, 2),
                    large_net=round(large_net / YI, 2),
                    medium_net=round(medium_net / YI, 2),
                    small_net=round(small_net / YI, 2),
                ),
            )
        except Exception as e:
            logger.warning(f"大盘资金流(push2 实时API)获取失败: {type(e).__name__}: {e}")
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
                logger.warning(f"板块资金流获取失败 {tf_label}: {type(e).__name__}: {e}")
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
