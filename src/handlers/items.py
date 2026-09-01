import logging
from datetime import UTC, date, datetime, time, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.db.session import SessionLocal
from src.domain.calendar import TimeParseError, combine_to_utc, parse_time, today_in
from src.domain.enums import ItemKind
from src.domain.models import DESCRIPTION_MAX, TITLE_MAX, Group, Item, ItemDraft, ItemPatch
from src.domain.reminders import resolve_offsets
from src.handlers.ui import Event, notify, show, with_group
from src.scheduler import cancel_item, schedule_item
from src.services.digest import DEFAULT_HORIZON_DAYS, DigestService
from src.services.groups import ActiveContext, GroupManager
from src.services.items import ItemManager
from src.states import AddItem, EditItem
from src.ui import keyboards as kb
from src.ui.callbacks import AddStep, CalendarNav, EditStep, ItemAction, Nav, OffsetToggle, TimePick
from src.utils import parse_links
from src.views import items as item_views
from src.views import texts as t

logger = logging.getLogger(__name__)

router = Router(name="items")
router.message.filter(F.chat.type == "private")

_EDITABLE_FIELDS = (
    ("title", "Название"),
    ("description", "Описание"),
    ("deadline", "Дедлайн"),
    ("links", "Ссылки"),
    ("category", "Категория"),
    ("subject", "Предмет"),
)


# ============ Список, карточка, дайджест ============


@router.callback_query(Nav.filter(F.to == "tasks"))
async def open_tasks(event: Event, state: FSMContext, *args, **kwargs) -> None:
    """Экран активных задач и важных дат."""
    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.answer()
    await render_tasks(event)


async def render_tasks(event: Event) -> None:
    """Рисует список записей активной группы."""
    ctx = await with_group(event)
    if ctx is None:
        return
    now = datetime.now(tz=UTC)
    async with SessionLocal() as session:
        board = await ItemManager(session).board(ctx.group, now=now)
    tasks = item_views.group_by_subject_order(board.tasks, board.subject_names)
    events = item_views.group_by_subject_order(board.events, board.subject_names)
    text = item_views.render_list(
        tasks=tasks, events=events, subject_names=board.subject_names, group=ctx.group, now=now
    )
    numbers = item_views.item_number_map(tasks, events)
    await show(event, text, kb.list_keyboard(tasks=tasks, events=events, numbers=numbers))


@router.callback_query(Nav.filter(F.to == "passed"))
async def open_passed(callback: CallbackQuery) -> None:
    """Экран прошедших записей."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    now = datetime.now(tz=UTC)
    async with SessionLocal() as session:
        manager = ItemManager(session)
        items = await manager.passed(ctx.group.id, now=now)
        subject_names = await manager.subject_names(ctx.group.id)
    await callback.answer()
    await show(
        callback,
        item_views.render_passed(
            items=items, subject_names=subject_names, group=ctx.group, now=now
        ),
        kb.passed_keyboard(items),
    )


@router.callback_query(Nav.filter(F.to == "digest"))
async def open_digest(callback: CallbackQuery) -> None:
    """Дайджест ближайших дедлайнов по запросу."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    await callback.answer()
    await send_digest(callback, ctx.group)


async def send_digest(event: Event, group: Group) -> None:
    """Отправляет дайджест на 14 дней в текущий чат."""
    now = datetime.now(tz=UTC)
    async with SessionLocal() as session:
        messages = await DigestService(SessionLocal, None).build(session, group, now=now)
    if not messages:
        await show(event, t.DIGEST_EMPTY.format(days=DEFAULT_HORIZON_DAYS))
        return
    target = event if isinstance(event, Message) else event.message
    if not isinstance(target, Message):
        return
    for text in messages:
        await target.answer(text, parse_mode="HTML")


@router.callback_query(ItemAction.filter(F.action == "open"))
async def open_card(callback: CallbackQuery, callback_data: ItemAction, state: FSMContext) -> None:
    """Карточка записи."""
    await state.clear()
    await callback.answer()
    await _render_card(callback, callback_data.item_id)


@router.callback_query(ItemAction.filter(F.action == "del"))
async def confirm_delete(callback: CallbackQuery, callback_data: ItemAction) -> None:
    """Спрашивает подтверждение удаления."""
    loaded = await _load_editable(callback, callback_data.item_id)
    if loaded is None:
        return
    item, _ = loaded
    await callback.answer()
    await show(
        callback,
        t.CONFIRM_DELETE_ITEM.format(title=item.title),
        kb.confirm_delete_keyboard(item.id),
    )


