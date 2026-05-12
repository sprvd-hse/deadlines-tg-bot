from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src import texts
from src.handlers.menu import open_home

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await message.answer(texts.START)
    await open_home(message, state)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(texts.NOTHING_TO_CANCEL)
        return
    await state.clear()
    await message.answer(texts.CANCELLED)


async def _resolve_group(session, message: Message):
    from sqlalchemy import select

    from src.db.models import Group
    from src.services.groups import get_active_group
    from src.services.users import get_or_create_user

    u = message.from_user
    if u is None:
        return None
    if message.chat.type == "private":
        async with session.begin():
            user = await get_or_create_user(
                session, u.id, u.username, u.full_name or u.username or "user"
            )
            return await get_active_group(session, user)
    return (
        await session.execute(
            select(Group).where(Group.chat_id == message.chat.id)
        )
    ).scalar_one_or_none()


async def _no_group_reply(message: Message) -> None:
    if message.chat.type == "private":
        await message.answer(texts.NO_ACTIVE_GROUP)
    else:
        await message.answer(
            "Этот чат не привязан к группе. Сначала /bind_chat в этом чате."
        )


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    from src.db.session import SessionLocal
    from src.services.digest import build_digest_messages

    if message.from_user is None:
        return
    async with SessionLocal() as session:
        group = await _resolve_group(session, message)
        if group is None:
            await _no_group_reply(message)
            return
        messages = await build_digest_messages(session, group)

    if not messages:
        await message.answer(texts.DIGEST_EMPTY)
        return
    for chunk in messages:
        await message.answer(chunk)


@router.message(Command("all"))
async def cmd_all(message: Message) -> None:
    from src.db.session import SessionLocal
    from src.services.digest import build_digest_messages

    if message.from_user is None:
        return
    async with SessionLocal() as session:
        group = await _resolve_group(session, message)
        if group is None:
            await _no_group_reply(message)
            return
        messages = await build_digest_messages(
            session, group, horizon_days=None, include_passed=True
        )

    if not messages:
        await message.answer("В группе пока нет заданий.")
        return
    for chunk in messages:
        await message.answer(chunk)
