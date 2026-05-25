"""BaoStock 数据适配器 — 备用数据源（无需 token）

数据源级别：备用 1（当 AKShare 和 TuShare 都失败时使用）
BaoStock 免费、无需注册，提供股票/指数日线行情数据。
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import baostock as bs  # type: ignore
import pandas as pd

from backend.data_sources.base import BaseDataSource, FundData, MarketIndices

logger = logging.getLogger(__name__)


_ETF_PREFIX_MAP = {
    "51": "sh",
    "56": "sh",
    "58": "sh",
    "588": "sh",
    "159": "sz",
    "15": "sz",
}


def _to_bs_code(code: str) -> str:
    """基金代码 → BaoStock 格式"""
    for prefix, market in _ETF_PREFIX_MAP.items():
        if code.startswith(prefix):
            return f"{market}.{code}"
    # OTC 基金默认用 sz
    return f"sz.{code}"


class BaoStockAdapter(BaseDataSource):
    """BaoStock 数据适配器

    免费数据源，无需 token，作为备用方案提供基金净值和指数数据。
    """

    MAX_RETRIES = 2
    BASE_DELAY = 1.0

    def __init__(self) -> None:
        self._available = False
        try:
            bs.login()
            self._available = True
        except Exception as e:
            logger.warning(f"BaoStock 登录失败: {e}")

    def __del__(self) -> None:
        if self._available:
            try:
                bs.logout()
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return self._available

    async def get_fund_data(self, code: str, period: int = 250, fund_type: Optional[str] = None) -> FundData:
        if not self._available:
            raise RuntimeError("BaoStock 未登录")

        fund_data = FundData(code=code)
        bs_code = _to_bs_code(code)

        end_date = date.today().strftime("%Y-%m-%d")
        start_date = (date.today() - timedelta(days=period * 2)).strftime("%Y-%m-%d")

        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                fields="date,close,volume,peTTM,pbMRQ",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2",  # 前复权
            )
            if rs.error_code != "0":
                raise ValueError(f"BaoStock 查询失败: {rs.error_msg}")

            df = rs.get_data()
            if df is None or df.empty:
                raise ValueError(f"BaoStock 数据为空 code={code}")

            df = df.dropna(subset=["close"])
            df = df.tail(period)

            fund_data.close_history = df["close"].astype(float).tolist()
            fund_data.date_history = df["date"].astype(str).tolist()

            last_row = df.iloc[-1]
            fund_data.close = float(last_row["close"])
            fund_data.date = str(last_row["date"])

            if "volume" in df.columns:
                fund_data.volume_history = df["volume"].astype(float).tolist()
                fund_data.volume = float(df.iloc[-1]["volume"])

            if "peTTM" in df.columns:
                pe = df.iloc[-1].get("peTTM")
                if pe and str(pe) not in ("", "None", "nan"):
                    fund_data.pe = float(pe)

            if "pbMRQ" in df.columns:
                pb = df.iloc[-1].get("pbMRQ")
                if pb and str(pb) not in ("", "None", "nan"):
                    fund_data.pb = float(pb)
        except Exception as e:
            logger.warning(f"BaoStock 获取基金数据失败 code={code}: {e}")
            raise

        return fund_data

    async def get_market_indices(self) -> MarketIndices:
        indices = MarketIndices()
        if not self._available:
            return indices

        index_map = {
            "sh.000001": "sh_composite",
            "sz.399001": "sz_component",
            "sz.399006": "cyb",
            "sh.000300": "hs300",
        }
        today_str = date.today().strftime("%Y-%m-%d")

        for bs_code, attr in index_map.items():
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    fields="date,close",
                    start_date=today_str,
                    end_date=today_str,
                    frequency="d",
                )
                if rs.error_code == "0":
                    df = rs.get_data()
                    if df is not None and not df.empty:
                        val = float(df.iloc[-1]["close"])
                        setattr(indices, attr, val)
            except Exception:
                continue

        indices.date = date.today().isoformat()
        return indices

    async def get_bond_yield(self) -> Optional[float]:
        """BaoStock 不提供债券收益率"""
        return None

