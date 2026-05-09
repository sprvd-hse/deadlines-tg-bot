from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot import texts
from bot.db.session import SessionLocal
from bot.services import subjects as subjects_svc
from bot.services.groups import get_active_group
from bot.services.users import get_or_create_user

router = Router(name="subjects")

_SUBJECT_NAME_MAX = 128


def _user_meta(message: Message) -> tuple[int, str | None, str] | None:
    if message.from_user is None:
        return None
    u = message.from_user
    return u.id, u.username, u.full_name or u.username or "user"


@router.message(Command("subjects"))
async def cmd_subjects(message: Message) -> None:
    meta = _user_meta(message)
    if meta is None:
        return
    tg_id, username, full_name = meta

    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        items = await subjects_svc.list_subjects(session, group.id)
        names = [s.name for s in items]

    if not names:
        await message.answer(texts.SUBJECTS_EMPTY)
        return
    await message.answer(texts.SUBJECTS_HEADER + "\n" + "\n".join(f"• {n}" for n in names))


@router.message(Command("add_subject"))
async def cmd_add_subject(message: Message, command: CommandObject) -> None:
    meta = _user_meta(message)
    if meta is None:
        return
    name = (command.args or "").strip()
    if not name:
        await message.answer(texts.USAGE_ADD_SUBJECT)
        return
    if len(name) > _SUBJECT_NAME_MAX:
        await message.answer(texts.GROUP_NAME_TOO_LONG)
        return

    tg_id, username, full_name = meta
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        created = await subjects_svc.add_subject(session, group.id, name)

    if created is None:
        await message.answer(texts.SUBJECT_ALREADY_EXISTS.format(name=name))
    else:
        await message.answer(texts.SUBJECT_ADDED.format(name=name))


@router.message(Command("delete_subject"))
async def cmd_delete_subject(message: Message, command: CommandObject) -> None:
    meta = _user_meta(message)
    if meta is None:
        return
    name = (command.args or "").strip()
    if not name:
        await message.answer(texts.USAGE_DELETE_SUBJECT)
        return

    tg_id, username, full_name = meta
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        status = await subjects_svc.delete_subject(session, group.id, name)

    if status == "not_found":
        await message.answer(texts.SUBJECT_NOT_FOUND.format(name=name))
    elif status == "has_assignments":
        await message.answer(texts.SUBJECT_HAS_ASSIGNMENTS.format(name=name))
    else:
        await message.answer(texts.SUBJECT_DELETED.format(name=name))