@router.callback_query(ItemAction.filter(F.action.in_({"delyes", "close"})))
async def do_delete(callback: CallbackQuery, callback_data: ItemAction) -> None:
    """Удаляет запись или закрывает задачу без дедлайна."""
    loaded = await _load_editable(callback, callback_data.item_id)
    if loaded is None:
        return
    item, _ = loaded
    async with SessionLocal() as session, session.begin():
        await ItemManager(session).delete(item.id)
    cancel_item(item.id)
    await callback.answer(t.ITEM_CLOSED if callback_data.action == "close" else t.ITEM_DELETED)
    await render_tasks(callback)


# ============ Напоминания записи ============


@router.callback_query(ItemAction.filter(F.action == "rem"))
async def open_item_reminders(callback: CallbackQuery, callback_data: ItemAction) -> None:
    """Экран напоминаний записи."""
    loaded = await _load_item(callback, callback_data.item_id)
    if loaded is None:
        return
    item, ctx = loaded
    await callback.answer()
    await _render_item_reminders(callback, item, ctx.group)


@router.callback_query(OffsetToggle.filter((F.scope == "item") & (F.action == "edit")))
async def edit_item_offsets(
    callback: CallbackQuery, callback_data: OffsetToggle, state: FSMContext
) -> None:
    """Открывает редактор сроков напоминаний записи."""
    loaded = await _load_editable(callback, callback_data.target_id)
    if loaded is None:
        return
    item, ctx = loaded
    selected = set(
        resolve_offsets(
            kind=item.kind,
            item_offsets=item.reminder_offsets,
            muted=False,
            settings=ctx.group.reminders,
        )
    )
    await state.update_data(offsets=sorted(selected))
    await callback.answer()
    await show(
        callback,
        t.REMINDERS_ITEM_EDIT,
        kb.offsets_keyboard(scope="item", target_id=item.id, selected=selected, allow_reset=True),
    )


@router.callback_query(OffsetToggle.filter((F.scope == "item") & (F.action == "toggle")))
async def toggle_item_offset(
    callback: CallbackQuery, callback_data: OffsetToggle, state: FSMContext
) -> None:
    """Переключает один срок напоминания записи."""
    selected = set((await state.get_data()).get("offsets", []))
    selected ^= {callback_data.minutes}
    await state.update_data(offsets=sorted(selected))
    await callback.answer()
    await show(
        callback,
        t.REMINDERS_ITEM_EDIT,
        kb.offsets_keyboard(
            scope="item", target_id=callback_data.target_id, selected=selected, allow_reset=True
        ),
    )


@router.callback_query(
    OffsetToggle.filter((F.scope == "item") & (F.action.in_({"done", "reset", "mute", "unmute"})))
)
async def save_item_offsets(
    callback: CallbackQuery, callback_data: OffsetToggle, state: FSMContext
) -> None:
    """Сохраняет настройки напоминаний записи."""
    loaded = await _load_editable(callback, callback_data.target_id)
    if loaded is None:
        return
    item, ctx = loaded
    patch = _offset_patch(callback_data.action, (await state.get_data()).get("offsets", []))
    async with SessionLocal() as session, session.begin():
        updated = await ItemManager(session).update(item.id, patch)
    await state.update_data(offsets=None)
    if updated is not None:
        schedule_item(updated, ctx.group)
    await callback.answer(t.REMINDERS_SAVED)
    await _render_item_reminders(callback, updated or item, ctx.group)


# ============ Добавление ============


@router.callback_query(Nav.filter(F.to == "add"))
async def start_add(event: Event, state: FSMContext, *args, **kwargs) -> None:
    """Запускает мастер добавления записи."""
    await state.clear()
    ctx = await with_group(event)
    if ctx is None:
        return
    async with SessionLocal() as session:
        subjects = await ItemManager(session).subjects(ctx.group.id)
    if not subjects:
        await show(event, t.NEED_SUBJECTS)
        return
    if isinstance(event, CallbackQuery):
        await event.answer()
    await state.set_state(AddItem.kind)
    await state.update_data(group_id=ctx.group.id)
    await show(event, t.ADD_KIND, kb.kind_keyboard())


