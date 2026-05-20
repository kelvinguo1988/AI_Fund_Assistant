"""AKShare 数据适配器 — 获取基金净值、PE、PB、成交量、指数数据"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import asyncio

import akshare as ak  # type: ignore
import pandas as pd

from backend.data_sources.base import BaseDataSource, FundData, MarketIndices

logger = logging.getLogger(__name__)


class AKShareAdapter(BaseDataSource):
    """AKShare 数据适配器实现

    支持获取：
    - ETF/场外基金净值历史
    - 指数 PE/PB（通过 index_value_name_funddb 接口）
    - 成交量（ETF 场内交易数据）
    - 市场主要指数
    - 10 年期国债收益率
    """

    MAX_RETRIES = 2       # 最大重试次数（含首次）
    BASE_DELAY = 1.0      # 初始退避延迟（秒）
    _last_call_time: float = 0.0  # 上次 API 调用时间
    _min_call_interval: float = 1.0  # 最小调用间隔（秒），防止触发限流

    async def _call(self, func, *args, **kwargs):
        """带限流 + 指数退避重试的异步 API 调用

        AKShare 的 HTTP 连接可能因网络波动、限流等原因断开。
        限制调用频率避免触发反爬。
        """
        import functools
        import time

        # 限流：确保两次调用间隔不少于 _min_call_interval
        now = time.time()
        since_last = now - self._last_call_time
        if since_last < self._min_call_interval:
            await asyncio.sleep(self._min_call_interval - since_last)
        self._last_call_time = time.time()

        last_exc = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                partial = functools.partial(func, *args, **kwargs)
                return await asyncio.wait_for(
                    asyncio.to_thread(partial),
                    timeout=15.0,
                )
            except (asyncio.TimeoutError, Exception) as e:
                last_exc = e
                if attempt < self.MAX_RETRIES:
                    delay = self.BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"{func.__name__} 失败 (attempt {attempt}/{self.MAX_RETRIES}): {e}, "
                        f"{delay:.1f}s 后重试..."
                    )
                    await asyncio.sleep(delay)
        logger.error(f"{func.__name__} 重试 {self.MAX_RETRIES} 次后仍然失败: {last_exc}")
        raise last_exc

    async def get_fund_data(self, code: str, period: int = 250) -> FundData:
        """获取基金完整数据

        Args:
            code: 基金代码 如 "510300"
            period: 回看天数

        Returns:
            FundData 对象
        """
        fund_data = FundData(code=code)

        try:
            fund_data = await self._get_etf_data(code, period)
        except Exception as e:
            logger.warning(f"ETF 数据获取失败 code={code}: {e}, 尝试场外基金接口")
            try:
                fund_data = await self._get_otc_fund_data(code, period)
            except Exception as e2:
                logger.error(f"场外基金数据获取也失败 code={code}: {e2}")

        # 补充债券收益率
        try:
            fund_data.bond_yield = await self.get_bond_yield()
        except Exception as e:
            logger.warning(f"国债收益率获取失败: {e}")

        # 补充基准指数行情（沪深300，用于信息比率计算）
        if not fund_data.benchmark_history:
            try:
                await self._fill_benchmark_data(fund_data, period)
            except Exception as e:
                logger.warning(f"基准指数数据获取失败: {e}")

        # 补充基金规模数据（用于规模稳定性计算）
        if not fund_data.fund_size_history:
            try:
                await self._fill_fund_size(code, fund_data)
            except Exception as e:
                logger.warning(f"基金规模数据获取失败: {e}")

        return fund_data

    async def _get_etf_data(self, code: str, period: int) -> FundData:
        """获取 ETF 场内交易数据"""
        fund_data = FundData(code=code)

        # 尝试获取 ETF 行情数据（带重试）
        df = await self._call(ak.fund_etf_hist_em, symbol=code, period="daily", adjust="qfq")
        if df is None or df.empty:
            raise ValueError(f"ETF 行情数据为空 code={code}")

        df = df.tail(period)
        df = df.sort_values("日期")

        fund_data.close_history = df["收盘"].astype(float).tolist()
        fund_data.volume_history = df["成交量"].astype(float).tolist()
        fund_data.date_history = df["日期"].astype(str).tolist()

        last_row = df.iloc[-1]
        fund_data.close = float(last_row["收盘"])
        fund_data.volume = float(last_row["成交量"])
        fund_data.date = str(last_row["日期"])

        # 尝试获取基金名称
        try:
            info_df = await self._call(ak.fund_etf_spot_em)
            if info_df is not None and not info_df.empty:
                match = info_df[info_df["代码"] == code]
                if not match.empty:
                    fund_data.name = str(match.iloc[0]["名称"])
        except Exception as e:
            logger.warning(f"ETF 名称获取失败 code={code}: {e}")

        # 尝试获取 PE/PB 数据（通过关联指数）
        try:
            await self._fill_pe_pb_for_etf(code, fund_data)
        except Exception as e:
            logger.warning(f"ETF PE/PB 数据获取失败 code={code}: {e}")

        return fund_data

    async def _get_otc_fund_data(self, code: str, period: int) -> FundData:
        """获取场外基金净值数据"""
        fund_data = FundData(code=code)

        try:
            df = await self._call(ak.fund_open_fund_info_em, symbol=code, indicator="单位净值走势")
            if df is not None and not df.empty:
                df = df.tail(period)
                df = df.sort_values("净值日期")

                fund_data.close_history = df["单位净值"].astype(float).tolist()
                fund_data.date_history = df["净值日期"].astype(str).tolist()

                last_row = df.iloc[-1]
                fund_data.close = float(last_row["单位净值"])
                fund_data.date = str(last_row["净值日期"])
        except Exception as e:
            logger.warning(f"场外基金净值获取失败 code={code}: {e}")

        # 尝试获取基金名称
        try:
            info_df = await self._call(ak.fund_name_em)
            if info_df is not None and not info_df.empty:
                match = info_df[info_df["基金代码"] == code]
                if not match.empty:
                    fund_data.name = str(match.iloc[0]["基金简称"])
        except Exception as e:
            logger.warning(f"场外基金名称获取失败 code={code}: {e}")

        return fund_data

    async def _fill_pe_pb_for_etf(self, code: str, fund_data: FundData) -> None:
        """根据 ETF 代码尝试填充 PE/PB 数据

        通过 index_value_name_funddb 接口获取关联指数的估值数据
        """
        # ETF 代码到指数代码的映射
        etf_index_map = {
            "510300": "000300",  # 沪深300ETF → 沪深300
            "510500": "000905",  # 中证500ETF → 中证500
            "510050": "000016",  # 上证50ETF → 上证50
            "159915": "399006",  # 创业板ETF → 创业板指
            "512100": "000016",  # 中证1000ETF
            "588000": "000688",  # 科创50ETF
        }

        index_code = etf_index_map.get(code)
        if index_code is None:
            logger.debug(f"ETF {code} 无关联指数映射，跳过 PE/PB 获取")
            return

        try:
            df = await self._call(ak.index_value_name_funddb)
            if df is not None and not df.empty:
                match = df[df["指数代码"] == index_code]
                if not match.empty:
                    row = match.iloc[0]
                    pe_str = str(row.get("市盈率", ""))
                    pb_str = str(row.get("市净率", ""))
                    if pe_str and pe_str not in ("", "None", "nan"):
                        fund_data.pe = float(pe_str)
                    if pb_str and pb_str not in ("", "None", "nan"):
                        fund_data.pb = float(pb_str)
        except Exception as e:
            logger.warning(f"PE/PB 数据获取失败 index={index_code}: {e}")

    async def _fill_benchmark_data(self, fund_data: FundData, period: int) -> None:
        """填充基准指数（沪深300）历史行情用于信息比率计算"""
        df = await self._call(ak.stock_zh_index_daily, symbol="sh000300")
        if df is not None and not df.empty:
            df = df.tail(period + 10)
            df = df.sort_values("date")
            fund_data.benchmark_history = df["close"].astype(float).tolist()
            logger.info(f"基准指数数据填充完成: {len(fund_data.benchmark_history)} 行")

    async def _fill_fund_size(self, code: str, fund_data: FundData) -> None:
        """填充基金季度规模数据用于规模稳定性计算"""
        try:
            df = await self._call(ak.fund_scale_open_sina, symbol=code)
            if df is not None and not df.empty:
                # 寻找规模相关字段
                size_col = None
                for col in ["总募集规模", "总资产", "净资产", "最新规模", "基金规模"]:
                    if col in df.columns:
                        size_col = col
                        break
                if size_col:
                    sizes = df[size_col].dropna().astype(float).tail(4).tolist()
                    if sizes:
                        fund_data.fund_size_history = sizes
                        logger.info(f"基金规模数据填充完成: {len(sizes)} 期")
        except Exception as e:
            logger.debug(f"基金规模获取失败 code={code}: {e}")
            # fund_scale_open_sina 可能对部分基金不返回数据，静默忽略

    async def get_market_indices(self) -> MarketIndices:
        """获取市场主要指数数据"""
        indices = MarketIndices()
        try:
            df = await self._call(ak.stock_zh_index_spot_em)
            if df is not None and not df.empty:
                today = date.today().strftime("%Y-%m-%d")
                indices.date = today

                index_map = {
                    "000001": "sh_composite",    # 上证综指
                    "399001": "sz_component",    # 深证成指
                    "399006": "cyb",             # 创业板指
                    "000300": "hs300",           # 沪深300
                }

                for _, row in df.iterrows():
                    code = str(row.get("代码", ""))
                    if code in index_map:
                        attr = index_map[code]
                        latest = row.get("最新价", None)
                        if latest is not None and str(latest) not in ("", "None", "nan"):
                            setattr(indices, attr, float(latest))
        except Exception as e:
            logger.warning(f"市场指数获取失败: {e}")

        return indices

    async def get_bond_yield(self) -> Optional[float]:
        """获取 10 年期国债收益率"""
        try:
            # 尝试从 bond_china_yield 接口获取
            df = await self._call(ak.bond_china_yield, start_date="20240101")
            if df is not None and not df.empty:
                # 筛选 10 年期国债
                bond_10y = df[df["债券类型"] == "国债"]
                if not bond_10y.empty:
                    latest = bond_10y.sort_values("日期").iloc[-1]
                    yield_val = latest.get("收益率", None)
                    if yield_val is not None and str(yield_val) not in ("", "None", "nan"):
                        return float(yield_val)
        except Exception as e:
            logger.warning(f"国债收益率获取失败: {e}")

        # 回退：使用常见值 2.7%
        logger.info("国债收益率获取失败，使用回退值 2.7%")
        return 2.7
