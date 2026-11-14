"""系统版本徽标（2026-11-14）

需求：顶栏标题后显示版本号 + 构建 commit 短哈希。

踩过的坑：schema 里声明了 version/build_sha 两个字段，但路由构造
AIConfigOut 时**漏填** → 前端永远拿到 null，徽标不显示。
这种「schema 有、实现没填」的遗漏单测不覆盖就会悄悄回归。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.version import APP_VERSION, BUILD_SHA, version_display  # noqa: E402


class TestVersionModule:
    def test_app_version_nonempty(self):
        assert APP_VERSION, "版本号不能为空"
        assert APP_VERSION.count(".") >= 1, "应是 x.y.z 形式"

    def test_version_display_contains_version(self):
        got = version_display()
        assert APP_VERSION in got

    def test_version_display_includes_sha_when_present(self):
        """有构建哈希时要拼进去（Docker build 注入 .git_sha）"""
        got = version_display()
        assert APP_VERSION in got, "版本号必须出现在显示串里"
        if BUILD_SHA:
            assert BUILD_SHA in got, "BUILD_SHA 应出现在显示串里"
        else:
            # 本地开发无构建注入：只显示版本号（允许 v 前缀，如 v2.0.0）
            assert got.lstrip("v") == APP_VERSION


class TestResponseWiring:
    """守住「schema 有字段但端点没填」这个已发生过的遗漏"""

    def test_get_system_fills_version(self):
        import inspect
        from backend.routers import system_config

        src = inspect.getsource(system_config.get_system_config)
        assert "version=version_display()" in src, "get_system_config 必须填 version"
        assert "build_sha=BUILD_SHA" in src, "get_system_config 必须填 build_sha"

    def test_update_system_fills_version(self):
        import inspect
        from backend.routers import system_config

        src = inspect.getsource(system_config.update_system_config)
        assert "version=version_display()" in src, "update_system_config 也要填（前端保存后要能刷新徽标）"

    def test_schema_has_fields(self):
        from backend.schemas.system_config import AIConfigOut

        fields = AIConfigOut.model_fields
        assert "version" in fields
        assert "build_sha" in fields