@router.callback_query(AddStep.filter(F.step == "cancel"))
async def cancel_add(callback: CallbackQuery, state: FSMContext) -> None:
    """Отменяет мастер."""
    await state.clear()
    await callback.answer(t.CANCELLED)
    await render_tasks(callback)


@router.callback_query(AddItem.kind, AddStep.filter(F.step == "kind"))
async def pick_kind(callback: CallbackQuery, callback_data: AddStep, state: FSMContext) -> None:
    """Сохраняет тип записи и просит выбрать предмет."""
    await state.update_data(kind=callback_data.value)
    ctx = await with_group(callback)
    if ctx is None:
        return
    async with SessionLocal() as session:
        subjects = await ItemManager(session).subjects(ctx.group.id)
    await state.set_state(AddItem.subject)
    await callback.answer()
    await show(callback, t.ADD_SUBJECT, kb.subjects_keyboard(subjects))


@router.callback_query(AddItem.subject, AddStep.filter(F.step == "newsubj"))
async def ask_new_subject(callback: CallbackQuery, state: FSMContext) -> None:
    """Просит название нового предмета прямо из мастера."""
    await state.set_state(AddItem.new_subject)
    await callback.answer()
    await show(callback, t.SUBJECT_ASK_NAME)


@router.message(AddItem.new_subject, F.text)
async def save_new_subject(message: Message, state: FSMContext) -> None:
    """Создаёт предмет и продолжает мастер."""
    ctx = await with_group(message)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        subject, _ = await GroupManager(session).add_subject(
            group_id=ctx.group.id, name=message.text or ""
        )
    if subject is None:
        await message.answer(t.SUBJECT_DUPLICATE)
        return
    await state.update_data(subject_id=subject.id)
    await _ask_category(message, state)


@router.callback_query(AddItem.subject, AddStep.filter(F.step == "subject"))
async def pick_subject(callback: CallbackQuery, callback_data: AddStep, state: FSMContext) -> None:
    """Сохраняет предмет и просит категорию."""
    await state.update_data(subject_id=int(callback_data.value))
    await callback.answer()
    await _ask_category(callback, state)


@router.callback_query(AddItem.category, AddStep.filter(F.step == "category"))
async def pick_category(callback: CallbackQuery, callback_data: AddStep, state: FSMContext) -> None:
    """Сохраняет категорию и просит название."""
    await state.update_data(category=callback_data.value)
    await state.set_state(AddItem.title)
    await callback.answer()
    await show(callback, t.ADD_TITLE)


@router.message(AddItem.title, F.text)
async def input_title(message: Message, state: FSMContext) -> None:
    """Принимает название записи."""
    title = (message.text or "").strip()
    if len(title) > TITLE_MAX:
        await message.answer(t.ADD_TITLE_TOO_LONG)
        return
    await state.update_data(title=title)
    await state.set_state(AddItem.description)
    await show(message, t.ADD_DESCRIPTION, kb.skip_keyboard())


@router.message(AddItem.description, F.text)
async def input_description(message: Message, state: FSMContext) -> None:
    """Принимает описание записи."""
    description = (message.text or "").strip()
    if len(description) > DESCRIPTION_MAX:
        await message.answer(t.ADD_DESCRIPTION_TOO_LONG)
        return
    await state.update_data(description=description)
    await _ask_deadline(message, state)


@router.callback_query(AddItem.description, AddStep.filter(F.step == "skip"))
async def skip_description(callback: CallbackQuery, state: FSMContext) -> None:
    """Пропускает описание."""
    await callback.answer()
    await _ask_deadline(callback, state)


@router.message(AddItem.links, F.text)
async def input_links(message: Message, state: FSMContext) -> None:
    """Принимает ссылки."""
    links = parse_links(message.text or "")
    if not links:
        await message.answer(t.ADD_LINKS_INVALID)
        return
    await state.update_data(links=links)
    await _ask_confirm(message, state)


@router.callback_query(AddItem.links, AddStep.filter(F.step == "skip"))
async def skip_links(callback: CallbackQuery, state: FSMContext) -> None:
    """Пропускает ссылки."""
    await callback.answer()
    await _ask_confirm(callback, state)


