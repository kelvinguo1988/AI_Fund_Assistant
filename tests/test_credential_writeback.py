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

    def test_bare_string_stays_bare(self):
        """裸字符串凭证必须保持裸串（WorkBuddy 直接把它塞进请求头）

        2026-11-07 代码审查发现：原实现把裸串当空对象，回写成
        {"refresh": "..."}；WorkBuddy 下次启动会把整个 JSON 当 token 发出去
        → 认证必然失败。这恰好是回写机制本想避免的「重启后莫名失败」。
        """
        got = _merge("bare-refresh-token", {"refresh": "new"})
        assert got == "new", "裸串凭证只替换值，不能包成 JSON"

    def test_bare_string_unchanged_returns_none(self):
        assert _merge("same-token", {"refresh": "same-token"}) is None

    def test_bare_string_ignores_non_refresh_updates(self):
        """裸串只能承载 refresh，其它字段（如 access）无处安放，应忽略"""
        assert _merge("tok", {"access": "a"}) is None

    def test_json_credential_still_merges(self):
        """JSON 凭证（trae/qoder）仍按对象合并"""
        got = _merge('{"refresh":"old","uid":"u1"}', {"refresh": "new"})
        assert got is not None
        assert json.loads(got) == {"refresh": "new", "uid": "u1"}

    def test_empty_existing_starts_json(self):
        got = _merge("", {"refresh": "new"})
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


def _patch_session(monkeypatch, db_session):
    """让 persist_credential 用测试的 db_session（它内部走全局 session factory）"""
    import contextlib

    import backend.llm.credential_store as cs

    @contextlib.asynccontextmanager
    async def _fake_factory():
        yield db_session

    class _FakeDB:
        async_session_factory = staticmethod(_fake_factory)

    monkeypatch.setitem(__import__("sys").modules, "backend.database", _FakeDB)


class TestFallbackWriteback:
    """备用链路的凭证也要回写（2026-11-07 审查发现的遗漏）

    fallback.py 会用同一个 realm 构造 Trae/Qoder Provider。当它们被配成
    fallback（主模型是 deepseek/magpie）时，原实现因 current_model != realm
    直接跳过回写；而 provider 每个请求都从 DB 重建，所以**不必等重启**，
    下一次 fallback 调用就会用已作废的 refresh。
    """

    @pytest.mark.asyncio
    async def test_writes_to_fallback_key(self, db_session, monkeypatch):
        _patch_session(monkeypatch, db_session)
        from backend.models.system_config import SystemConfig

        db_session.add_all([
            SystemConfig(config_key="ai_model", config_value="deepseek"),
            SystemConfig(config_key="ai_fallback_model", config_value="trae-cn"),
            SystemConfig(config_key="ai_fallback_api_key",
                         config_value='{"refresh":"old","uid":"u1"}'),
        ])
        await db_session.commit()

        ok = await persist_credential("trae-cn", {"refresh": "rotated"})
        assert ok is True, "fallback 配置为 trae-cn 时必须回写"

        from sqlalchemy import select
        row = (await db_session.execute(
            select(SystemConfig).where(SystemConfig.config_key == "ai_fallback_api_key")
        )).scalars().first()
        assert json.loads(row.config_value)["refresh"] == "rotated"
        assert json.loads(row.config_value)["uid"] == "u1", "不能丢其它字段"

    @pytest.mark.asyncio
    async def test_main_key_untouched_when_fallback(self, db_session, monkeypatch):
        """写 fallback 时不能顺手改了主链路 key"""
        _patch_session(monkeypatch, db_session)
        from backend.models.system_config import SystemConfig
        from sqlalchemy import select

        db_session.add_all([
            SystemConfig(config_key="ai_model", config_value="deepseek"),
            SystemConfig(config_key="ai_api_key", config_value="main-key"),
            SystemConfig(config_key="ai_fallback_model", config_value="qoder-cn"),
            SystemConfig(config_key="ai_fallback_api_key", config_value='{"access":"a"}'),
        ])
        await db_session.commit()

        await persist_credential("qoder-cn", {"refresh": "r2"})
        row = (await db_session.execute(
            select(SystemConfig).where(SystemConfig.config_key == "ai_api_key")
        )).scalars().first()
        assert row.config_value == "main-key", "主链路 key 不该被 fallback 的轮换影响"


class TestWorkBuddyBareString:
    """WorkBuddy 的 ai_api_key 是裸 refresh token，两种形状都要能吃"""

    def test_bare_token_accepted(self):
        from backend.llm.workbuddy_provider import WorkBuddyProvider

        p = WorkBuddyProvider(api_key="bare-token-123")
        assert p._refresh_token == "bare-token-123"

    def test_json_shape_also_accepted(self):
        """历史数据可能已被写成 JSON（旧 bug 留下的），要能自愈"""
        from backend.llm.workbuddy_provider import WorkBuddyProvider

        p = WorkBuddyProvider(api_key='{"refresh":"tok-from-json"}')
        assert p._refresh_token == "tok-from-json", "不能把整个 JSON 当 token"


class TestTraeRefreshClearsCredAccess:
    """Trae 401 重试必须连凭证里的 access 一起清（2026-11-07 审查）"""

    def test_retry_pops_stored_access(self):
        import inspect
        from backend.llm import trae_provider

        src = inspect.getsource(trae_provider.TraeProvider._stream)
        assert '_cred.pop("access"' in src, (
            "只清 self._token 不够：_ensure_token 会先查 _stored_access()，"
            "那里读的是 _cred['access']，不清就永远走不到 ExchangeToken"
        )


class TestQoderAuthMapping:
    """Qoder 清单接口的 401 也要映射成 _AuthExpired（否则刷新机制失效）"""

    def test_model_list_raises_auth_expired(self):
        import inspect
        from backend.llm import qoder_provider

        src = inspect.getsource(qoder_provider.QoderProvider._ensure_model_entry)
        assert "_AuthExpired" in src, "清单接口 401 必须触发刷新路径"

    def test_retry_failure_has_actionable_message(self):
        """重试后仍失败要给可操作提示，不能裸抛 _AuthExpired（空消息）"""
        import inspect
        from backend.llm import qoder_provider

        src = inspect.getsource(qoder_provider.QoderProvider._stream)
        assert "认证失败" in src
        assert src.count("_AuthExpired") >= 2, "两段都要捕获"


class TestToolIndexFallback:
    """缺 index 时分片不能各开新槽（否则参数被截断）"""

    @pytest.mark.parametrize("mod", ["trae_provider", "qoder_provider"])
    def test_defaults_to_zero(self, mod):
        import inspect
        import importlib

        m = importlib.import_module(f"backend.llm.{mod}")
        cls = getattr(m, "TraeProvider" if mod == "trae_provider" else "QoderProvider")
        src = inspect.getsource(cls.astream_with_tools)
        assert "idx = 0" in src, "缺 index 应归到 0 号槽，而不是新开一个"
        assert "idx = len(tool_acc)" not in src


class TestNoCredentialLeak:
    def test_exchange_error_does_not_dump_response(self):
        """ExchangeToken 响应含 Token/RefreshToken，不能整段进异常"""
        import inspect
        from backend.llm import trae_provider

        src = inspect.getsource(trae_provider.TraeProvider._ensure_token)
        assert "str(data)[:160]" not in src, "会把 Token/RefreshToken 写进日志与外泄"
