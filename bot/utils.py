import secrets

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