@router.callback_query(AddItem.confirm, AddStep.filter(F.step == "reminders"))
async def draft_reminders(callback: CallbackQuery, state: FSMContext) -> None:
    """Открывает настройку напоминаний для черновика."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    data = await state.get_data()
    kind = ItemKind(data["kind"])
    selected = set(
        resolve_offsets(
            kind=kind,
            item_offsets=data.get("reminder_offsets"),
            muted=False,
            settings=ctx.group.reminders,
        )
    )
    await state.update_data(offsets=sorted(selected))
    await state.set_state(AddItem.reminders)
    await callback.answer()
    await show(
        callback,
        t.REMINDERS_ITEM_EDIT,
        kb.offsets_keyboard(scope="draft", target_id=0, selected=selected, allow_reset=True),
    )


@router.callback_query(AddItem.reminders, OffsetToggle.filter(F.action == "toggle"))
async def toggle_draft_offset(
    callback: CallbackQuery, callback_data: OffsetToggle, state: FSMContext
) -> None:
    """Переключает срок напоминания черновика."""
    selected = set((await state.get_data()).get("offsets", []))
    selected ^= {callback_data.minutes}
    await state.update_data(offsets=sorted(selected))
    await callback.answer()
    await show(
        callback,
        t.REMINDERS_ITEM_EDIT,
        kb.offsets_keyboard(scope="draft", target_id=0, selected=selected, allow_reset=True),
    )


@router.callback_query(AddItem.reminders, OffsetToggle.filter(F.action.in_({"done", "reset"})))
async def save_draft_offsets(
    callback: CallbackQuery, callback_data: OffsetToggle, state: FSMContext
) -> None:
    """Сохраняет напоминания черновика и возвращает к подтверждению."""
    data = await state.get_data()
    offsets = None if callback_data.action == "reset" else sorted(data.get("offsets", []))
    await state.update_data(reminder_offsets=offsets, offsets=None)
    await callback.answer(t.REMINDERS_SAVED)
    await _ask_confirm(callback, state)


@router.callback_query(AddItem.confirm, AddStep.filter(F.step == "confirm"))
async def create_item(callback: CallbackQuery, state: FSMContext) -> None:
    """Создаёт запись из черновика."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    draft = _draft_from(await state.get_data())
    async with SessionLocal() as session, session.begin():
        item = await ItemManager(session).create(
            group_id=ctx.group.id, author_id=ctx.user.id, draft=draft
        )
    schedule_item(item, ctx.group)
    await state.clear()
    await callback.answer(t.ADD_CREATED)
    await _render_card(callback, item.id)


# ============ Редактирование ============


@router.callback_query(ItemAction.filter(F.action == "edit"))
async def start_edit(callback: CallbackQuery, callback_data: ItemAction, state: FSMContext) -> None:
    """Показывает выбор поля для изменения."""
    loaded = await _load_editable(callback, callback_data.item_id)
    if loaded is None:
        return
    item, _ = loaded
    await state.clear()
    await state.update_data(item_id=item.id, kind=item.kind.value)
    await callback.answer()
    await show(callback, t.EDIT_PICK_FIELD, _edit_fields_keyboard(item))


@router.callback_query(EditStep.filter(F.field == "title"))
async def edit_title(callback: CallbackQuery, callback_data: EditStep, state: FSMContext) -> None:
    """Просит новое название."""
    await state.update_data(item_id=callback_data.item_id)
    await state.set_state(EditItem.title)
    await callback.answer()
    await show(callback, t.ADD_TITLE)


@router.message(EditItem.title, F.text)
async def save_title(message: Message, state: FSMContext) -> None:
    """Сохраняет новое название."""
    title = (message.text or "").strip()
    if len(title) > TITLE_MAX:
        await message.answer(t.ADD_TITLE_TOO_LONG)
        return
    await _apply_patch(message, state, ItemPatch(title=title))


@router.callback_query(EditStep.filter(F.field == "description"))
async def edit_description(
    callback: CallbackQuery, callback_data: EditStep, state: FSMContext
) -> None:
    """Просит новое описание."""
    await state.update_data(item_id=callback_data.item_id)
    await state.set_state(EditItem.description)
    await callback.answer()
    await show(callback, t.ADD_DESCRIPTION, kb.skip_keyboard())


@router.message(EditItem.description, F.text)
async def save_description(message: Message, state: FSMContext) -> None:
    """Сохраняет описание."""
    description = (message.text or "").strip()
    if len(description) > DESCRIPTION_MAX:
        await message.answer(t.ADD_DESCRIPTION_TOO_LONG)
        return
    await _apply_patch(message, state, ItemPatch(description=description))


