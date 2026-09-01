import logging
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.session import SessionLocal
from src.services.digest import DEFAULT_HORIZON_DAYS, DigestService
from src.services.groups import GroupManager
from src.views import texts as t

logger = logging.getLogger(__name__)

router = Router(name="chat")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.message(Command("bind_chat"))
async def bind_chat(message: Message) -> None:
    """Привязывает групповой чат к группе создателя."""
    sender = message.from_user
    if sender is None:
        return
    async with SessionLocal() as session, session.begin():
        manager = GroupManager(session)
        bound = await manager.group_by_chat(message.chat.id)
        if bound is not None:
            await message.answer(t.CHAT_ALREADY_BOUND.format(group=bound.name), parse_mode="HTML")
            return
        ctx = await manager.context(
            tg_user_id=sender.id,
            tg_username=sender.username,
            full_name=sender.full_name or str(sender.id),
        )
        active = ctx.activated()
        if active is None:
            await message.answer(t.CHAT_NEED_PRIVATE_SETUP)
            return
        if not active.is_creator:
            await message.answer(t.CHAT_CREATOR_ONLY)
            return
        await manager.update_settings(active.group.id, chat_id=message.chat.id)
    await message.answer(t.CHAT_BOUND.format(group=active.group.name), parse_mode="HTML")


@router.message(Command("digest"))
async def chat_digest(message: Message) -> None:
    """Показывает ближайшие дедлайны привязанной группы."""
    now = datetime.now(tz=UTC)
    async with SessionLocal() as session:
        group = await GroupManager(session).group_by_chat(message.chat.id)
        if group is None:
            await message.answer(t.CHAT_NOT_BOUND)
            return
        messages = await DigestService(SessionLocal, None).build(session, group, now=now)
    if not messages:
        await message.answer(t.DIGEST_EMPTY.format(days=DEFAULT_HORIZON_DAYS))
        return
    for text in messages:
        await message.answer(text, parse_mode="HTML")


@router.message(Command("help"))
async def chat_help(message: Message) -> None:
    """Краткая справка в групповом чате."""
    await message.answer(t.HELP, parse_mode="HTML")
