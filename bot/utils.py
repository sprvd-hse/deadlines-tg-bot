import re
import secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

# Crockford base32 без неоднозначных символов: исключены 0, 1, I, L, O, U
INVITE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_INVITE_BLOCK_LEN = 4
_INVITE_BLOCKS = 3


def generate_invite_code() -> str:
    """Генерирует invite-код вида XXXX-XXXX-XXXX (~60 бит энтропии)."""
    chars = "".join(
        secrets.choice(INVITE_ALPHABET) for _ in range(_INVITE_BLOCK_LEN * _INVITE_BLOCKS)
    )
    return "-".join(
        chars[i * _INVITE_BLOCK_LEN : (i + 1) * _INVITE_BLOCK_LEN] for i in range(_INVITE_BLOCKS)
    )


def normalize_invite_code(raw: str) -> str | None:
    """Приводит ввод к каноническому виду XXXX-XXXX-XXXX или возвращает None при невалидном."""
    cleaned = raw.strip().upper().replace("-", "").replace(" ", "")
    expected = _INVITE_BLOCK_LEN * _INVITE_BLOCKS
    if len(cleaned) != expected or any(c not in INVITE_ALPHABET for c in cleaned):
        return None
    return "-".join(
        cleaned[i * _INVITE_BLOCK_LEN : (i + 1) * _INVITE_BLOCK_LEN] for i in range(_INVITE_BLOCKS)
    )


_LINK_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def parse_links(raw: str) -> list[str]:
    """Разбивает текст по пробелам/переводам строк, фильтрует только http(s)-ссылки."""
    parts = re.split(r"\s+", raw.strip())
    return [p for p in parts if p and _LINK_RE.match(p)]


def format_deadline_display(
    deadline_utc: datetime, tz_name: str, now: datetime | None = None
) -> str:
    """Возвращает строку вида '15.12 23:59 (через 14 часов)' / '... (просрочено на 2 дня)'."""
    tz = ZoneInfo(tz_name)
    local = deadline_utc.astimezone(tz)
    now_local = (now or datetime.now(tz=UTC)).astimezone(tz)

    if local.year == now_local.year:
        date_str = f"{local.day:02d}.{local.month:02d} {local.hour:02d}:{local.minute:02d}"
    else:
        date_str = (
            f"{local.day:02d}.{local.month:02d}.{local.year} {local.hour:02d}:{local.minute:02d}"
        )
    return f"{date_str} ({_format_relative(local - now_local)})"


def _format_relative(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total < 0:
        return f"просрочено на {_humanize(-total)}"
    return f"через {_humanize(total)}"


def _humanize(total_seconds: int) -> str:
    days = total_seconds // 86400
    if days >= 1:
        return f"{days} {_plural_ru(days, 'день', 'дня', 'дней')}"
    hours = total_seconds // 3600
    if hours >= 1:
        return f"{hours} {_plural_ru(hours, 'час', 'часа', 'часов')}"
    minutes = max(1, total_seconds // 60)
    return f"{minutes} {_plural_ru(minutes, 'минуту', 'минуты', 'минут')}"


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 14:
        return many
    last = n_abs % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many
