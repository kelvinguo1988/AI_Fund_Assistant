"""AI Skill 功能回归测试 — CRUD/导入/占位符渲染/提示词注入/factory model_id"""

import sys, os, asyncio
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.ai_skill_service import (
    build_skills_prompt,
    extract_placeholders,
)
from backend.models.ai_skill import AISkill


# ── 占位符提取与拼接（纯函数）────────────────────────────────────────

class TestPlaceholders:
    def test_extract_all_types(self):
        prompt = "分析 {{ fund_pool }} 和 {{market_regime}}，单基金 {{fund: 12}}"
        tokens = extract_placeholders(prompt)
        assert tokens == ["fund_pool", "market_regime", "fund: 12"]

    def test_extract_none(self):
        assert extract_placeholders("没有占位符的提示词") == []
        assert extract_placeholders("") == []

    def test_build_skills_prompt(self):
        skills = [
            AISkill(id=1, name="A", system_prompt="分析框架A"),
            AISkill(id=2, name="B", system_prompt="分析框架B"),
        ]
        text = build_skills_prompt(skills)
        assert "## Skill: A" in text and "分析框架A" in text
        assert "## Skill: B" in text
        assert text.index("Skill: A") < text.index("Skill: B"), "按 id 序"

    def test_build_empty(self):
        assert build_skills_prompt([]) == ""


# ── factory model_id 覆盖 ─────────────────────────────────────────────

class TestFactoryModelId:
    def test_glm_default(self):
        from backend.llm.factory import LLMFactory
        p = LLMFactory.create("glm", "k", "https://x")
        assert p.model_name == "glm-4-flash"

    def test_glm_override(self):
        from backend.llm.factory import LLMFactory
        p = LLMFactory.create("glm", "k", "https://x", model_id="glm-4-plus")
        assert p.model_name == "glm-4-plus"

    def test_tongyi_override(self):
        from backend.llm.factory import LLMFactory
        p = LLMFactory.create("tongyi", "k", "https://x", model_id="qwen-max")
        assert p.model_name == "qwen-max"

    def test_deepseek_override(self):
        from backend.llm.factory import LLMFactory
        p = LLMFactory.create("deepseek", "k", "https://x", model_id="deepseek-reasoner")
        assert p.model_name == "deepseek-reasoner"

    def test_blank_falls_back_to_preset(self):
        from backend.llm.factory import LLMFactory
        p = LLMFactory.create("glm", "k", "https://x", model_id="  ")
        assert p.model_name == "glm-4-flash"


# ── Skill API 端到端（内存库）─────────────────────────────────────────

@pytest_asyncio.fixture
async def db_session():
    """独立内存库会话"""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from backend.database import Base
    import backend.models  # 注册全部表

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_skill_crud_and_import(db_session):
    from backend.routers.ai_skill import create_skill, import_skills, list_skills, delete_skill
    from backend.schemas.ai import AISkillCreate, AISkillImportItem

    # 创建
    r = await create_skill(AISkillCreate(
        name="测试技能", description="d", system_prompt="分析 {{fund_pool}}",
    ), db_session)
    assert r.data.id > 0 and r.data.enabled is True

    # 批量导入：更新已存在 + 新建一条
    res = await import_skills([
        AISkillImportItem(name="测试技能", description="d2", system_prompt="新内容"),
        AISkillImportItem(name="技能B", description="", system_prompt="B"),
    ], db_session)
    assert res.data.created == 1 and res.data.updated == 1

    skills = (await list_skills(db_session)).data
    assert len(skills) == 2
    updated = next(s for s in skills if s.name == "测试技能")
    assert updated.system_prompt == "新内容"

    # 名称重复创建 → HTTPException
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await create_skill(AISkillCreate(name="测试技能", system_prompt="x"), db_session)
    assert exc.value.status_code == 400

    # 删除
    await delete_skill(updated.id, db_session)
    assert len((await list_skills(db_session)).data) == 1


@pytest.mark.asyncio
async def test_enabled_filter_and_render(db_session):
    """禁用的 skill 不注入；占位符渲染进提示词"""
    from backend.services.ai_skill_service import get_enabled_skills, render_skill_prompts
    db_session.add_all([
        AISkill(name="启用", system_prompt="看 {{fund_pool}}", enabled=True),
        AISkill(name="禁用", system_prompt="不应出现", enabled=False),
    ])
    await db_session.commit()

    enabled = await get_enabled_skills(db_session)
    assert [s.name for s in enabled] == ["启用"]

    rendered = await render_skill_prompts(enabled, db_session)
    name, prompt = rendered[0]
    assert name == "启用"
    assert prompt.startswith("看 （"), f"占位符应被渲染为数据或降级文本，实际: {prompt}"
    assert "{{" not in prompt


@pytest.mark.asyncio
async def test_fund_placeholder_unknown_id(db_session):
    from backend.services.ai_skill_service import render_placeholder
    out = await render_placeholder("fund:99999", db_session)
    assert out == "（数据暂不可用）"


@pytest.mark.asyncio
async def test_chat_injects_enabled_skill(db_session):
    """chat 系统提示词包含启用的 skill 内容（mock provider 捕获 system_prompt）"""
    from backend.services.ai_service import AIService
    from backend.schemas.ai import ChatMessage
    from backend.models.system_config import SystemConfig

    db_session.add_all([
        SystemConfig(config_key="ai_enabled", config_value="true"),
        SystemConfig(config_key="ai_api_key", config_value="test-key"),
        SystemConfig(config_key="ai_model", config_value="deepseek"),
    ])
    db_session.add(AISkill(name="测试技能", system_prompt="必须输出三段式结论", enabled=True))
    await db_session.commit()

    captured = {}

    class _FakeProvider:
        async def chat(self, system_prompt, messages, **kw):
            captured["system_prompt"] = system_prompt
            return "ok"

    import backend.llm.factory as factory_mod
    orig = factory_mod.LLMFactory.create
    factory_mod.LLMFactory.create = lambda *a, **k: _FakeProvider()
    try:
        svc = AIService(db_session)
        resp = await svc.chat(ChatMessage(content="分析一下"))
        assert resp.content == "ok"
        assert "必须输出三段式结论" in captured["system_prompt"], "skill 应注入系统提示词"
        assert "【启用的分析技能" in captured["system_prompt"]
    finally:
        factory_mod.LLMFactory.create = orig
