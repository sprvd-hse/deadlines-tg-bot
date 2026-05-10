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


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    from sqlalchemy import select

    from src.db.models import Group
    from src.db.session import SessionLocal
    from src.services.digest import build_digest_messages
    from src.services.groups import get_active_group
    from src.services.users import get_or_create_user

    if message.from_user is None:
        return
    u = message.from_user

    async with SessionLocal() as session:
        group: Group | None
        if message.chat.type == "private":
            # В личке — активная группа пользователя.
            async with session.begin():
                user = await get_or_create_user(
                    session, u.id, u.username, u.full_name or u.username or "user"
                )
                group = await get_active_group(session, user)
        else:
            # В групповом чате — только если этот чат привязан к какой-то группе.
            group = (
                await session.execute(
                    select(Group).where(Group.chat_id == message.chat.id)
                )
            ).scalar_one_or_none()

        if group is None:
            if message.chat.type == "private":
                await message.answer(texts.NO_ACTIVE_GROUP)
            else:
                await message.answer(
                    "Этот чат не привязан к группе. Сначала /bind_chat в этом чате."
                )
            return
        messages = await build_digest_messages(session, group)

    if not messages:
        await message.answer(texts.DIGEST_EMPTY)
        return
    for chunk in messages:
        await message.answer(chunk)
