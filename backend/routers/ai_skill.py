"""AI Skill 路由 — CRUD / 批量导入 / 启停"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.ai_skill import AISkill
from backend.schemas.ai import (
    AISkillCreate,
    AISkillImportItem,
    AISkillImportResult,
    AISkillOut,
    AISkillToggle,
    AISkillUpdate,
)
from backend.schemas.common import ApiResponse
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/skills", response_model=ApiResponse[list[AISkillOut]])
async def list_skills(db: AsyncSession = Depends(get_db)):
    """Skill 列表（按 id 序，与注入顺序一致）"""
    skills = (await db.execute(select(AISkill).order_by(AISkill.id))).scalars().all()
    return ApiResponse(data=[
        AISkillOut(
            id=sk.id, name=sk.name, description=sk.description,
            system_prompt=sk.system_prompt, enabled=sk.enabled,
            created_at=str(sk.created_at) if sk.created_at else None,
            updated_at=str(sk.updated_at) if sk.updated_at else None,
        )
        for sk in skills
    ])


@router.post("/skills", response_model=ApiResponse[AISkillOut])
async def create_skill(body: AISkillCreate, db: AsyncSession = Depends(get_db)):
    """新建/导入单个 Skill（name 重复报 400）"""
    skill = AISkill(
        name=body.name.strip(),
        description=body.description,
        system_prompt=body.system_prompt,
        enabled=body.enabled,
    )
    db.add(skill)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Skill 名称已存在: {body.name}")
    await db.refresh(skill)
    return ApiResponse(data=AISkillOut(
        id=skill.id, name=skill.name, description=skill.description,
        system_prompt=skill.system_prompt, enabled=skill.enabled,
        created_at=str(skill.created_at), updated_at=str(skill.updated_at),
    ))


@router.post("/skills/import", response_model=ApiResponse[AISkillImportResult])
async def import_skills(
    body: list[AISkillImportItem], db: AsyncSession = Depends(get_db)
):
    """批量导入 Skill — 按 name upsert（存在则更新内容与启停，不存在则创建）"""
    result = AISkillImportResult()
    for item in body:
        if not item.name or not item.name.strip():
            result.errors.append("name 为空，已跳过")
            continue
        existing = (await db.execute(
            select(AISkill).where(AISkill.name == item.name.strip())
        )).scalars().first()
        try:
            if existing:
                existing.description = item.description
                existing.system_prompt = item.system_prompt
                existing.enabled = item.enabled
                existing.updated_at = __import__("datetime").datetime.now()
                result.updated += 1
            else:
                db.add(AISkill(
                    name=item.name.strip(),
                    description=item.description,
                    system_prompt=item.system_prompt,
                    enabled=item.enabled,
                ))
                result.created += 1
        except Exception as e:
            await db.rollback()
            result.errors.append(f"{item.name}: {e}")
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"导入提交失败: {e}")
    return ApiResponse(data=result)


@router.put("/skills/{skill_id}", response_model=ApiResponse[AISkillOut])
async def update_skill(
    skill_id: int, body: AISkillUpdate, db: AsyncSession = Depends(get_db)
):
    """更新 Skill（部分字段）"""
    skill = (await db.execute(select(AISkill).where(AISkill.id == skill_id))).scalars().first()
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    if body.name is not None and body.name.strip() != skill.name:
        dup = (await db.execute(
            select(AISkill).where(AISkill.name == body.name.strip())
        )).scalars().first()
        if dup:
            raise HTTPException(status_code=400, detail=f"Skill 名称已存在: {body.name}")
        skill.name = body.name.strip()
    if body.description is not None:
        skill.description = body.description
    if body.system_prompt is not None:
        skill.system_prompt = body.system_prompt
    if body.enabled is not None:
        skill.enabled = body.enabled
    await db.commit()
    await db.refresh(skill)
    return ApiResponse(data=AISkillOut(
        id=skill.id, name=skill.name, description=skill.description,
        system_prompt=skill.system_prompt, enabled=skill.enabled,
        created_at=str(skill.created_at), updated_at=str(skill.updated_at),
    ))


@router.patch("/skills/{skill_id}/toggle", response_model=ApiResponse[AISkillOut])
async def toggle_skill(
    skill_id: int, body: AISkillToggle, db: AsyncSession = Depends(get_db)
):
    """启用/禁用 Skill"""
    skill = (await db.execute(select(AISkill).where(AISkill.id == skill_id))).scalars().first()
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    skill.enabled = body.enabled
    await db.commit()
    await db.refresh(skill)
    return ApiResponse(data=AISkillOut(
        id=skill.id, name=skill.name, description=skill.description,
        system_prompt=skill.system_prompt, enabled=skill.enabled,
        created_at=str(skill.created_at), updated_at=str(skill.updated_at),
    ))


@router.delete("/skills/{skill_id}", response_model=ApiResponse[None])
async def delete_skill(skill_id: int, db: AsyncSession = Depends(get_db)):
    """删除 Skill"""
    skill = (await db.execute(select(AISkill).where(AISkill.id == skill_id))).scalars().first()
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    await db.delete(skill)
    await db.commit()
    return ApiResponse()
