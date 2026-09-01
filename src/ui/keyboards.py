from datetime import date, time

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.domain.calendar import (
    can_go_back,
    can_go_forward,
    is_selectable,
    month_grid,
    month_title,
    shift_month,
    time_presets_for,
)
from src.domain.enums import (
    KIND_ICONS,
    ItemKind,
    categories_for,
    category_label,
)
from src.domain.humanize import WEEKDAYS_RU
from src.domain.models import Group, Item, Member, Subject
from src.domain.reminders import OFFSET_PRESETS
from src.ui.callbacks import (
    NOOP,
    AddStep,
    AdminAction,
    CalendarNav,
    GroupAction,
    ItemAction,
    MemberAction,
    Nav,
    OffsetToggle,
    SubjectAction,
    TimePick,
)

BTN_TASKS = "📋 Задания"
BTN_ADD = "➕ Добавить"
BTN_SUBJECTS = "📚 Предметы"
BTN_GROUP = "👥 Группа"
BTN_SETTINGS = "⚙️ Настройки"
BTN_HELP = "❓ Помощь"

BACK = "‹ Назад"
CANCEL = "❌ Отмена"
SKIP = "⏭ Пропустить"
DONE = "✅ Готово"


def home_reply() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура под полем ввода."""
    rows = [
        [KeyboardButton(text=BTN_TASKS), KeyboardButton(text=BTN_ADD)],
        [KeyboardButton(text=BTN_SUBJECTS), KeyboardButton(text=BTN_GROUP)],
        [KeyboardButton(text=BTN_SETTINGS), KeyboardButton(text=BTN_HELP)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def _nav(to: str, text: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=Nav(to=to).pack())


def list_keyboard(
    *,
    tasks: list[Item],
    events: list[Item],
    numbers: dict[int, int],
    completed_ids: frozenset[int] = frozenset(),
) -> InlineKeyboardMarkup:
    """По кнопке-переключателю и кнопке открытия карточки на каждую запись."""
    builder = InlineKeyboardBuilder()
    for item in tasks + events:
        done = item.id in completed_ids
        label = f"{numbers[item.id]}. {KIND_ICONS[item.kind]} {item.title}"
        builder.row(
            InlineKeyboardButton(
                text="✅" if done else "☐",
                callback_data=ItemAction(action="toggle", item_id=item.id).pack(),
            ),
            InlineKeyboardButton(
                text=_clip(label, 50),
                callback_data=ItemAction(action="open", item_id=item.id).pack(),
            ),
        )
    builder.row(_nav("passed", "🗄 Прошедшие"), _nav("digest", "📅 Ближайшие"))
    return builder.as_markup()


def passed_keyboard(items: list[Item]) -> InlineKeyboardMarkup:
    """Кнопки прошедших записей."""
    builder = InlineKeyboardBuilder()
    for index, item in enumerate(items, start=1):
        builder.row(
            InlineKeyboardButton(
                text=_clip(f"{index}. {item.title}"),
                callback_data=ItemAction(action="open", item_id=item.id).pack(),
            )
        )
    builder.row(_nav("tasks", BACK))
    return builder.as_markup()


def card_keyboard(item: Item, *, can_modify: bool, completed: bool = False) -> InlineKeyboardMarkup:
    """Кнопки карточки записи."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="↩️ Вернуть в работу" if completed else "✅ Выполнено",
            callback_data=ItemAction(action="togglecard", item_id=item.id).pack(),
        )
    )
    if can_modify:
        builder.row(
            InlineKeyboardButton(
                text="✏️ Изменить",
                callback_data=ItemAction(action="edit", item_id=item.id).pack(),
            ),
            InlineKeyboardButton(
                text="🔔 Напоминания",
                callback_data=ItemAction(action="rem", item_id=item.id).pack(),
            ),
        )
        if item.is_open:
            builder.row(
                InlineKeyboardButton(
                    text="📦 Закрыть",
                    callback_data=ItemAction(action="close", item_id=item.id).pack(),
                )
            )
        builder.row(
            InlineKeyboardButton(
                text="🗑 Удалить", callback_data=ItemAction(action="del", item_id=item.id).pack()
            )
        )
    builder.row(_nav("tasks", "📋 К списку"))
    return builder.as_markup()


def confirm_delete_keyboard(item_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления записи."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🗑 Да, удалить",
            callback_data=ItemAction(action="delyes", item_id=item_id).pack(),
        ),
        InlineKeyboardButton(
            text=CANCEL, callback_data=ItemAction(action="open", item_id=item_id).pack()
        ),
    )
    return builder.as_markup()


