from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.models import AssignmentType, Subject

ASSIGNMENT_TYPE_LABELS: dict[AssignmentType, str] = {
    AssignmentType.homework: "Домашнее задание",
    AssignmentType.test: "Тест",
    AssignmentType.exam: "Экзамен",
    AssignmentType.lab: "Лабораторная",
    AssignmentType.project: "Проект",
    AssignmentType.other: "Другое",
}


def assignment_type_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"assign_type:{atype.value}")]
        for atype, label in ASSIGNMENT_TYPE_LABELS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subjects_kb(subjects: list[Subject]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=s.name, callback_data=f"assign_subj:{s.id}")] for s in subjects
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✓ Создать", callback_data="assign_confirm:yes"),
                InlineKeyboardButton(text="✗ Отмена", callback_data="assign_confirm:no"),
            ]
        ]
    )
