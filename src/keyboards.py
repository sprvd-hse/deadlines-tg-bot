from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from src.db.models import AssignmentType, Subject

ASSIGNMENT_TYPE_LABELS: dict[AssignmentType, str] = {
    AssignmentType.homework: "Домашнее задание",
    AssignmentType.test: "Тест",
    AssignmentType.control: "Контрольная",
    AssignmentType.exam: "Экзамен",
    AssignmentType.lab: "Лабораторная",
    AssignmentType.project: "Проект",
    AssignmentType.other: "Другое",
}


def _reminder_hint(atype: AssignmentType) -> str:
    # Локальный импорт — чтобы избежать циклической зависимости
    # (reminders импортирует ASSIGNMENT_TYPE_LABELS из этого модуля).
    from src.services.reminders import OFFSETS_BY_TYPE

    offsets = OFFSETS_BY_TYPE.get(atype, [])
    if not offsets:
        return ""
    days = "/".join(str(int(off.days)) for _rt, off in offsets)
    return f"напомню за {days} дн."


def assignment_type_kb(prefix: str = "assign_type", keep_label: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if keep_label is not None:
        rows.append([InlineKeyboardButton(text=f"⤴ Оставить ({keep_label})", callback_data=f"{prefix}:keep")])
    rows.extend(
        [
            InlineKeyboardButton(
                text=f"{label} · {_reminder_hint(atype)}",
                callback_data=f"{prefix}:{atype.value}",
            )
        ]
        for atype, label in ASSIGNMENT_TYPE_LABELS.items()
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subjects_kb(
    subjects: list[Subject], prefix: str = "assign_subj", keep_label: str | None = None
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if keep_label is not None:
        rows.append([InlineKeyboardButton(text=f"⤴ Оставить ({keep_label})", callback_data=f"{prefix}:keep")])
    rows.extend(
        [InlineKeyboardButton(text=s.name, callback_data=f"{prefix}:{s.id}")] for s in subjects
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb(prefix: str = "assign_confirm", yes_label: str = "✅ Создать") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=yes_label, callback_data=f"{prefix}:yes"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}:no"),
            ]
        ]
    )


def members_kb(members: list[tuple[int, str]], prefix: str) -> InlineKeyboardMarkup:
    """`members`: список (user_id, label)."""
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{prefix}:{uid}")]
        for uid, label in members
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def yes_no_kb(prefix: str, yes_label: str = "Да", no_label: str = "Нет") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=yes_label, callback_data=f"{prefix}:yes"),
                InlineKeyboardButton(text=no_label, callback_data=f"{prefix}:no"),
            ]
        ]
    )


# ============ Главное меню (inline-навигация) ============


# Reply-keyboard под полем ввода уже содержит навигацию,
# поэтому отдельные «Назад/Меню» в inline убраны.


def home_kb(*, has_active_group: bool, is_creator: bool) -> InlineKeyboardMarkup:
    if not has_active_group:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать группу", callback_data="m:grp:new")],
                [InlineKeyboardButton(text="🔑 Присоединиться", callback_data="m:grp:join")],
                [InlineKeyboardButton(text="❓ Помощь", callback_data="m:help")],
            ]
        )
    rows = [
        [InlineKeyboardButton(text="📋 Задания", callback_data="m:tasks")],
        [InlineKeyboardButton(text="➕ Добавить задание", callback_data="m:add")],
        [InlineKeyboardButton(text="📚 Предметы", callback_data="m:subj")],
        [InlineKeyboardButton(text="👥 Группа", callback_data="m:grp")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="m:set")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="m:help")],
    ]
    if is_creator:
        rows.insert(4, [InlineKeyboardButton(text="🛠 Админ группы", callback_data="m:adm")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_home_kb() -> InlineKeyboardMarkup | None:
    return None


def with_back_home(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup | None:
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


# ============ Reply-keyboard (постоянная под полем ввода) ============

REPLY_BTN_TASKS = "📋 Задания"
REPLY_BTN_ADD = "➕ Добавить"
REPLY_BTN_SUBJ = "📚 Предметы"
REPLY_BTN_GRP = "👥 Группа"
REPLY_BTN_SET = "⚙️ Настройки"
REPLY_BTN_HELP = "❓ Помощь"

REPLY_BUTTONS: tuple[str, ...] = (
    REPLY_BTN_TASKS,
    REPLY_BTN_ADD,
    REPLY_BTN_SUBJ,
    REPLY_BTN_GRP,
    REPLY_BTN_SET,
    REPLY_BTN_HELP,
)


def home_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=REPLY_BTN_TASKS), KeyboardButton(text=REPLY_BTN_ADD)],
            [KeyboardButton(text=REPLY_BTN_SUBJ), KeyboardButton(text=REPLY_BTN_GRP)],
            [KeyboardButton(text=REPLY_BTN_SET), KeyboardButton(text=REPLY_BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def remove_reply_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def skip_kb(prefix: str, *, with_clear: bool = False) -> InlineKeyboardMarkup:
    """Кнопка «⏭ Пропустить» (и опционально «🧹 Очистить») для шагов FSM."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"{prefix}:skip")]
    ]
    if with_clear:
        rows.append(
            [InlineKeyboardButton(text="🧹 Очистить", callback_data=f"{prefix}:clear")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
