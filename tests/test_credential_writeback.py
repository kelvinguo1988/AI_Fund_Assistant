"""凭证回写（2026-10-31）

背景：Trae / Qoder 的 refresh token 每次刷新都轮换，旧值立刻作废。
只在内存更新的话，进程重启就会用回废值 → 「过段时间莫名认证失败」。
本文件守住三条安全约束，它们都是踩过坑才加的。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.llm.credential_store import _WRITABLE, _merge, persist_credential  # noqa: E402


class TestMerge:
    def test_merge_new_field(self):
        got = _merge('{"refresh":"old","uid":"u1"}', {"refresh": "new"})
        assert got is not None
        data = json.loads(got)
        assert data["refresh"] == "new" and data["uid"] == "u1", "不能丢其他字段"

    def test_no_change_returns_none(self):
        """约束 3：内容相同不写库，避免 updated_at 抖动"""
        assert _merge('{"refresh":"same"}', {"refresh": "same"}) is None

    def test_ignores_falsy_updates(self):
        assert _merge('{"refresh":"same"}', {"refresh": ""}) is None
        assert _merge('{"refresh":"same"}', {}) is None

    def test_broken_existing_json_is_recovered(self):
        """已有值不是 JSON 时也要能写入，而不是崩"""
        got = _merge("not-json", {"refresh": "new"})
        assert got is not None and json.loads(got)["refresh"] == "new"

    def test_plain_text_key_is_recovered(self):
        """用户可能只填了裸 token（非 JSON），回写要能补成 JSON"""
        got = _merge("bare-refresh-token", {"refresh": "new"})
        assert got is not None and json.loads(got)["refresh"] == "new"


class TestWriteGuard:
    @pytest.mark.asyncio
    async def test_unknown_realm_refused(self):
        """不在白名单的 provider 不回写（避免误伤）"""
        assert await persist_credential("zen-free", {"refresh": "x"}) is False
        assert await persist_credential("no-such-provider", {"refresh": "x"}) is False

    @pytest.mark.asyncio
    async def test_empty_changes_refused(self):
        assert await persist_credential("qoder-cn", {}) is False
        assert await persist_credential("qoder-cn", {"refresh": ""}) is False

    def test_writable_realms_cover_new_providers(self):
        for r in ("trae-cn", "trae-global", "qoder-cn", "qoder",
                  "workbuddy", "workbuddy-ai"):
            assert r in _WRITABLE, f"{r} 应允许回写"

    @pytest.mark.asyncio
    async def test_skips_when_provider_switched(self, db_session):
        """约束 2：用户切到别家时不能覆盖——这是最危险的一种写入"""
        from backend.models.system_config import SystemConfig

        db_session.add_all([
            SystemConfig(config_key="ai_model", config_value="zen-free"),
            SystemConfig(config_key="ai_api_key", config_value=""),
        ])
        await db_session.commit()

        ok = await persist_credential("qoder-cn", {"refresh": "new-token"})
        assert ok is False, "当前配置不是 qoder-cn 时不该写"

        row = (await db_session.execute(
            __import__("sqlalchemy").select(SystemConfig).where(
                SystemConfig.config_key == "ai_api_key")
        )).scalars().first()
        assert row.config_value in ("", None), "不能污染用户的新配置"


class TestNeverRaises:
    @pytest.mark.asyncio
    async def test_db_failure_is_swallowed(self, monkeypatch):
        """约束 1：回写失败绝不能让聊天请求失败"""
        import backend.llm.credential_store as cs

        def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(cs, "_WRITABLE", {"qoder-cn", "trae-cn"})
        # 让 import 出的 async_session_factory 抛错
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "backend.database":
                raise RuntimeError("db down")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        ok = await cs.persist_credential("qoder-cn", {"refresh": "x"})
        assert ok is False, "应返回 False 而不是抛异常"


class TestProviderWiring:
    def test_trae_persists_rotation(self):
        import inspect
        from backend.llm import trae_provider
        src = inspect.getsource(trae_provider.TraeProvider._ensure_token)
        assert "persist_credential" in src

    def test_qoder_persists_rotation(self):
        import inspect
        from backend.llm import qoder_provider
        src = inspect.getsource(qoder_provider.QoderProvider._refresh_access)
        assert "persist_credential" in src

    def test_workbuddy_persists_rotation(self):
        import inspect
        from backend.llm import workbuddy_provider
        src = inspect.getsource(workbuddy_provider.WorkBuddyProvider._ensure_access)
        assert "persist_credential" in src

    def test_qoder_refreshes_on_401_once(self):
        """401/403 要刷新重试一次，而不是直接失败"""
        import inspect
        from backend.llm import qoder_provider
        src = inspect.getsource(qoder_provider.QoderProvider._stream)
        assert "_refresh_access" in src
        assert src.count("_stream_once") == 2, "只重试一次，不能打转"
