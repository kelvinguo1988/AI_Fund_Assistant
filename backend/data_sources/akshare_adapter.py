"""AKShare 数据适配器 — 获取基金净值、PE、PB、成交量、指数数据"""

import logging
import random
from datetime import date, datetime, timedelta
from typing import Optional

import asyncio

import akshare as ak  # type: ignore
import pandas as pd

from backend.data_sources.base import BaseDataSource, FundData, MarketIndices, guess_fund_type

logger = logging.getLogger(__name__)

# User-Agent 池用于反爬虫
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
]


class AKShareAdapter(BaseDataSource):
    """AKShare 数据适配器实现

    支持获取：
    - ETF/场外基金净值历史
    - 指数 PE/PB（通过 stock_zh_index_value_csindex 接口）
    - 成交量（ETF 场内交易数据）
    - 市场主要指数
    - 10 年期国债收益率
    """

    MAX_RETRIES = 3       # 最大重试次数（含首次）
    BASE_DELAY = 2.0      # 初始退避延迟（秒）

    # 类级缓存 + 文件持久化：通过 fund_open_fund_rank_em 批量获取基金名称映射，
    # 避免每次查询都走网络请求。内存 dict 缓存 + JSON 文件持久化。
    _fund_name_map: Optional[dict[str, str]] = None  # {code: name}
    _fund_rank_df: Optional[pd.DataFrame] = None  # 原始 DataFrame，用于增量匹配
    _cache_timestamp: float = 0.0
    _CACHE_TTL: float = 3600.0
    _CACHE_FILE: str = "fund_name_cache.json"

    # 共享数据缓存：全市场基金共用的数据（国债收益率、沪深300基准、指数估值），
    # 避免每只基金重复请求同一接口。51 只基金原实现会产生 100+ 次重复调用。
    _bond_yield_cache: Optional[float] = None
    _bond_yield_ts: float = 0.0
    _benchmark_cache: Optional[list[float]] = None
    _benchmark_ts: float = 0.0
    _index_value_cache: dict[str, tuple[float, pd.DataFrame]] = {}  # {index_code: (ts, df)}
    _SHARED_CACHE_TTL: float = 3600.0  # 共享数据缓存 1 小时

    # ETF 行情缓存：fund_etf_spot_em 返回全量 ETF 行情，多只 ETF 并发时复用
    _etf_spot_cache: Optional[pd.DataFrame] = None
    _etf_spot_ts: float = 0.0

    # ── 并发缓存锁（防止缓存穿透时多协程同时发起网络请求）──────────────
    # 背景：51 只基金并发分析时，第一只基金的网络请求还没返回填充缓存，
    # 其余 50 只基金都已检测到缓存为 None 并同时发起网络请求，
    # 导致 91 次 fund_open_fund_rank_em + 47 次 stock_zh_index_value_csindex 超时。
    # 修复：使用 asyncio.Lock + 双重检查模式，确保同一接口同一时间只有一个网络请求。
    # 使用 lazy init（Python 3.9 中 asyncio.Lock 不能在类定义时创建）。
    _fund_name_lock: Optional["asyncio.Lock"] = None
    _bond_yield_lock: Optional["asyncio.Lock"] = None
    _benchmark_lock: Optional["asyncio.Lock"] = None
    _index_value_locks: dict[str, "asyncio.Lock"] = {}  # 按 index_code 分锁
    _etf_spot_lock: Optional["asyncio.Lock"] = None

    @classmethod
    def _get_lock(cls, lock_attr: str, key: Optional[str] = None) -> "asyncio.Lock":
        """Lazy-init asyncio.Lock

        Python 3.9 中 asyncio.Lock() 在类定义时创建会触发 DeprecationWarning，
        因此使用 lazy init，在第一次实际使用时创建并绑定到当前事件循环。

        Args:
            lock_attr: 类属性名（如 '_fund_name_lock'）
            key: 可选的二级 key（用于按 index_code 分锁），None 时使用 lock_attr
        """
        if key is not None:
            lock = cls._index_value_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                cls._index_value_locks[key] = lock
            return lock
        else:
            lock = getattr(cls, lock_attr)
            if lock is None:
                lock = asyncio.Lock()
                setattr(cls, lock_attr, lock)
            return lock

    async def _call(self, func, *args, **kwargs):
        """带超时 + UA 轮换 + 指数退避重试的异步 API 调用

        修复说明：
        - 原实现使用类级全局 `_last_call_time` + `sleep(3)` 串行限流，导致 40-60 只
          基金 × (3s 限流 + 2-5s jitter + 请求耗时) = 数十分钟串行等待。
        - 改为使用 `run_with_timeout` 内置的全局信号量（并发 5）限流，允许并发。
        - 移除成功后的 `sleep(jitter)`，保留失败后的指数退避重试。
        - 使用独立线程池，避免 akshare 卡住时孤儿线程耗尽默认线程池。
        - 超时从 30s 降为 25s（配合 patch 的 20s requests 超时）。

        支持 _max_attempts 参数覆盖最大重试次数：
        - 有降级接口的调用（如 ETF→OTC）设 1，失败立即切接口
        - 无降级的独立调用保持默认 3 次
        """
        max_attempts = kwargs.pop('_max_attempts', self.MAX_RETRIES)

        from backend.utils.concurrency import run_with_timeout

        last_exc = None
        func_name = getattr(func, "__name__", repr(func))
        for attempt in range(1, max_attempts + 1):
            try:
                result = await run_with_timeout(func, *args, timeout=25.0, **kwargs)
                return result
            except (asyncio.TimeoutError, ConnectionError, ConnectionResetError, Exception) as e:
                last_exc = e
                if attempt < max_attempts:
                    delay = self.BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1)
                    reason = type(e).__name__
                    msg = str(e) or "(no error message)"
                    logger.warning(
                        f"{func_name} 失败 (attempt {attempt}/{max_attempts}): "
                        f"[{reason}] {msg}, {delay:.1f}s 后重试..."
                    )
                    await asyncio.sleep(delay)
        reason = type(last_exc).__name__
        msg = str(last_exc) or "(no error message)"
        logger.error(f"{func_name} 重试 {max_attempts} 次后仍然失败: [{reason}] {msg}")
        raise last_exc

    async def get_fund_data(self, code: str, period: int = 250, fund_type: Optional[str] = None) -> FundData:
        """获取基金完整数据

        根据 fund_type（或代码前缀推测）直接路由到对应接口，
        避免对所有 OTC 基金先试 ETF 接口再降级的无效轮询。

        Args:
            code: 基金代码 如 "510300"
            period: 回看天数
            fund_type: "etf" / "otc"，None 时由代码前缀自动推测

        Returns:
            FundData 对象
        """
        fund_data = FundData(code=code)

        # 确定主路径：优先使用传入的 fund_type，无则按代码前缀推测
        primary_type = (fund_type or guess_fund_type(code))
        is_primary_etf = (primary_type == "etf")

        if is_primary_etf:
            primary = self._get_etf_data
            fallback = self._get_otc_fund_data
            primary_name = "ETF"
            fallback_name = "场外基金"
        else:
            primary = self._get_otc_fund_data
            fallback = self._get_etf_data
            primary_name = "场外基金"
            fallback_name = "ETF"

        try:
            fund_data = await primary(code, period)
            logger.debug(f"{primary_name} 数据获取成功 code={code}")
        except Exception as e:
            logger.warning(f"{primary_name} 数据获取失败 code={code}: {e}, 尝试{fallback_name}接口")
            try:
                fund_data = await fallback(code, period)
            except Exception as e2:
                logger.error(f"{fallback_name}数据获取也失败 code={code}: {e2}")

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

        # 尝试获取 ETF 行情数据（1 次失败立即切场外基金接口，不重试）
        df = await self._call(ak.fund_etf_hist_em, symbol=code, period="daily", adjust="qfq", _max_attempts=1)
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

        # 尝试获取基金名称（带缓存的 fund_etf_spot_em）
        try:
            await self._fill_etf_name(code, fund_data)
        except Exception as e:
            logger.warning(f"ETF 名称获取失败 code={code}: {e}")

        # 尝试获取 PE/PB 数据（通过关联指数）
        try:
            await self._fill_pe_pb_for_etf(code, fund_data)
        except Exception as e:
            logger.warning(f"ETF PE/PB 数据获取失败 code={code}: {e}")

        return fund_data

    async def _fill_etf_name(self, code: str, fund_data: FundData) -> None:
        """填充 ETF 名称（带缓存的 fund_etf_spot_em）

        优化：fund_etf_spot_em 返回全量 ETF 行情数据（800+ 只），
        多只 ETF 并发时复用同一份缓存，避免重复请求。
        使用 asyncio.Lock 防止缓存穿透。
        """
        import time as _time

        # 无锁快速检查缓存
        now = _time.time()
        if (
            AKShareAdapter._etf_spot_cache is not None
            and now - AKShareAdapter._etf_spot_ts < AKShareAdapter._SHARED_CACHE_TTL
        ):
            match = AKShareAdapter._etf_spot_cache[AKShareAdapter._etf_spot_cache["代码"] == code]
            if not match.empty:
                fund_data.name = str(match.iloc[0]["名称"])
                logger.debug(f"ETF 名称命中缓存 code={code}")
                return

        # 获取锁后再次检查
        lock = self._get_lock('_etf_spot_lock')
        async with lock:
            # 双重检查
            now = _time.time()
            if (
                AKShareAdapter._etf_spot_cache is not None
                and now - AKShareAdapter._etf_spot_ts < AKShareAdapter._SHARED_CACHE_TTL
            ):
                match = AKShareAdapter._etf_spot_cache[AKShareAdapter._etf_spot_cache["代码"] == code]
                if not match.empty:
                    fund_data.name = str(match.iloc[0]["名称"])
                    logger.debug(f"ETF 名称命中缓存（锁内复用）code={code}")
                    return

            info_df = await self._call(ak.fund_etf_spot_em)
            if info_df is not None and not info_df.empty:
                AKShareAdapter._etf_spot_cache = info_df
                AKShareAdapter._etf_spot_ts = now
                match = info_df[info_df["代码"] == code]
                if not match.empty:
                    fund_data.name = str(match.iloc[0]["名称"])
                    logger.info(f"ETF 行情缓存已填充: {len(info_df)} 只（首次网络请求）")

    async def _get_otc_fund_nav_raw(self, code: str, period: int) -> Optional[pd.DataFrame]:
        """直接调用天天基金原始 API 获取场外基金净值（备用）

        当 akshare 的 fund_open_fund_info_em 失败时使用此接口。
        这是天天基金前端页面真实调用的 API，稳定性远高于 akshare 的页面解析。
        """
        import requests

        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=period * 2)).isoformat()

        url = "https://api.fund.eastmoney.com/f10/lsjz"
        headers = {
            "Referer": f"https://fund.eastmoney.com/f10/jjjz_{code}.html",
            "User-Agent": random.choice(_USER_AGENTS),
        }
        params = {
            "fundCode": code,
            "pageIndex": 1,
            "pageSize": max(period * 2, 90),
            "startDate": start_date,
            "endDate": end_date,
        }

        def _fetch():
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()

        try:
            from backend.utils.concurrency import run_with_timeout
            data = await run_with_timeout(_fetch, timeout=15.0)
        except Exception as e:
            logger.debug(f"天天基金原始 API 获取失败 code={code}: {e}")
            return None

        if data.get("Data") and data["Data"].get("LSJZList"):
            df = pd.DataFrame(data["Data"]["LSJZList"])
            df = df.rename(columns={
                "FSRQ": "净值日期",
                "DWJZ": "单位净值",
                "LJJZ": "累计净值",
                "JZZZL": "日增长率",
            })
            df["净值日期"] = pd.to_datetime(df["净值日期"])
            df = df.sort_values("净值日期")
            df["单位净值"] = df["单位净值"].astype(float)
            return df.tail(period)

        logger.debug(f"天天基金原始 API 返回空数据 code={code}")
        return None

    async def _get_otc_fund_data(self, code: str, period: int) -> FundData:
        """获取场外基金净值数据

        策略 1: akshare fund_open_fund_info_em
        策略 2: 天天基金原始 API（akshare 对场外基金支持不好时降级）
        """
        fund_data = FundData(code=code)
        df = None

        # 策略 1: akshare
        try:
            df = await self._call(ak.fund_open_fund_info_em, symbol=code, indicator="单位净值走势", _max_attempts=1)
            if df is not None and df.empty:
                df = None
        except Exception as e:
            logger.warning(f"场外基金净值获取失败 code={code} (akshare): {e}")

        # 策略 2: 天天基金原始 API（akshare 失败时降级）
        if df is None:
            logger.info(f"尝试天天基金原始 API 获取 code={code}")
            df = await self._get_otc_fund_nav_raw(code, period)

        if df is not None and not df.empty:
            df = df.tail(period)
            df = df.sort_values("净值日期")

            fund_data.close_history = df["单位净值"].astype(float).tolist()
            fund_data.date_history = df["净值日期"].astype(str).tolist()

            last_row = df.iloc[-1]
            fund_data.close = float(last_row["单位净值"])
            fund_data.date = str(last_row["净值日期"])

        # 尝试获取基金名称（多策略，带缓存）
        name = await self._get_cached_fund_name(code)
        if name:
            fund_data.name = name

        return fund_data

    @classmethod
    def _load_name_cache_from_file(cls) -> Optional[dict[str, str]]:
        """从 JSON 文件加载持久化的基金名称映射"""
        import json, os
        if os.path.exists(cls._CACHE_FILE):
            try:
                with open(cls._CACHE_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.debug(f"基金名称缓存文件读取失败: {e}")
        return None

    @classmethod
    def _save_name_cache_to_file(cls, name_map: dict[str, str]) -> None:
        """将基金名称映射持久化到 JSON 文件"""
        import json
        try:
            with open(cls._CACHE_FILE, "w") as f:
                json.dump(name_map, f, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"基金名称缓存文件写入失败: {e}")

    async def _get_cached_fund_name(self, code: str) -> Optional[str]:
        """从缓存（内存 → 文件 → 网络）获取基金名称

        优先使用 fund_open_fund_rank_em 批量拉取全量基金名称，
        缓存在内存 dict 和 JSON 文件中，后续查询不产生网络调用。
        完全避开 fund_name_em（东财反爬高危接口）。

        并发安全：使用 asyncio.Lock + 双重检查模式，防止 51 只基金并发时
        同时穿透缓存发起 51 次网络请求（实测导致 91 次超时）。
        """
        import time

        # 步骤 1: 无锁快速检查内存缓存（命中则直接返回，不阻塞）
        now = time.time()
        if AKShareAdapter._fund_name_map is not None:
            name = AKShareAdapter._fund_name_map.get(code)
            if name is not None:
                return name
            # 内存中有但找不到该 code → 尝试刷新（可能新增基金）
            if now - AKShareAdapter._cache_timestamp < AKShareAdapter._CACHE_TTL:
                return None  # 缓存未过期，确实没有

        # 步骤 2: 文件缓存（进程间持久化，无锁检查）
        if AKShareAdapter._fund_name_map is None:
            file_cache = self._load_name_cache_from_file()
            if file_cache is not None:
                AKShareAdapter._fund_name_map = file_cache
                AKShareAdapter._cache_timestamp = now
                name = file_cache.get(code)
                if name is not None:
                    return name

        # 步骤 3: 获取锁后再次检查（double-checked locking）
        # 防止 51 只基金并发时同时穿透：第一个协程发网络请求填充缓存，
        # 其余协程等待锁释放后直接读缓存。
        lock = self._get_lock('_fund_name_lock')
        async with lock:
            # 双重检查：等待锁期间可能已被其他协程填充
            now = time.time()
            if AKShareAdapter._fund_name_map is not None:
                name = AKShareAdapter._fund_name_map.get(code)
                if name is not None:
                    logger.debug(f"基金名称命中缓存（锁内复用）code={code}")
                    return name
                if now - AKShareAdapter._cache_timestamp < AKShareAdapter._CACHE_TTL:
                    return None

            # 仍需网络请求（只有第一个进入的协程会走到这里）
            try:
                rank_df = await self._call(ak.fund_open_fund_rank_em, symbol="全部")
                if rank_df is not None and not rank_df.empty:
                    name_map = dict(zip(rank_df["基金代码"].astype(str), rank_df["基金简称"]))
                    AKShareAdapter._fund_name_map = name_map
                    AKShareAdapter._fund_rank_df = rank_df
                    AKShareAdapter._cache_timestamp = now
                    # 异步写文件（不阻塞）
                    self._save_name_cache_to_file(name_map)
                    logger.info(f"基金名称缓存已填充: {len(name_map)} 只（首次网络请求）")
                    return name_map.get(code)
            except Exception as e:
                logger.debug(f"场外基金名称获取失败 code={code} (rank_em): {e}")

        return None

    async def _fill_pe_pb_for_etf(self, code: str, fund_data: FundData) -> None:
        """根据 ETF 代码尝试填充 PE/PB 数据

        通过 stock_zh_index_value_csindex 接口获取关联指数的估值数据。
        优化：按指数代码缓存，同一指数不重复请求（如多只 ETF 都跟踪沪深300）。

        并发安全：按 index_code 分锁，防止多只跟踪同一指数的 ETF 并发时
        同时穿透缓存（实测 stock_zh_index_value_csindex 47 次超时）。
        """
        import time as _time

        # ETF 代码到指数代码的映射
        etf_index_map = {
            "510300": "000300",  # 沪深300ETF → 沪深300
            "510500": "000905",  # 中证500ETF → 中证500
            "510050": "000016",  # 上证50ETF → 上证50
            "159915": "399006",  # 创业板ETF → 创业板指
            "512100": "000852",  # 中证1000ETF
            "588000": "000688",  # 科创50ETF
        }

        index_code = etf_index_map.get(code)
        if index_code is None:
            logger.debug(f"ETF {code} 无关联指数映射，跳过 PE/PB 获取")
            return

        # 检查缓存（无锁快速路径）
        now = _time.time()
        cached = AKShareAdapter._index_value_cache.get(index_code)
        if cached is not None:
            ts, df = cached
            if now - ts < AKShareAdapter._SHARED_CACHE_TTL and df is not None:
                row = df.iloc[-1]
                pe_str = str(row.get("市盈率1", ""))
                pb_str = str(row.get("市盈率2", ""))
                if pe_str and pe_str not in ("", "None", "nan"):
                    fund_data.pe = float(pe_str)
                if pb_str and pb_str not in ("", "None", "nan"):
                    fund_data.pb = float(pb_str)
                logger.debug(f"PE/PB 命中缓存 index={index_code}")
                return

        # 获取锁后再次检查（按 index_code 分锁，不同指数不互相阻塞）
        lock = self._get_lock('_index_value_locks', key=index_code)
        async with lock:
            # 双重检查：等待锁期间可能已被其他协程填充
            now = _time.time()
            cached = AKShareAdapter._index_value_cache.get(index_code)
            if cached is not None:
                ts, df = cached
                if now - ts < AKShareAdapter._SHARED_CACHE_TTL and df is not None:
                    row = df.iloc[-1]
                    pe_str = str(row.get("市盈率1", ""))
                    pb_str = str(row.get("市盈率2", ""))
                    if pe_str and pe_str not in ("", "None", "nan"):
                        fund_data.pe = float(pe_str)
                    if pb_str and pb_str not in ("", "None", "nan"):
                        fund_data.pb = float(pb_str)
                    logger.debug(f"PE/PB 命中缓存（锁内复用）index={index_code}")
                    return

            try:
                df = await self._call(ak.stock_zh_index_value_csindex, symbol=index_code)
                if df is not None and not df.empty:
                    # 写入缓存
                    AKShareAdapter._index_value_cache[index_code] = (now, df)
                    row = df.iloc[-1]  # 取最新一条
                    pe_str = str(row.get("市盈率1", ""))
                    pb_str = str(row.get("市盈率2", ""))
                    if pe_str and pe_str not in ("", "None", "nan"):
                        fund_data.pe = float(pe_str)
                    if pb_str and pb_str not in ("", "None", "nan"):
                        fund_data.pb = float(pb_str)
                    logger.info(f"PE/PB 缓存已填充 index={index_code}（首次网络请求）")
            except Exception as e:
                logger.warning(f"PE/PB 数据获取失败 index={index_code}: {e}")

    async def _fill_benchmark_data(self, fund_data: FundData, period: int) -> None:
        """填充基准指数（沪深300）历史行情用于信息比率计算

        优化：沪深300是全市场共享基准，51 只基金重复请求同一接口。
        添加类级缓存，1 小时内复用，避免重复网络调用。

        并发安全：使用 asyncio.Lock 防止 51 只基金并发时同时穿透缓存。
        """
        import time as _time

        # 无锁快速检查缓存
        now = _time.time()
        if (
            AKShareAdapter._benchmark_cache is not None
            and now - AKShareAdapter._benchmark_ts < AKShareAdapter._SHARED_CACHE_TTL
        ):
            fund_data.benchmark_history = AKShareAdapter._benchmark_cache
            logger.debug(f"基准指数数据命中缓存: {len(fund_data.benchmark_history)} 行")
            return

        # 获取锁后再次检查（double-checked locking）
        lock = self._get_lock('_benchmark_lock')
        async with lock:
            # 双重检查：等待锁期间可能已被其他协程填充
            now = _time.time()
            if (
                AKShareAdapter._benchmark_cache is not None
                and now - AKShareAdapter._benchmark_ts < AKShareAdapter._SHARED_CACHE_TTL
            ):
                fund_data.benchmark_history = AKShareAdapter._benchmark_cache
                logger.debug(f"基准指数数据命中缓存（锁内复用）: {len(fund_data.benchmark_history)} 行")
                return

            df = await self._call(ak.stock_zh_index_daily, symbol="sh000300")
            if df is not None and not df.empty:
                df = df.tail(period + 10)
                df = df.sort_values("date")
                history = df["close"].astype(float).tolist()
                fund_data.benchmark_history = history
                # 写入缓存
                AKShareAdapter._benchmark_cache = history
                AKShareAdapter._benchmark_ts = now
                logger.info(f"基准指数数据填充完成: {len(history)} 行（首次网络请求）")

    async def _fill_fund_size(self, code: str, fund_data: FundData) -> None:
        """填充基金季度规模数据用于规模稳定性计算

        fund_scale_open_sina 接口对多数基金不可靠（KeyError），直接跳过以节省时间。
        策略: fund_scale_daily_szse（深交所 ETF 日频份额数据，仅 159 开头代码可用）。
        """
        # 仅尝试深交所 ETF 份额数据（159xxx）
        if code.startswith("159"):
            try:
                end = date.today().isoformat()
                start = (date.today() - timedelta(days=720)).isoformat()
                df = await self._call(ak.fund_scale_daily_szse, start_date=start, end_date=end, symbol="ETF")
                if df is not None and not df.empty:
                    match = df[df["基金代码"] == code]
                    if not match.empty:
                        fund_data.fund_size_history = match["基金份额"].astype(float).tail(4).tolist()
                        logger.info(f"基金规模数据填充完成: {len(fund_data.fund_size_history)} 期 (daily_szse)")
                        return
            except Exception as e:
                logger.debug(f"基金规模获取失败 code={code} (daily_szse): {e}")

        logger.debug(f"基金规模无可用数据源 code={code}，规模稳定性因子将使用中性值")

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
        """获取 10 年期国债收益率

        优化：国债收益率是全市场共享数据，所有基金用同一个值。
        添加类级缓存，1 小时内复用，避免 51 只基金重复请求同一接口。

        并发安全：使用 asyncio.Lock 防止 51 只基金并发时同时穿透缓存
        （实测 stock_zh_index_value_csindex 在 get_bond_yield 和 _fill_pe_pb_for_etf
        中同时被调用，并发时大量超时）。

        注：bond_china_yield 数据源自 2021 年起未更新，新日期范围返回空。
        当前优先尝试实时替代接口，仍不可得时使用回退值。
        """
        import time as _time

        # 无锁快速检查缓存
        now = _time.time()
        if (
            AKShareAdapter._bond_yield_cache is not None
            and now - AKShareAdapter._bond_yield_ts < AKShareAdapter._SHARED_CACHE_TTL
        ):
            return AKShareAdapter._bond_yield_cache

        # 获取锁后再次检查（double-checked locking）
        lock = self._get_lock('_bond_yield_lock')
        async with lock:
            # 双重检查：等待锁期间可能已被其他协程填充
            now = _time.time()
            if (
                AKShareAdapter._bond_yield_cache is not None
                and now - AKShareAdapter._bond_yield_ts < AKShareAdapter._SHARED_CACHE_TTL
            ):
                logger.debug(f"国债收益率命中缓存（锁内复用）: {AKShareAdapter._bond_yield_cache}")
                return AKShareAdapter._bond_yield_cache

            # 策略 1: 尝试全局指数估值表获取无风险利率参考
            # 优化：优先复用 _fill_pe_pb_for_etf 已缓存的 000300 数据，
            # 避免同一接口被 get_bond_yield 和 _fill_pe_pb_for_etf 同时调用。
            cached_df = None
            cached_entry = AKShareAdapter._index_value_cache.get("000300")
            if cached_entry is not None:
                ts, cached_df = cached_entry
                if now - ts >= AKShareAdapter._SHARED_CACHE_TTL:
                    cached_df = None

            if cached_df is not None and not cached_df.empty:
                last_row = cached_df.iloc[-1]
                div_yield = last_row.get("股息率1", None)
                if div_yield and str(div_yield) not in ("", "None", "nan"):
                    result = round(float(div_yield) + 1.5, 2)
                    logger.info(f"基于股息率估算无风险利率: {float(div_yield):.2f}%（复用 PE/PB 缓存）")
                    AKShareAdapter._bond_yield_cache = result
                    AKShareAdapter._bond_yield_ts = now
                    return result

            try:
                df = await self._call(ak.stock_zh_index_value_csindex, symbol="000300")
                if df is not None and not df.empty:
                    # 同时写入 _index_value_cache 供 _fill_pe_pb_for_etf 复用
                    AKShareAdapter._index_value_cache["000300"] = (now, df)
                    last_row = df.iloc[-1]
                    div_yield = last_row.get("股息率1", None)
                    if div_yield and str(div_yield) not in ("", "None", "nan"):
                        # 股息率 ≈ 无风险利率替代，保守加 1.5% 风险溢价作为 10Y 国债近似
                        result = round(float(div_yield) + 1.5, 2)
                        logger.info(f"基于股息率估算无风险利率: {float(div_yield):.2f}%（首次网络请求）")
                        AKShareAdapter._bond_yield_cache = result
                        AKShareAdapter._bond_yield_ts = now
                        return result
            except Exception as e:
                logger.debug(f"国债收益率估值替代获取失败: {e}")

            # 策略 2: 尝试 bond_china_yield 历史接口（仅 2021 年前数据有效）
            try:
                df = await self._call(ak.bond_china_yield, start_date="20200101")
                if df is not None and not df.empty:
                    bond_10y = df[df["曲线名称"] == "中债国债收益率曲线"]
                    if not bond_10y.empty:
                        latest = bond_10y.sort_values("日期").iloc[-1]
                        yield_val = latest.get("10年", None)
                        if yield_val is not None and str(yield_val) not in ("", "None", "nan"):
                            result = float(yield_val)
                            AKShareAdapter._bond_yield_cache = result
                            AKShareAdapter._bond_yield_ts = now
                            return result
            except Exception as e:
                logger.debug(f"国债收益率历史接口获取失败: {e}")

            # 回退：不再硬编码 2.7% 假数据，返回 None 由引擎层显式降级
            # （与 factor_engine.py 的 2.5% 默认值叠加会基于过时利率假设产生信号）
            logger.warning("国债收益率实时接口不可用，返回 None 由引擎降级")
            return None
