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
    from src.db.models import Group
    from src.db.session import SessionLocal
    from src.services.digest import build_digest_messages
    from src.services.groups import get_active_group
    from src.services.users import get_or_create_user

    if message.from_user is None:
        return
    u = message.from_user
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        # Перечитываем группу через session.get для удержания контекста.
        group_id = group.id

    async with SessionLocal() as session:
        g = await session.get(Group, group_id)
        if g is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        messages = await build_digest_messages(session, g)

    if not messages:
        await message.answer(texts.DIGEST_EMPTY)
        return
    for chunk in messages:
        await message.answer(chunk)
