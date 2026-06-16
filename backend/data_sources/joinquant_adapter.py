"""JoinQuant (聚宽) 数据适配器 — 基金净值、指数、债券收益率

数据源级别：备用数据源（AKShare 失败后的备选之一）
需要 JoinQuant 账号（环境变量 JOINQUANT_USER / JOINQUANT_PASSWORD）
使用 jqdatasdk 包访问聚宽数据接口。
"""

import logging
from datetime import date, timedelta
from typing import Optional

from backend.data_sources.base import BaseDataSource, FundData, MarketIndices

logger = logging.getLogger(__name__)


# ETF 代码后缀映射（聚宽格式）
_ETF_SUFFIX_MAP = {
    "51": ".XSHG",  # 上交所 ETF
    "56": ".XSHG",
    "58": ".XSHG",
    "159": ".XSHE",  # 深交所 ETF
    "15": ".XSHE",
}


def _to_jq_code(code: str) -> str:
    """基金代码 → JoinQuant 格式"""
    for prefix, suffix in _ETF_SUFFIX_MAP.items():
        if code.startswith(prefix):
            return f"{code}{suffix}"
    # OTC 基金默认无后缀，聚宽直接用原始代码
    return code


class JoinQuantAdapter(BaseDataSource):
    """JoinQuant (聚宽) 数据适配器

    使用 jqdatasdk 获取基金净值、市场指数、债券收益率等数据。
    需要配置 JOINQUANT_USER 和 JOINQUANT_PASSWORD 环境变量。
    """

    MAX_RETRIES = 2
    BASE_DELAY = 1.0

    def __init__(self, user: str = "", password: str = "") -> None:
        self._available = False
        self._jq = None
        if user and password:
            try:
                import jqdatasdk as jq  # type: ignore  # lazy import
                jq.auth(user, password)
                self._jq = jq
                self._available = True
            except ImportError:
                logger.info("jqdatasdk 未安装，跳过 JoinQuant 数据源")
            except Exception as e:
                logger.warning(f"JoinQuant 认证失败: {e}")

    @property
    def available(self) -> bool:
        if self._available and self._jq:
            try:
                # 检查连接是否仍然有效
                return self._jq.is_auth()
            except Exception:
                self._available = False
        return self._available

    async def get_fund_data(
        self, code: str, period: int = 250, fund_type: Optional[str] = None,
    ) -> FundData:
        if not self._available or not self._jq:
            raise RuntimeError("JoinQuant 不可用（未认证或未安装 jqdatasdk）")

        jq = self._jq
        fund_data = FundData(code=code)
        jq_code = _to_jq_code(code)

        end_date = date.today()
        start_date = end_date - timedelta(days=period * 2)

        try:
            # 获取基金/ETF 日线数据
            df = jq.get_price(
                jq_code,
                start_date=start_date,
                end_date=end_date,
                frequency="daily",
                fields=["close", "volume", "high", "low", "open"],
                panel=False,
            )

            if df is None or df.empty:
                raise ValueError(f"JoinQuant 数据为空 code={code}")

            df = df.sort_values("time") if "time" in df.columns else df
            df = df.tail(period)

            fund_data.close_history = df["close"].astype(float).tolist()
            if "time" in df.columns:
                fund_data.date_history = df["time"].astype(str).tolist()
            elif "date" in df.columns:
                fund_data.date_history = df["date"].astype(str).tolist()

            last_row = df.iloc[-1]
            fund_data.close = float(last_row["close"])
            fund_data.date = str(last_row.get("time", last_row.get("date", "")))

            if "volume" in df.columns:
                fund_data.volume_history = df["volume"].astype(float).tolist()
                fund_data.volume = float(df.iloc[-1]["volume"])

        except Exception as e:
            logger.warning(f"JoinQuant 获取基金数据失败 code={code}: {e}")
            raise

        return fund_data

    async def get_market_indices(self) -> MarketIndices:
        """获取市场主要指数"""
        indices = MarketIndices()
        if not self._available or not self._jq:
            return indices

        jq = self._jq
        index_map = {
            "000001.XSHG": "sh_composite",  # 上证综指
            "399001.XSHE": "sz_component",  # 深证成指
            "399006.XSHE": "cyb",           # 创业板指
            "000300.XSHG": "hs300",         # 沪深300
        }

        today_str = date.today().strftime("%Y-%m-%d")
        for jq_code, attr in index_map.items():
            try:
                df = jq.get_price(
                    jq_code,
                    start_date=today_str,
                    end_date=today_str,
                    frequency="daily",
                    fields=["close"],
                    panel=False,
                )
                if df is not None and not df.empty:
                    val = float(df.iloc[-1]["close"])
                    setattr(indices, attr, val)
            except Exception:
                continue

        indices.date = date.today().isoformat()
        return indices

    async def get_bond_yield(self) -> Optional[float]:
        """获取 10 年期国债收益率（通过聚宽债券接口）"""
        if not self._available or not self._jq:
            return None

        jq = self._jq
        try:
            # 聚宽债券收益率接口
            df = jq.get_bond_yield(
                date=date.today(),
                tenor="10",
            )
            if df is not None and not df.empty:
                return round(float(df.iloc[-1].get("yield", 0)), 2)
        except Exception as e:
            logger.warning(f"JoinQuant 债券收益率获取失败: {e}")
        return None
