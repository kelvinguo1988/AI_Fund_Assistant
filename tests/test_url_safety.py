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
