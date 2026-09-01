from collections.abc import Iterable
from html import escape

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
