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
    AssignmentType.exam: "Экзамен",
    AssignmentType.lab: "Лабораторная",
    AssignmentType.project: "Проект",
    AssignmentType.other: "Другое",
}


def assignment_type_kb(prefix: str = "assign_type", keep_label: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if keep_label is not None:
        rows.append([InlineKeyboardButton(text=f"⤴ Оставить ({keep_label})", callback_data=f"{prefix}:keep")])
    rows.extend(
        [InlineKeyboardButton(text=label, callback_data=f"{prefix}:{atype.value}")]
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


_BTN_BACK = InlineKeyboardButton(text="⬅️ Назад", callback_data="m:home")
_BTN_HOME = InlineKeyboardButton(text="🏠 Меню", callback_data="m:home")


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


def back_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_BTN_HOME]])


def with_back_home(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[*rows, [_BTN_BACK, _BTN_HOME]])


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
