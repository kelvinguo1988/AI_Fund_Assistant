"""TuShare 数据适配器 — 基金净值、指数数据

数据源级别：次级（当 AKShare 失败时使用）
需要 TuShare Pro Token（环境变量 TUSHARE_TOKEN）
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import tushare as ts  # type: ignore

from backend.data_sources.base import BaseDataSource, FundData, MarketIndices

logger = logging.getLogger(__name__)


class TuShareAdapter(BaseDataSource):
    """TuShare 数据适配器

    作为 AKShare 的次级数据源，需配置 TUSHARE_TOKEN。
    适用于获取基金净值历史、市场指数、债券收益率等。
    """

    MAX_RETRIES = 2
    BASE_DELAY = 1.0

    def __init__(self, token: str = "") -> None:
        self._available = False
        if token:
            try:
                ts.set_token(token)
                self._pro = ts.pro_api()
                self._available = True
            except Exception as e:
                logger.warning(f"TuShare 初始化失败: {e}")

    @property
    def available(self) -> bool:
        return self._available

    def _to_ts_code(self, code: str) -> str:
        """基金代码 → TuShare ts_code"""
        if code.startswith(("51", "56", "58", "15", "159", "588")):
            return f"{code}.SH"
        return f"{code}.SZ"

    async def get_fund_data(self, code: str, period: int = 250, fund_type: Optional[str] = None) -> FundData:
        if not self._available:
            raise RuntimeError("TuShare 未初始化（缺少 token）")

        fund_data = FundData(code=code)
        ts_code = self._to_ts_code(code)

        try:
            df = self._pro.fund_daily(
                ts_code=ts_code,
                start_date=(date.today() - timedelta(days=period * 2)).strftime("%Y%m%d"),
                end_date=date.today().strftime("%Y%m%d"),
            )
            if df is not None and not df.empty:
                df = df.sort_values("trade_date")
                fund_data.close_history = df["close"].astype(float).tolist()[-period:]
                fund_data.date_history = df["trade_date"].astype(str).tolist()[-period:]

                last_row = df.iloc[-1]
                fund_data.close = float(last_row["close"])
                fund_data.date = str(last_row["trade_date"])
                fund_data.name = str(last_row.get("ts_code", code))

                if "vol" in df.columns:
                    fund_data.volume_history = df["vol"].astype(float).tolist()[-period:]
                    fund_data.volume = float(df.iloc[-1]["vol"])
        except Exception as e:
            logger.warning(f"TuShare fund_daily 失败 code={code}: {e}")

        if not fund_data.close_history:
            raise ValueError(f"TuShare 未获取到基金数据 code={code}")

        return fund_data

    async def get_market_indices(self) -> MarketIndices:
        indices = MarketIndices()
        if not self._available:
            return indices

        index_map = {
            "000001.SH": "sh_composite",
            "399001.SZ": "sz_component",
            "399006.SZ": "cyb",
            "000300.SH": "hs300",
        }
        try:
            today_str = date.today().strftime("%Y%m%d")
            for ts_code, attr in index_map.items():
                try:
                    df = self._pro.index_daily(ts_code=ts_code, start_date=today_str, end_date=today_str)
                    if df is not None and not df.empty:
                        val = float(df.iloc[0].get("close", 0))
                        setattr(indices, attr, val)
                except Exception:
                    continue
            indices.date = date.today().isoformat()
        except Exception as e:
            logger.warning(f"TuShare 指数获取失败: {e}")

        return indices

    async def get_bond_yield(self) -> Optional[float]:
        """TuShare 的 shibor/国债数据"""
        if not self._available:
            return None
        try:
            df = self._pro.shibor(start_date=(date.today() - timedelta(days=5)).strftime("%Y%m%d"))
            if df is not None and not df.empty:
                return round(float(df.iloc[-1].get("1_y", 2.7)), 2)
        except Exception as e:
            logger.warning(f"TuShare 债券收益率获取失败: {e}")
        return None
