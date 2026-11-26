"""fundgz 下线后的降级链路（2026-11-26）

## 线上故障
仪表盘读不到基金数据，错误日志写「fundgz 反爬拦截，进入冷却」。

## 根因（GitHub 两个项目独立确认 + 本地实测）
天天基金应**监管要求**下线「基金实时估值」，`fundgz.1234567.com.cn`
现在对任意代码返回 **HTTP 200 + HTML「页面未找到」**（3924 字节），
JSONP 回调永不触发。原实现把「解析不出 JSONP」笼统判为反爬：

- 日志写 rate_limit → 排查方向全错（去查代理/换 UA，实际是政策下线）
- 每次失败进 600s 冷却 → 但接口**永远不会成功**，纯属空转

参考：
- zhengshengning/fund-baby PR#4「修复基金实时估值接口下线导致的数据加载失败」
- zichuanwangcloud-gif/fundsight PR#58「摆脱失效的盘中估值接口」

## 修复
1. `looks_like_html_error()` 精确识别 404 页（不能"见 HTML 就判下线"，
   真正的反爬拦截页仍要走冷却）
2. 判定下线后置 `_fundgz_dead`，本进程内不再请求，日志降级为 info 只说一次
3. 新增腾讯官方净值 `qt.gtimg.cn/q=jj{code}` 作为主替代，批量 50 只/次
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.fund_realtime_service import (  # noqa: E402
    FundRealtimeService, is_fundgz_dead, looks_like_html_error,
    parse_tencent_fund_nav,
)

# 实测的 fundgz 下线响应（HTTP 200，内容却是 404 页）
FUNDGZ_404_PAGE = (
    '<!doctype html>\n<html>\n<head>\n<meta http-equiv="Content-Type" content="text/html; '
    'charset=utf-8">\n<title>页面未找到 - 东方财富网</title>\n</head><body>...'
)
# 真实反爬拦截页（不含 404 特征）
ANTI_BOT_PAGE = "<html><body>blocked</body></html>"


class TestHtmlErrorDetection:
    def test_detects_eastmoney_404_page(self):
        """东方财富 404 页 → 判定为接口下线"""
        assert looks_like_html_error(FUNDGZ_404_PAGE) is True

    def test_anti_bot_page_not_treated_as_deprecated(self):
        """普通反爬拦截页**不能**被判成永久下线——那会误关整条链路"""
        assert looks_like_html_error(ANTI_BOT_PAGE) is False

    def test_valid_jsonp_not_html(self):
        assert looks_like_html_error('jsonpgz({"fundcode":"000001"})') is False

    def test_empty_text(self):
        assert looks_like_html_error("") is False


class TestTencentNavParser:
    RAW = (
        'v_jj009033="009033~建信上海金ETF联接A~0.0000~0.0000~~1.8062~1.8062~1.2330~2026-11-26~";\n'
        'v_jj968049="968049~摩根亚洲股息人民币派息~0.0000~0.0000~~12.4200~15.5700~-2.5118~2026-11-25~";'
    )

    def test_parses_multiple(self):
        got = parse_tencent_fund_nav(self.RAW)
        assert set(got) == {"009033", "968049"}

    def test_field_mapping(self):
        got = parse_tencent_fund_nav(self.RAW)["009033"]
        assert got["name"] == "建信上海金ETF联接A"
        assert got["dwjz"] == pytest.approx(1.8062)   # 单位净值
        assert got["gszzl"] == pytest.approx(1.233)   # 日涨跌%
        assert got["jzrq"] == "2026-11-26"            # 净值日期
        assert got["source"] == "tencent_nav"

    def test_qdii_covered(self):
        """QDII/互认基金也能取到——原数据源长期不覆盖 968049"""
        got = parse_tencent_fund_nav(self.RAW)["968049"]
        assert got["dwjz"] == pytest.approx(12.42)
        assert got["gszzl"] == pytest.approx(-2.5118)

    def test_zero_nav_is_skipped(self):
        """腾讯用 0.0 表示无数据（盘中字段），不能当真实净值"""
        raw = 'v_jj000000="000000~某基金~0.0000~0.0000~~0.0000~0.0000~0.0000~2026-11-26~";'
        assert parse_tencent_fund_nav(raw) == {}

    def test_garbage_input(self):
        for bad in ("", "not a quote", "v_jj123", 'v_jj1="short"'):
            assert parse_tencent_fund_nav(bad) == {}


class TestFallbackWiring:
    def test_tencent_layer_before_holdings(self):
        """腾讯官方净值必须排在持仓自算之前（官方数字 > 近似估算）"""
        import inspect
        from backend.services import fund_realtime_service as m

        src = inspect.getsource(m.FundRealtimeService.get_realtime)
        i_tx = src.find("_fetch_tencent_nav")
        i_hd = src.find("_fill_from_holdings")
        assert i_tx > 0 and i_hd > 0
        assert i_tx < i_hd, "腾讯净值应先于持仓自算"

    def test_dead_flag_exposed(self):
        assert isinstance(is_fundgz_dead(), bool)

    def test_batch_size_defined(self):
        from backend.services.fund_realtime_service import TENCENT_FUND_BATCH
        assert 0 < TENCENT_FUND_BATCH <= 60, "腾讯接口单次上限约 60"

    def test_source_tag_distinguishes_nav_from_estimate(self):
        """source=tencent_nav 与 fundgz 的估算要能区分开"""
        import inspect
        from backend.services import fund_realtime_service as m

        src = inspect.getsource(m.FundRealtimeService.get_realtime)
        assert '"tencent_nav"' in src
        assert '"official_nav"' in src, "est_model 要标明这是官方净值不是估算"