@router.callback_query(EditItem.description, AddStep.filter(F.step == "skip"))
async def clear_description(callback: CallbackQuery, state: FSMContext) -> None:
    """Стирает описание."""
    await callback.answer()
    await _apply_patch(callback, state, ItemPatch(clear_description=True))


@router.callback_query(EditStep.filter(F.field == "links"))
async def edit_links(callback: CallbackQuery, callback_data: EditStep, state: FSMContext) -> None:
    """Просит новые ссылки."""
    await state.update_data(item_id=callback_data.item_id)
    await state.set_state(EditItem.links)
    await callback.answer()
    await show(callback, t.ADD_LINKS, kb.skip_keyboard())


@router.message(EditItem.links, F.text)
async def save_links(message: Message, state: FSMContext) -> None:
    """Сохраняет ссылки."""
    links = parse_links(message.text or "")
    if not links:
        await message.answer(t.ADD_LINKS_INVALID)
        return
    await _apply_patch(message, state, ItemPatch(links=links))


@router.callback_query(EditItem.links, AddStep.filter(F.step == "skip"))
async def clear_links(callback: CallbackQuery, state: FSMContext) -> None:
    """Стирает ссылки."""
    await callback.answer()
    await _apply_patch(callback, state, ItemPatch(links=[]))


@router.callback_query(EditStep.filter(F.field == "category"))
async def edit_category(
    callback: CallbackQuery, callback_data: EditStep, state: FSMContext
) -> None:
    """Показывает категории для смены."""
    loaded = await _load_editable(callback, callback_data.item_id)
    if loaded is None:
        return
    item, _ = loaded
    await state.set_state(EditItem.field)
    await state.update_data(item_id=item.id)
    await callback.answer()
    await show(callback, t.ADD_CATEGORY, kb.categories_keyboard(item.kind))


@router.callback_query(EditItem.field, AddStep.filter(F.step == "category"))
async def save_category(callback: CallbackQuery, callback_data: AddStep, state: FSMContext) -> None:
    """Сохраняет категорию."""
    await callback.answer()
    await _apply_patch(callback, state, ItemPatch(category=callback_data.value))


@router.callback_query(EditStep.filter(F.field == "subject"))
async def edit_subject(callback: CallbackQuery, callback_data: EditStep, state: FSMContext) -> None:
    """Показывает предметы для смены."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    async with SessionLocal() as session:
        subjects = await ItemManager(session).subjects(ctx.group.id)
    await state.set_state(EditItem.field)
    await state.update_data(item_id=callback_data.item_id)
    await callback.answer()
    await show(callback, t.ADD_SUBJECT, kb.subjects_keyboard(subjects))


@router.callback_query(EditItem.field, AddStep.filter(F.step == "subject"))
async def save_subject(callback: CallbackQuery, callback_data: AddStep, state: FSMContext) -> None:
    """Сохраняет предмет."""
    await callback.answer()
    await _apply_patch(callback, state, ItemPatch(subject_id=int(callback_data.value)))


@router.callback_query(EditStep.filter(F.field == "deadline"))
async def edit_deadline(
    callback: CallbackQuery, callback_data: EditStep, state: FSMContext
) -> None:
    """Открывает календарь для смены дедлайна."""
    loaded = await _load_editable(callback, callback_data.item_id)
    if loaded is None:
        return
    item, ctx = loaded
    await state.set_state(EditItem.deadline)
    await state.update_data(item_id=item.id, kind=item.kind.value)
    await callback.answer()
    await _show_calendar(callback, ctx.group, item.kind)


# ============ Календарь (общий для добавления и редактирования) ============


@router.callback_query(CalendarNav.filter(F.action == "nav"))
async def calendar_nav(
    callback: CallbackQuery, callback_data: CalendarNav, state: FSMContext
) -> None:
    """Листает месяцы."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    kind = await _kind_from_state(state)
    await callback.answer()
    await _show_calendar(
        callback, ctx.group, kind, year=callback_data.year, month=callback_data.month
    )


