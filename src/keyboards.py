from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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


def confirm_kb(prefix: str = "assign_confirm", yes_label: str = "✓ Создать") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=yes_label, callback_data=f"{prefix}:yes"),
                InlineKeyboardButton(text="✗ Отмена", callback_data=f"{prefix}:no"),
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