def kind_keyboard() -> InlineKeyboardMarkup:
    """Выбор типа записи."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📝 Задача", callback_data=AddStep(step="kind", value=ItemKind.TASK).pack()
        ),
        InlineKeyboardButton(
            text="📅 Важная дата", callback_data=AddStep(step="kind", value=ItemKind.EVENT).pack()
        ),
    )
    builder.row(InlineKeyboardButton(text=CANCEL, callback_data=AddStep(step="cancel").pack()))
    return builder.as_markup()


def subjects_keyboard(subjects: list[Subject]) -> InlineKeyboardMarkup:
    """Выбор предмета в мастере добавления."""
    builder = InlineKeyboardBuilder()
    for subject in subjects:
        builder.row(
            InlineKeyboardButton(
                text=_clip(subject.name),
                callback_data=AddStep(step="subject", value=str(subject.id)).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text="➕ Новый предмет", callback_data=AddStep(step="newsubj").pack())
    )
    builder.row(InlineKeyboardButton(text=CANCEL, callback_data=AddStep(step="cancel").pack()))
    return builder.as_markup()


def categories_keyboard(kind: ItemKind) -> InlineKeyboardMarkup:
    """Выбор категории, зависящей от типа записи."""
    builder = InlineKeyboardBuilder()
    for value in categories_for(kind):
        builder.button(
            text=category_label(kind, value),
            callback_data=AddStep(step="category", value=value).pack(),
        )
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=CANCEL, callback_data=AddStep(step="cancel").pack()))
    return builder.as_markup()


def skip_keyboard() -> InlineKeyboardMarkup:
    """Пропуск необязательного шага."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=SKIP, callback_data=AddStep(step="skip").pack()))
    builder.row(InlineKeyboardButton(text=CANCEL, callback_data=AddStep(step="cancel").pack()))
    return builder.as_markup()


def calendar_keyboard(
    *, year: int, month: int, today: date, kind: ItemKind, allow_open: bool
) -> InlineKeyboardMarkup:
    """Инлайн-календарь: сетка месяца, быстрые кнопки и переключение месяцев."""
    builder = InlineKeyboardBuilder()

    prev_year, prev_month = shift_month(year, month, -1)
    next_year, next_month = shift_month(year, month, 1)
    builder.row(
        InlineKeyboardButton(
            text="‹" if can_go_back(year, month, today) else " ",
            callback_data=CalendarNav(action="nav", year=prev_year, month=prev_month).pack()
            if can_go_back(year, month, today)
            else NOOP,
        ),
        InlineKeyboardButton(text=month_title(year, month), callback_data=NOOP),
        InlineKeyboardButton(
            text="›" if can_go_forward(year, month, today) else " ",
            callback_data=CalendarNav(action="nav", year=next_year, month=next_month).pack()
            if can_go_forward(year, month, today)
            else NOOP,
        ),
    )
    builder.row(*(InlineKeyboardButton(text=day, callback_data=NOOP) for day in WEEKDAYS_RU))

    for week in month_grid(year, month):
        buttons = []
        for cell in week:
            if cell is None:
                buttons.append(InlineKeyboardButton(text=" ", callback_data=NOOP))
            elif not is_selectable(cell, today):
                buttons.append(InlineKeyboardButton(text="·", callback_data=NOOP))
            else:
                label = f"·{cell.day}·" if cell == today else str(cell.day)
                buttons.append(
                    InlineKeyboardButton(
                        text=label,
                        callback_data=CalendarNav(
                            action="pick", year=cell.year, month=cell.month, day=cell.day
                        ).pack(),
                    )
                )
        builder.row(*buttons)

    builder.row(
        InlineKeyboardButton(text="Сегодня", callback_data=CalendarNav(action="today").pack()),
        InlineKeyboardButton(text="Завтра", callback_data=CalendarNav(action="tomorrow").pack()),
        InlineKeyboardButton(text="+7 дней", callback_data=CalendarNav(action="week").pack()),
    )
    if allow_open and kind is ItemKind.TASK:
        builder.row(
            InlineKeyboardButton(
                text="📌 До конца модуля", callback_data=CalendarNav(action="open").pack()
            )
        )
    builder.row(InlineKeyboardButton(text=CANCEL, callback_data=AddStep(step="cancel").pack()))
    return builder.as_markup()


