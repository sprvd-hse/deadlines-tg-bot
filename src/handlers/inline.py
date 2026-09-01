import logging
from datetime import UTC, datetime, timedelta

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultUnion,
    InputTextMessageContent,
    LinkPreviewOptions,
)

from src.db.session import SessionLocal
from src.domain.enums import KIND_ICONS, category_label
from src.domain.humanize import format_due_short
from src.services.digest import DEFAULT_HORIZON_DAYS, DigestService
from src.services.groups import GroupManager
from src.services.items import ItemManager
from src.views.common import bold, esc

logger = logging.getLogger(__name__)

router = Router(name="inline")

MAX_RESULTS = 30
CACHE_SECONDS = 30
NO_GROUP_TITLE = "Нет активной группы"
NO_GROUP_TEXT = "Открой бота и создай группу или войди по ссылке-приглашению."
EMPTY_TITLE = "Ничего не нашлось"
EMPTY_TEXT = f"В ближайшие {DEFAULT_HORIZON_DAYS} дней подходящих дедлайнов нет."
DIGEST_TITLE = "📅 Все ближайшие дедлайны"


@router.inline_query()
async def inline_deadlines(query: InlineQuery) -> None:
    """Подсказки с дедлайнами активной группы для вставки в любой чат."""
    now = datetime.now(tz=UTC)
    async with SessionLocal() as session:
        ctx = await GroupManager(session).context(
            tg_user_id=query.from_user.id,
            tg_username=query.from_user.username,
            full_name=query.from_user.full_name or str(query.from_user.id),
        )
        active = ctx.activated()
        if active is None:
            await _answer(query, [_hint(NO_GROUP_TITLE, NO_GROUP_TEXT)])
            return
        items = await ItemManager(session).upcoming(
            active.group.id, now=now, until=now + timedelta(days=DEFAULT_HORIZON_DAYS)
        )
        subject_names = await ItemManager(session).subject_names(active.group.id)
        digest = await DigestService(SessionLocal, None).build(session, active.group, now=now)

    needle = query.query.strip().casefold()
    if needle:
        items = [
            i
            for i in items
            if needle in i.title.casefold()
            or needle in subject_names.get(i.subject_id, "").casefold()
        ]
    if not items:
        await _answer(query, [_hint(EMPTY_TITLE, EMPTY_TEXT)])
        return

    results: list[InlineQueryResultUnion] = []
    if digest and not needle:
        results.append(
            InlineQueryResultArticle(
                id="digest",
                title=DIGEST_TITLE,
                description=f"Одним сообщением, на {DEFAULT_HORIZON_DAYS} дней",
                input_message_content=_content(digest[0]),
            )
        )
    for item in items[:MAX_RESULTS]:
        subject = subject_names.get(item.subject_id, "?")
        due = format_due_short(item.due_at, active.group.tz, now)
        results.append(
            InlineQueryResultArticle(
                id=str(item.id),
                title=f"{KIND_ICONS[item.kind]} {item.title}",
                description=f"{subject} · {due}",
                input_message_content=_content(
                    f"{KIND_ICONS[item.kind]} {bold(item.title)}\n"
                    f"{esc(category_label(item.kind, item.category))} · {esc(subject)}\n"
                    f"🕒 {esc(due)}"
                ),
            )
        )
    await _answer(query, results)


def _content(text: str) -> InputTextMessageContent:
    """Сообщение, которое вставится в чат."""
    return InputTextMessageContent(
        message_text=text,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


def _hint(title: str, text: str) -> InlineQueryResultArticle:
    """Единственная подсказка, когда показывать нечего."""
    return InlineQueryResultArticle(
        id="hint",
        title=title,
        description=text,
        input_message_content=_content(esc(text)),
    )


async def _answer(query: InlineQuery, results: list[InlineQueryResultUnion]) -> None:
    """Личный ответ на inline-запрос с коротким кэшем."""
    await query.answer(results, cache_time=CACHE_SECONDS, is_personal=True)
