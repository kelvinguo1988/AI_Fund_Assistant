"""腾讯全市场 ETF 快照（2026-10-19 根因修复的替代数据源）

背景：东财 push2/push2delay 两集群对该部署出口 IP 的 HTTP 层精确封禁
（同族 fundsuggest/push2his/anonflow2 全部 200 可用；curl/openssl/python
三客户端一致被掐 —— 排除TLS指纹，确认 IP 层面 HTTP 拒绝），
akshare fund_etf_spot_em（唯一全市场量价快照源）恒失败 → /etf-scan
恒「全市场快照不可用：eastmoney 熔断中」。

本模块提供完整替代链（2026-10-19 实测 17 批零失败、5.2s、1677/1686 只）：

  清单: akshare fund_etf_fund_daily_em（fund.eastmoney.com 族，未封禁）
        → [(code,name)] 全市场 1686 只
  行情: 腾讯 qt.gtimg.cn 批量 50/批（现有稳定源，无鉴权稳接口）
        → f3 最新价 / f32 涨跌幅 / f37成交额(万) / f38 换手率% / f49 量比
        → 主买占比 = (外盘6 − 内盘7)/(外盘6 + 内盘7) × 100

口径注记（与东财 fund_etf_spot_em 对照，4 只白盘样本逐一相等）：
  - f38 换手率% 与 f49 量比 与东财口径一致（f43 是盘口动态量比，勿用）；
  - 腾讯无「主力净流入」概念，用主买占比替代：东财 4.4% vs 腾讯主买
    28.7%，绝对值偏高 ~20pp、相对排名可用，前端文案已相应改为「主买占优榜」。

腾讯没有 IOPV/折溢价字段 → 这两个字段恒 None，`evaluate_etf_hints`
   的折溢价提示自动跳过（腾讯链路下无该提示：文档口径，已确认可接受）。
"""
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 腾讯 ETF 全市场行情批量参数
TX_BATCH_SIZE = 50
TX_BATCH_GAP = 0.15        # 批间隔（秒）
TX_TIMEOUT = 10.0
TX_FAIL_RATIO = 0.5        # 失败批次占比超过该值 → 返回 None（不熔断共享源）

# 与 backend/services/fund_realtime_service.py 保持同源（只 import 不重复定义）
from backend.services.fund_realtime_service import TENCENT_URL, tencent_code  # noqa: F401,E402


def _to_float(v) -> Optional[float]:
    if v in (None, "", "-", "--"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_tencent_etf_row(payload: str) -> Optional[dict]:
    """腾讯单行 ETF 行情 → 标准快照行(与 _get_etf_spot 同 schema)

    腾讯响应行: v_sh510300="1~名称~code~3~4~...~";
    字段: [1]名称 [3]最新价 [6]外盘(手) [7]内盘(手) [30]时间 [32]涨跌幅%
          [37]成交额(万) [38]换手率% [49]量比
    """
    fields = payload.strip().strip('"').split("~")
    if len(fields) <= 49:
        return None
    code = fields[2]
    if not code.isdigit():
        return None
    try:
        pct = float(fields[32])
    except (TypeError, ValueError):
        return None
    ob, ib = _to_float(fields[6]), _to_float(fields[7])
    if ob is None or ib is None or ob + ib <= 0:
        buy_ratio = None
    else:
        buy_ratio = (ob - ib) / (ob + ib) * 100.0
    turnover = _to_float(fields[38])
    vol_ratio = _to_float(fields[49])
    amount_wan = _to_float(fields[37])
    return {
        "code": code,
        "name": fields[1],
        "price": _to_float(fields[3]),
        "pct": pct,
        "time": fields[30] if fields[30] else "",
        "turnover_rate": turnover,
        "volume_ratio": vol_ratio,
        "main_inflow_pct": buy_ratio,   # 语义=主买占比(非主力), 前端文案已对齐
        "amount": amount_wan * 1e4 if amount_wan is not None else None,
        # 1 位: 深市 15x/16x ETF/LOF; 5 位: 沪市 51x/56x/58x ETF
        "fund_type": "etf" if code[0] in "156" else None,
    }


def parse_tencent_etf_batch(text: str) -> dict[str, dict]:
    """腾讯批量响应(GBK 文本) → {纯数字code: 标准快照行}"""
    out: dict[str, dict] = {}
    for line in (text or "").split(";"):
        line = line.strip()
        if not line.startswith("v_") or "=" not in line:
            continue
        var, payload = line.split("=", 1)
        # 腾讯对不存在的代码返回 v_pv_none_match="1" → 跳过
        if "v_pv_none" in var:
            continue
        row = parse_tencent_etf_row(payload)
        if row is not None and row["code"].isdigit():
            out[row.pop("code")] = row
    return out


def to_tencent_etf_code(code: str) -> str:
    """ETF 纯数字代码 → 腾讯带市场前缀

    实测 2026-10-19: 5/6 开头=sh(沪市 ETF 51/56/58), 1/5 开头全覆盖不到
    深市 15/16/159 → sz。ETF 代码首位只可能是 5(沪)/1(深)。
    """
    if len(code) == 6 and code[0] == "5":
        return "sh" + code
    if len(code) == 6 and code[0] == "1":
        return "sz" + code
    # 兜底走通用规则（股票/其他: 港股 5位 hk / 北交所 92 bj）
    return tencent_code(code)
