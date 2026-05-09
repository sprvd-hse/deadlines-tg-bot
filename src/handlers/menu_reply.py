"""Хендлеры reply-keyboard: ловят текст «📋 Задания», «➕ Добавить» и т.п.,
сбрасывают FSM и открывают соответствующий inline-экран.

Подключается до FSM-роутеров, чтобы нажатия меню всегда отменяли текущий ввод.
"""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src import texts
from src.handlers.menu import render
from src.handlers.menu_add import open_add
from src.handlers.menu_groups import open_group_hub
from src.handlers.menu_settings import open_settings
from src.handlers.menu_subjects import open_subjects
from src.handlers.menu_tasks import open_tasks_page
from src.keyboards import (
    REPLY_BTN_ADD,
    REPLY_BTN_GRP,
    REPLY_BTN_HELP,
    REPLY_BTN_SET,
    REPLY_BTN_SUBJ,
    REPLY_BTN_TASKS,
    back_home_kb,
)

router = Router(name="menu_reply")


async def _reset(state: FSMContext) -> None:
    if await state.get_state() is not None:
        await state.clear()


@router.message(F.text == REPLY_BTN_TASKS)
async def reply_tasks(message: Message, state: FSMContext) -> None:
    await _reset(state)
    await open_tasks_page(message, page=0)


@router.message(F.text == REPLY_BTN_ADD)
async def reply_add(message: Message, state: FSMContext) -> None:
    await _reset(state)
    await open_add(message, state)


@router.message(F.text == REPLY_BTN_SUBJ)
async def reply_subj(message: Message, state: FSMContext) -> None:
    await _reset(state)
    await open_subjects(message)


@router.message(F.text == REPLY_BTN_GRP)
async def reply_grp(message: Message, state: FSMContext) -> None:
    await _reset(state)
    await open_group_hub(message)


@router.message(F.text == REPLY_BTN_SET)
async def reply_set(message: Message, state: FSMContext) -> None:
    await _reset(state)
    await open_settings(message)


@router.message(F.text == REPLY_BTN_HELP)
async def reply_help(message: Message, state: FSMContext) -> None:
    await _reset(state)
    await render(message, texts.HELP, back_home_kb())