def time_keyboard(kind: ItemKind) -> InlineKeyboardMarkup:
    """Пресеты времени плюс ручной ввод."""
    builder = InlineKeyboardBuilder()
    for preset in time_presets_for(kind):
        builder.button(
            text=_hhmm(preset),
            callback_data=TimePick(action="pick", hour=preset.hour, minute=preset.minute).pack(),
        )
    builder.adjust(4)
    builder.row(
        InlineKeyboardButton(text="Другое время", callback_data=TimePick(action="custom").pack()),
        InlineKeyboardButton(text=BACK, callback_data=CalendarNav(action="back").pack()),
    )
    return builder.as_markup()


def offsets_keyboard(
    *, scope: str, target_id: int, selected: set[int], allow_reset: bool
) -> InlineKeyboardMarkup:
    """Тумблеры сроков напоминаний."""
    builder = InlineKeyboardBuilder()
    for preset in OFFSET_PRESETS:
        mark = "✅" if preset.minutes in selected else "☑️"
        builder.button(
            text=f"{mark} {preset.label}",
            callback_data=OffsetToggle(
                scope=scope, target_id=target_id, action="toggle", minutes=preset.minutes
            ).pack(),
        )
    builder.adjust(2)
    tail = [
        InlineKeyboardButton(
            text=DONE,
            callback_data=OffsetToggle(scope=scope, target_id=target_id, action="done").pack(),
        )
    ]
    if allow_reset:
        tail.append(
            InlineKeyboardButton(
                text="↩️ Как в группе",
                callback_data=OffsetToggle(scope=scope, target_id=target_id, action="reset").pack(),
            )
        )
    builder.row(*tail)
    return builder.as_markup()


def item_reminders_keyboard(item: Item) -> InlineKeyboardMarkup:
    """Экран напоминаний конкретной записи."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔧 Настроить своё",
            callback_data=OffsetToggle(scope="item", target_id=item.id, action="edit").pack(),
        )
    )
    if item.reminders_muted:
        builder.row(
            InlineKeyboardButton(
                text="🔔 Включить",
                callback_data=OffsetToggle(scope="item", target_id=item.id, action="unmute").pack(),
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text="🔕 Выключить",
                callback_data=OffsetToggle(scope="item", target_id=item.id, action="mute").pack(),
            )
        )
    if item.reminder_offsets is not None:
        builder.row(
            InlineKeyboardButton(
                text="↩️ Как в группе",
                callback_data=OffsetToggle(scope="item", target_id=item.id, action="reset").pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=BACK, callback_data=ItemAction(action="open", item_id=item.id).pack()
        )
    )
    return builder.as_markup()


def draft_confirm_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение создания записи."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Создать", callback_data=AddStep(step="confirm").pack())
    )
    builder.row(
        InlineKeyboardButton(text="🔔 Напоминания", callback_data=AddStep(step="reminders").pack()),
        InlineKeyboardButton(text=CANCEL, callback_data=AddStep(step="cancel").pack()),
    )
    return builder.as_markup()


def subjects_manage_keyboard(subjects: list[Subject]) -> InlineKeyboardMarkup:
    """Список предметов с удалением."""
    builder = InlineKeyboardBuilder()
    for index, subject in enumerate(subjects, start=1):
        builder.button(
            text=f"🗑 {index}",
            callback_data=SubjectAction(action="del", subject_id=subject.id).pack(),
        )
    builder.adjust(4)
    builder.row(
        InlineKeyboardButton(text="➕ Добавить", callback_data=SubjectAction(action="add").pack())
    )
    builder.row(_nav("home", BACK))
    return builder.as_markup()


def group_keyboard(*, has_group: bool, is_creator: bool, group_count: int) -> InlineKeyboardMarkup:
    """Экран группы."""
    builder = InlineKeyboardBuilder()
    if group_count > 1:
        builder.row(
            InlineKeyboardButton(
                text="🔄 Сменить группу", callback_data=GroupAction(action="switch").pack()
            )
        )
    builder.row(
        InlineKeyboardButton(text="➕ Создать", callback_data=GroupAction(action="new").pack()),
        InlineKeyboardButton(
            text="🔑 Войти по коду", callback_data=GroupAction(action="join").pack()
        ),
    )
    if has_group:
        if is_creator:
            builder.row(_nav("admin", "🛠 Настройки группы"))
        builder.row(
            InlineKeyboardButton(text="🚪 Выйти", callback_data=GroupAction(action="leave").pack())
        )
    builder.row(_nav("home", BACK))
    return builder.as_markup()


def group_switch_keyboard(groups: list[tuple[Group, bool]]) -> InlineKeyboardMarkup:
    """Выбор активной группы."""
    builder = InlineKeyboardBuilder()
    for group, is_active in groups:
        mark = "▶️ " if is_active else ""
        builder.row(
            InlineKeyboardButton(
                text=_clip(f"{mark}{group.name}"),
                callback_data=GroupAction(action="set", group_id=group.id).pack(),
            )
        )
    builder.row(_nav("group", BACK))
    return builder.as_markup()


def settings_keyboard(*, personal_enabled: bool, is_creator: bool) -> InlineKeyboardMarkup:
    """Личные настройки участника."""
    builder = InlineKeyboardBuilder()
    label = (
        "🔕 Выключить личные напоминания" if personal_enabled else "🔔 Включить личные напоминания"
    )
    builder.row(
        InlineKeyboardButton(
            text=label,
            callback_data=AdminAction(
                action="personal", value="0" if personal_enabled else "1"
            ).pack(),
        )
    )
    if is_creator:
        builder.row(_nav("admin", "🛠 Настройки группы"))
    builder.row(_nav("home", BACK))
    return builder.as_markup()


def admin_keyboard() -> InlineKeyboardMarkup:
    """Настройки группы для создателя."""
    builder = InlineKeyboardBuilder()
    builder.row(_nav("reminders", "🔔 Напоминания"))
    builder.row(
        InlineKeyboardButton(text="🌍 Часовой пояс", callback_data=AdminAction(action="tz").pack()),
        InlineKeyboardButton(text="📰 Дайджест", callback_data=AdminAction(action="digest").pack()),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔑 Новый invite-код", callback_data=AdminAction(action="regen").pack()
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="👑 Передать группу", callback_data=AdminAction(action="transfer").pack()
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🗑 Удалить группу", callback_data=AdminAction(action="delete").pack()
        )
    )
    builder.row(_nav("group", BACK))
    return builder.as_markup()


def group_reminders_keyboard() -> InlineKeyboardMarkup:
    """Настройки напоминаний группы."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📝 Для задач",
            callback_data=OffsetToggle(scope="group_task", target_id=0, action="edit").pack(),
        ),
        InlineKeyboardButton(
            text="📅 Для важных дат",
            callback_data=OffsetToggle(scope="group_event", target_id=0, action="edit").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🕙 Время рассылки", callback_data=AdminAction(action="hour").pack()
        )
    )
    builder.row(_nav("admin", BACK))
    return builder.as_markup()