@router.callback_query(CalendarNav.filter(F.action == "back"))
async def calendar_back(callback: CallbackQuery, state: FSMContext) -> None:
    """Возвращает от выбора времени к календарю."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    await callback.answer()
    await _show_calendar(callback, ctx.group, await _kind_from_state(state))


@router.callback_query(CalendarNav.filter(F.action.in_({"pick", "today", "tomorrow", "week"})))
async def calendar_pick(
    callback: CallbackQuery, callback_data: CalendarNav, state: FSMContext
) -> None:
    """Фиксирует дату и просит выбрать время."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    today = today_in(ctx.group.tz)
    shifts = {"today": 0, "tomorrow": 1, "week": 7}
    if callback_data.action == "pick":
        picked = date(callback_data.year, callback_data.month, callback_data.day)
    else:
        picked = today + timedelta(days=shifts[callback_data.action])
    await state.update_data(picked_date=picked.isoformat())
    kind = await _kind_from_state(state)
    await callback.answer()
    await show(
        callback,
        t.ADD_TIME.format(date=picked.strftime("%d.%m.%Y")),
        kb.time_keyboard(kind),
    )


@router.callback_query(CalendarNav.filter(F.action == "open"))
async def calendar_open_deadline(callback: CallbackQuery, state: FSMContext) -> None:
    """Задача без дедлайна — «до конца модуля»."""
    await callback.answer()
    await _store_deadline(callback, state, None)


