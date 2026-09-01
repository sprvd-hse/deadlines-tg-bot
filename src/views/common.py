from collections.abc import Iterable
from datetime import datetime
from html import escape

from src.domain.humanize import ensure_utc

TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 3900


def esc(text: str) -> str:
    """Экранирует текст для parse_mode=HTML."""
    return escape(text, quote=False)


def bold(text: str) -> str:
    """Жирный текст (вход экранируется)."""
    return f"<b>{esc(text)}</b>"


def italic(text: str) -> str:
    """Курсив (вход экранируется)."""
    return f"<i>{esc(text)}</i>"


def code(text: str) -> str:
    """Моноширинный текст с click-to-copy (вход экранируется)."""
    return f"<code>{esc(text)}</code>"


# Формат entity date_time: r | w?[dD]?[tT]?
# r — относительное время, w — день недели, d/D — короткая/длинная дата,
# t/T — короткое/длинное время. Клиент показывает момент в поясе читателя,
# а старые клиенты — текст внутри тега как есть.
TIME_SHORT = "dt"
TIME_FULL = "wDt"
TIME_ONLY = "t"
TIME_RELATIVE = "r"


def time_tag(text: str, moment: datetime, fmt: str = TIME_SHORT) -> str:
    """Оборачивает момент в <tg-time>, чтобы читатель видел своё местное время."""
    unix = int(ensure_utc(moment).timestamp())
    return f'<tg-time unix="{unix}" format="{fmt}">{esc(text)}</tg-time>'


def quote(text: str) -> str:
    """Цитата (вход экранируется)."""
    return f"<blockquote>{esc(text)}</blockquote>"


def expandable_quote(text: str) -> str:
    """Сворачиваемая цитата (вход экранируется)."""
    return f"<blockquote expandable>{esc(text)}</blockquote>"


def raw_expandable_quote(html: str) -> str:
    """Сворачиваемая цитата вокруг уже готовой разметки."""
    return f"<blockquote expandable>{html}</blockquote>"


def link(url: str, title: str) -> str:
    """Ссылка с подписью."""
    return f'<a href="{escape(url, quote=True)}">{esc(title)}</a>'


def join_blocks(blocks: Iterable[str]) -> str:
    """Склеивает непустые блоки пустой строкой между ними."""
    return "\n\n".join(b for b in blocks if b)


def split_messages(blocks: Iterable[str], *, limit: int = SAFE_LIMIT) -> list[str]:
    """Режет последовательность блоков на сообщения, влезающие в лимит Telegram.

    Блок, который сам по себе длиннее лимита, дополнительно режется по строкам —
    иначе такое сообщение Telegram просто отклонит.
    """
    messages: list[str] = []
    current = ""
    for block in blocks:
        if not block:
            continue
        for piece in _fit(block, limit):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                messages.append(current)
            current = piece
    if current:
        messages.append(current)
    return messages


def _fit(block: str, limit: int) -> list[str]:
    """Разбивает слишком длинный блок по строкам."""
    if len(block) <= limit:
        return [block]
    pieces: list[str] = []
    current = ""
    for line in block.split("\n"):
        line = line[:limit]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        pieces.append(current)
        current = line
    if current:
        pieces.append(current)
    return pieces
