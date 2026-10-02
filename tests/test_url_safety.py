"""SSRF 防护回归测试 — 连通性/调休同步的 URL 白名单校验"""

import pytest

from backend.services.connectivity_service import _validate_public_url


def test_rejects_non_https():
    with pytest.raises(ValueError, match="HTTPS"):
        _validate_public_url("http://example.com/a.json")


def test_rejects_literal_private_ips():
    for url in (
        "https://127.0.0.1/x",
        "https://10.0.0.5/x",
        "https://192.168.1.1/x",
        "https://172.16.9.9/x",
    ):
        with pytest.raises(ValueError, match="内网|保留"):
            _validate_public_url(url)


def test_rejects_cloud_metadata_address():
    # 169.254.169.254 属链路本地段，是 SSRF 打元数据服务的经典目标
    with pytest.raises(ValueError, match="内网|保留"):
        _validate_public_url("https://169.254.169.254/latest/meta-data/")


def test_rejects_unspecified_address():
    with pytest.raises(ValueError, match="内网|保留"):
        _validate_public_url("https://0.0.0.0/x")


def test_rejects_domain_resolving_to_loopback():
    # 域名指向 127.0.0.1 是绕过字面 IP 黑名单的经典手法
    with pytest.raises(ValueError, match="内网|保留"):
        _validate_public_url("https://localhost/x")


def test_accepts_public_https_host():
    try:
        _validate_public_url("https://raw.githubusercontent.com/x/{year}.json")
    except ValueError as e:
        # 离线环境下 DNS 不可解析属预期（同样应拒绝发起请求），不得放行内网目标
        assert "无法解析" in str(e), f"意外拒绝原因: {e}"


class TestHolidayHopValidation:
    """调休同步的逐跳 SSRF 校验

    旧实现把 httpx 才有的 `resolution_callback` 传给 requests.get → 每次调用恒抛
    TypeError，异常被按年吞进 errors，holiday_calendar 长期 0 行（2026-10-01 审查 P1）。
    """

    class _Resp:
        def __init__(self, status=200, location=None, payload=None):
            self.status_code = status
            self.headers = {"Location": location} if location else {}
            self._payload = payload or {"days": []}

        @property
        def is_redirect(self):
            return self.status_code in (301, 302, 303, 307, 308) and bool(self.headers.get("Location"))

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    @pytest.fixture
    def harness(self, monkeypatch):
        """返回 (validated_urls, requested_urls)；校验器替换为不依赖 DNS 的假实现"""
        validated, requested = [], []

        def fake_validate(url):
            validated.append(url)
            if any(p in url for p in ("169.254.", "127.0.0.1", "10.0.0.")):
                raise ValueError("拒绝内网/保留地址")

        monkeypatch.setattr(
            "backend.services.connectivity_service._validate_public_url", fake_validate
        )
        return validated, requested, monkeypatch

    def _patch_get(self, monkeypatch, requested, responses):
        it = iter(responses)

        def fake_get(url, **kwargs):
            requested.append((url, kwargs))
            return next(it)

        monkeypatch.setattr("requests.get", fake_get)

    async def test_basic_fetch_works_and_ignores_redirect_kwarg(self, harness):
        from backend.services.holiday_sync_service import fetch_holiday_json

        validated, requested, monkeypatch = harness
        self._patch_get(monkeypatch, requested, [self._Resp(payload={"days": [
            {"date": "2026-10-01", "name": "国庆节", "isOffDay": True}]})])

        data = await fetch_holiday_json(2026, "https://raw.githubusercontent.com/x/{year}.json")
        assert data["days"][0]["date"] == "2026-10-01"
        assert validated == ["https://raw.githubusercontent.com/x/2026.json"]
        # 必须手动跟随重定向：交给 requests 自动跟随 = 跳过逐跳校验
        assert requested[0][1].get("allow_redirects") is False

    async def test_each_hop_revalidated(self, harness):
        from backend.services.holiday_sync_service import fetch_holiday_json

        validated, requested, monkeypatch = harness
        self._patch_get(monkeypatch, requested, [
            self._Resp(302, location="https://cdn.example.com/2026.json"),
            self._Resp(payload={"days": [{"date": "2026-05-01", "isOffDay": True}]}),
        ])

        data = await fetch_holiday_json(2026, "https://raw.githubusercontent.com/x/{year}.json")
        assert data["days"]
        assert validated == [
            "https://raw.githubusercontent.com/x/2026.json",
            "https://cdn.example.com/2026.json",
        ]

    async def test_redirect_to_metadata_address_blocked_before_request(self, harness):
        from backend.services.holiday_sync_service import fetch_holiday_json

        validated, requested, monkeypatch = harness
        self._patch_get(monkeypatch, requested, [
            self._Resp(302, location="http://169.254.169.254/latest/meta-data/"),
        ])

        with pytest.raises(ValueError, match="内网"):
            await fetch_holiday_json(2026, "https://raw.githubusercontent.com/x/{year}.json")
        # 第二跳根本不该发出请求
        assert len(requested) == 1

    async def test_redirect_loop_gives_up(self, harness):
        from backend.services.holiday_sync_service import fetch_holiday_json

        validated, requested, monkeypatch = harness
        self._patch_get(monkeypatch, requested,
                        [self._Resp(302, location="https://cdn.example.com/hop") for _ in range(10)])

        with pytest.raises(ValueError, match="重定向"):
            await fetch_holiday_json(2026, "https://raw.githubusercontent.com/x/{year}.json")


class TestEastmoneyHostMatch:
    """补丁的目标域判定（审查 P2：原为 `domain in url` 子串匹配）

    子串匹配会把 NID 令牌 + Referer 注入仿冒主机（凭据外泄），
    故判定必须落在 URL 的主机名上。
    """

    def test_real_hosts_match(self):
        from backend.patch.eastmoney_patch import _target_host
        assert _target_host("https://fund.eastmoney.com/pingzhongdata/004011.js") == "fund.eastmoney.com"
        assert _target_host("https://api.fund.eastmoney.com/f10/lsjz") == "api.fund.eastmoney.com"

    def test_case_and_port_normalized(self):
        from backend.patch.eastmoney_patch import _target_host
        assert _target_host("https://FUNDf10.EastMoney.com:443/x") == "fundf10.eastmoney.com"

    def test_spoofed_hosts_rejected(self):
        from backend.patch.eastmoney_patch import _target_host
        for url in (
            "https://fund.eastmoney.com.attacker.net/x",
            "https://attacker.net/?next=https%3A//fund.eastmoney.com/",
            "https://notfund.eastmoney.com/x",
            "https://attacker.net/fund.eastmoney.com/js/x.js",
        ):
            assert _target_host(url) is None, url

    def test_non_eastmoney_host_rejected(self):
        from backend.patch.eastmoney_patch import _target_host
        assert _target_host("https://localhost/fund.eastmoney.com") is None
        assert _target_host("") is None