def hours_keyboard(action: str) -> InlineKeyboardMarkup:
    """Выбор часа (для напоминаний или дайджеста)."""
    builder = InlineKeyboardBuilder()
    for hour in range(24):
        builder.button(
            text=f"{hour:02d}", callback_data=AdminAction(action=action, value=str(hour)).pack()
        )
    builder.adjust(6)
    builder.row(_nav("reminders" if action == "sethour" else "admin", BACK))
    return builder.as_markup()


def weekdays_keyboard() -> InlineKeyboardMarkup:
    """Выбор дня недели для дайджеста."""
    builder = InlineKeyboardBuilder()
    for index, name in enumerate(WEEKDAYS_RU):
        builder.button(
            text=name, callback_data=AdminAction(action="setday", value=str(index)).pack()
        )
    builder.adjust(7)
    builder.row(_nav("admin", BACK))
    return builder.as_markup()


def members_keyboard(members: list[Member], *, action: str) -> InlineKeyboardMarkup:
    """Список участников для выбора."""
    builder = InlineKeyboardBuilder()
    for member in members:
        if member.is_creator:
            continue
        builder.row(
            InlineKeyboardButton(
                text=_clip(member.user.full_name),
                callback_data=MemberAction(action=action, user_id=member.user.id).pack(),
            )
        )
    builder.row(_nav("admin", BACK))
    return builder.as_markup()


def confirm_keyboard(
    *, yes_callback: str, no_callback: str, yes_text: str = "Да"
) -> InlineKeyboardMarkup:
    """Универсальное подтверждение."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=yes_text, callback_data=yes_callback),
        InlineKeyboardButton(text=CANCEL, callback_data=no_callback),
    )
    return builder.as_markup()


def back_keyboard(to: str) -> InlineKeyboardMarkup:
    """Одна кнопка «Назад»."""
    builder = InlineKeyboardBuilder()
    builder.row(_nav(to, BACK))
    return builder.as_markup()


def _hhmm(moment: time) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


def _clip(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
