"""Owner-only template editor. Preview is local and never sends a message."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select

from .db import Session
from .message_templates import CATALOG, MessageFormatter, legacy_text
from .models import MessageTemplate
from .schemas import StrictModel
from .security import require_auth

router = APIRouter(prefix="/api/plugins", dependencies=[Depends(require_auth)])


class TemplateInput(StrictModel):
    text: str = Field(min_length=1, max_length=8000)


def definition(plugin, key):
    row = CATALOG.get((plugin, key))
    if not row:
        raise HTTPException(404, "Шаблон не найден")
    return row


@router.get("/{plugin}/templates")
async def listing(plugin: str):
    async with Session() as db:
        rows = (
            await db.scalars(select(MessageTemplate).where(MessageTemplate.plugin == plugin))
        ).all()
        saved = {row.key: row for row in rows}
        return [
            {
                "key": d.key,
                "title": d.title,
                "default": d.default,
                "text": saved[d.key].text
                if d.key in saved
                else await legacy_text(plugin, d.key, db) or d.default,
                "custom": d.key in saved and saved[d.key].text != d.default,
                "variables": d.variables,
                "group": d.group,
                "audience": d.audience,
                "condition": d.condition,
            }
            for d in CATALOG.values()
            if d.plugin == plugin
        ]


@router.put("/{plugin}/templates/{key}")
async def update(plugin: str, key: str, payload: TemplateInput):
    d = definition(plugin, key)
    try:
        MessageFormatter.validate(payload.text, d.variables)
        if plugin == "kosell" and key == "kosell_guard_success" and "{code}" not in payload.text:
            raise ValueError("Шаблон Steam Guard должен содержать {code}")
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    async with Session() as db:
        row = await db.scalar(
            select(MessageTemplate).where(
                MessageTemplate.plugin == plugin, MessageTemplate.key == key
            )
        )
        if row:
            row.text = payload.text
        else:
            db.add(MessageTemplate(plugin=plugin, key=key, text=payload.text))
        await db.commit()
    return {"ok": True}


@router.delete("/{plugin}/templates/{key}")
async def reset(plugin: str, key: str):
    d = definition(plugin, key)
    async with Session() as db:
        row = await db.scalar(
            select(MessageTemplate).where(
                MessageTemplate.plugin == plugin, MessageTemplate.key == key
            )
        )
        if row:
            row.text = d.default
        else:
            db.add(MessageTemplate(plugin=plugin, key=key, text=d.default))
        await db.commit()
    return {"ok": True}