@router.callback_query(TimePick.filter(F.action == "pick"))
async def time_pick(callback: CallbackQuery, callback_data: TimePick, state: FSMContext) -> None:
    """Собирает дату и время в дедлайн."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    data = await state.get_data()
    picked = date.fromisoformat(data["picked_date"])
    due_at = combine_to_utc(picked, time(callback_data.hour, callback_data.minute), ctx.group.tz)
    await callback.answer()
    await _store_deadline(callback, state, due_at)


@router.callback_query(TimePick.filter(F.action == "custom"))
async def time_custom(callback: CallbackQuery, state: FSMContext) -> None:
    """Просит ввести время текстом."""
    current = await state.get_state()
    await state.set_state(
        EditItem.custom_time if current == EditItem.deadline.state else AddItem.custom_time
    )
    await callback.answer()
    await show(callback, t.ADD_TIME_CUSTOM)


@router.message(AddItem.custom_time, F.text)
@router.message(EditItem.custom_time, F.text)
async def input_custom_time(message: Message, state: FSMContext) -> None:
    """Разбирает время, введённое вручную."""
    ctx = await with_group(message)
    if ctx is None:
        return
    try:
        moment = parse_time(message.text or "")
    except TimeParseError as exc:
        await message.answer(t.ADD_TIME_ERROR.format(error=exc))
        return
    data = await state.get_data()
    picked = date.fromisoformat(data["picked_date"])
    await _store_deadline(message, state, combine_to_utc(picked, moment, ctx.group.tz))


# ============ Вспомогательные ============


async def _store_deadline(event: Event, state: FSMContext, due_at: datetime | None) -> None:
    """Кладёт дедлайн в черновик или сразу применяет к записи."""
    data = await state.get_data()
    if data.get("item_id"):
        patch = ItemPatch(clear_due_at=True) if due_at is None else ItemPatch(due_at=due_at)
        await _apply_patch(event, state, patch)
        return
    await state.update_data(due_at=due_at.isoformat() if due_at else None)
    await state.set_state(AddItem.links)
    await show(event, t.ADD_LINKS, kb.skip_keyboard())


async def _apply_patch(event: Event, state: FSMContext, patch: ItemPatch) -> None:
    """Применяет изменения к записи и возвращает карточку."""
    data = await state.get_data()
    item_id = int(data["item_id"])
    loaded = await _load_editable(event, item_id)
    if loaded is None:
        return
    _, ctx = loaded
    async with SessionLocal() as session, session.begin():
        updated = await ItemManager(session).update(item_id, patch)
    await state.clear()
    if updated is not None:
        schedule_item(updated, ctx.group)
    await notify(event, t.EDIT_SAVED)
    await _render_card(event, item_id)


async def _ask_category(event: Event, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(AddItem.category)
    await show(event, t.ADD_CATEGORY, kb.categories_keyboard(ItemKind(data["kind"])))


async def _ask_deadline(event: Event, state: FSMContext) -> None:
    ctx = await with_group(event)
    if ctx is None:
        return
    await state.set_state(AddItem.deadline)
    await _show_calendar(event, ctx.group, await _kind_from_state(state))


async def _ask_confirm(event: Event, state: FSMContext) -> None:
    ctx = await with_group(event)
    if ctx is None:
        return
    data = await state.get_data()
    draft = _draft_from(data)
    async with SessionLocal() as session:
        subject_names = await ItemManager(session).subject_names(ctx.group.id)
    await state.set_state(AddItem.confirm)
    text = item_views.render_draft_summary(
        draft=draft,
        subject_name=subject_names.get(draft.subject_id, "?"),
        group=ctx.group,
        now=datetime.now(tz=UTC),
    )
    await show(event, f"{t.ADD_CONFIRM}\n\n{text}", kb.draft_confirm_keyboard())


async def _show_calendar(
    event: Event, group: Group, kind: ItemKind, *, year: int = 0, month: int = 0
) -> None:
    today = today_in(group.tz)
    prompt = t.ADD_DEADLINE_TASK if kind is ItemKind.TASK else t.ADD_DEADLINE_EVENT
    await show(
        event,
        prompt,
        kb.calendar_keyboard(
            year=year or today.year,
            month=month or today.month,
            today=today,
            kind=kind,
            allow_open=True,
        ),
    )


async def _render_card(event: Event, item_id: int) -> None:
    loaded = await _load_item(event, item_id)
    if loaded is None:
        return
    item, ctx = loaded
    async with SessionLocal() as session:
        manager = ItemManager(session)
        subject_names = await manager.subject_names(item.group_id)
        can_modify = await manager.can_modify(item=item, user_id=ctx.user.id)
    await show(
        event,
        item_views.render_card(
            item=item,
            subject_name=subject_names.get(item.subject_id, "?"),
            group=ctx.group,
            now=datetime.now(tz=UTC),
        ),
        kb.card_keyboard(item, can_modify=can_modify),
    )


async def _render_item_reminders(event: Event, item: Item, group: Group) -> None:
    await show(
        event,
        t.REMINDERS_ITEM.format(
            title=item.title, summary=item_views.render_reminder_summary(item, group)
        ),
        kb.item_reminders_keyboard(item),
    )


async def _load_item(event: Event, item_id: int) -> tuple[Item, ActiveContext] | None:
    """Загружает запись вместе с контекстом пользователя."""
    ctx = await with_group(event)
    if ctx is None:
        return None
    async with SessionLocal() as session:
        item = await ItemManager(session).get(item_id)
    if item is None or not item.is_active or item.group_id != ctx.group.id:
        await notify(event, t.ITEM_NOT_FOUND, alert=True)
        return None
    return item, ctx


async def _load_editable(event: Event, item_id: int) -> tuple[Item, ActiveContext] | None:
    """Загружает запись и проверяет право её менять."""
    loaded = await _load_item(event, item_id)
    if loaded is None:
        return None
    item, ctx = loaded
    async with SessionLocal() as session:
        allowed = await ItemManager(session).can_modify(item=item, user_id=ctx.user.id)
    if not allowed:
        await notify(event, t.NOT_ALLOWED, alert=True)
        return None
    return item, ctx


async def _kind_from_state(state: FSMContext) -> ItemKind:
    data = await state.get_data()
    return ItemKind(data.get("kind", ItemKind.TASK))


def _draft_from(data: dict) -> ItemDraft:
    """Собирает черновик из данных FSM."""
    due_raw = data.get("due_at")
    return ItemDraft(
        kind=ItemKind(data["kind"]),
        subject_id=int(data["subject_id"]),
        category=data["category"],
        title=data["title"],
        description=data.get("description"),
        links=data.get("links") or [],
        due_at=datetime.fromisoformat(due_raw) if due_raw else None,
        reminder_offsets=data.get("reminder_offsets"),
    )


def _offset_patch(action: str, offsets: list[int]) -> ItemPatch:
    """Переводит действие редактора напоминаний в патч записи."""
    if action == "reset":
        return ItemPatch(reset_reminder_offsets=True, reminders_muted=False)
    if action == "mute":
        return ItemPatch(reminders_muted=True)
    if action == "unmute":
        return ItemPatch(reminders_muted=False)
    return ItemPatch(reminder_offsets=sorted(offsets), reminders_muted=False)


def _edit_fields_keyboard(item: Item) -> InlineKeyboardMarkup:
    """Клавиатура выбора поля для редактирования."""
    builder = InlineKeyboardBuilder()
    for field, label in _EDITABLE_FIELDS:
        builder.button(text=label, callback_data=EditStep(field=field, item_id=item.id).pack())
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text=kb.BACK, callback_data=ItemAction(action="open", item_id=item.id).pack()
        )
    )
    return builder.as_markup()
