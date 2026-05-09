START = (
    "Привет! Я бот для учёта дедлайнов учебной группы.\n\n"
    "Открой меню: /menu\n"
    "Полный список команд: /help"
)

DIGEST_BTN = "📅 Ближайшие дедлайны"
DIGEST_EMPTY = "В ближайшие 14 дней дедлайнов нет."

MENU_HOME = "🏠 Главное меню\nАктивная группа: «{group}»"
MENU_HOME_NO_GROUP = (
    "🏠 Главное меню\nУ тебя пока нет активной группы — создай свою или присоединись по коду."
)

HELP = (
    "Команды:\n"
    "/create_group <название> — создать группу\n"
    "/join <код> — присоединиться по invite-коду\n"
    "/my_groups — мои группы, переключение активной\n"
    "\n"
    "В активной группе:\n"
    "/add — добавить задание (FSM)\n"
    "/cancel — отменить текущий ввод\n"
    "/list — активные и прошедшие задания\n"
    "/list_subject <предмет> — фильтр по предмету\n"
    "/delete <номер> — удалить задание (только автор/creator)\n"
    "/edit <номер> — редактировать задание (только автор/creator)\n"
    "/done <номер> — отметить выполненным\n"
    "/undone <номер> — снять отметку о выполнении\n"
    "/subjects — список предметов\n"
    "/add_subject <название> — добавить предмет\n"
    "/delete_subject <название> — удалить предмет (если нет активных заданий)\n"
    "\n"
    "/settings — личные настройки в активной группе\n"
    "/leave — выйти из активной группы\n"
    "\n"
    "Только для создателя группы:\n"
    "/transfer_ownership — передать роль создателя\n"
    "/delete_group — удалить группу\n"
    "/bind_chat — привязать групповой чат для дайджестов (в чате)\n"
    "/unbind_chat — отвязать чат\n"
    "/group_settings — TZ, день и час дайджеста\n"
    "/regenerate_code — перевыпустить invite-код\n"
    "\n"
    "/help — эта справка"
)

USAGE_CREATE_GROUP = "Использование: /create_group <название группы>"
USAGE_JOIN = "Использование: /join <код>\nКод имеет формат XXXX-XXXX-XXXX."

GROUP_NAME_TOO_LONG = "Название слишком длинное (максимум 128 символов)."
GROUP_CREATED = (
    "Группа «{name}» создана.\n"
    "Invite-код: `{code}`\n\n"
    "Чтобы пригласить участников, отправь им: `/join {code}`"
)

JOIN_NOT_FOUND = "Группа с таким кодом не найдена. Проверь формат: XXXX-XXXX-XXXX."
JOIN_ALREADY = "Ты уже состоишь в группе «{name}». Активная группа переключена на неё."
JOIN_SUCCESS = "Готово! Ты присоединился к группе «{name}». Она стала активной."
JOIN_RATE_LIMIT = "Слишком много попыток. Попробуй через минуту."

MY_GROUPS_EMPTY = (
    "Ты пока не состоишь ни в одной группе.\n"
    "Создай свою (/create_group) или присоединись по коду (/join)."
)
MY_GROUPS_HEADER = "Твои группы (активная отмечена ✓). Нажми, чтобы сделать активной:"

SWITCHED_GROUP = "Активная группа: «{name}»"
ALREADY_ACTIVE = "Эта группа уже активна."
NOT_A_MEMBER = "Ты не состоишь в этой группе."

NO_ACTIVE_GROUP = (
    "Нет активной группы. Создай группу (/create_group), присоединись к существующей (/join) "
    "или выбери активную (/my_groups)."
)

CANCELLED = "Отменено."
NOTHING_TO_CANCEL = "Нечего отменять."

USAGE_ADD_SUBJECT = "Использование: /add_subject <название>"
USAGE_DELETE_SUBJECT = "Использование: /delete_subject <название>"
SUBJECT_ADDED = "Предмет «{name}» добавлен."
SUBJECT_ALREADY_EXISTS = "Предмет «{name}» уже есть."
SUBJECT_NOT_FOUND = "Предмет «{name}» не найден."
SUBJECT_HAS_ASSIGNMENTS = "Нельзя удалить предмет «{name}» — на нём есть активные задания."
SUBJECT_DELETED = "Предмет «{name}» удалён."
SUBJECTS_EMPTY = "В группе пока нет предметов. Добавь: /add_subject <название>"
SUBJECTS_HEADER = "Предметы группы:"

ADD_NEED_SUBJECTS = "Сначала добавь хотя бы один предмет: /add_subject <название>"
ADD_STEP_TYPE = "Тип задания:"
ADD_STEP_SUBJECT = "Выбери предмет:"
ADD_STEP_TITLE = "Введи название задания:"
ADD_STEP_DESCRIPTION = "Введи описание (или - чтобы пропустить):"
ADD_STEP_DEADLINE = (
    "Введи дедлайн в одном из форматов:\n"
    "• сегодня 18:00\n"
    "• завтра 23:59\n"
    "• 15.12 18:00\n"
    "• 15.12.2026 18:00"
)
ADD_DEADLINE_ERROR = "Не получилось разобрать: {error}. Попробуй ещё раз."
ADD_STEP_LINKS = (
    "Введи ссылки одной строкой через пробел или каждую с новой строки.\n"
    "Чтобы пропустить — отправь -"
)
ADD_LINKS_NONE_VALID = "Не нашёл ни одной валидной ссылки. Отправь ссылки или - чтобы пропустить."
ADD_CONFIRM_HEADER = "Проверь и подтверди:"
ADD_CREATED = "Создано задание #{short_id}: {title}"
ADD_CANCELLED = "Создание задания отменено."
TITLE_TOO_LONG = "Название слишком длинное (максимум 256 символов)."
DESCRIPTION_TOO_LONG = "Описание слишком длинное (максимум 2048 символов)."

LIST_EMPTY = "В группе нет активных и недавних заданий."
LIST_ACTIVE_HEADER = "Активные:"
LIST_PASSED_HEADER = "Прошедшие (за 14 дней):"

USAGE_DELETE = "Использование: /delete <номер>"
DELETE_NOT_FOUND = "Задание #{n} не найдено."
DELETE_FORBIDDEN = "Удалять задание может только его автор или создатель группы."
DELETE_ALREADY = "Задание #{n} уже удалено."
DELETE_OK = "Задание #{n} удалено."

USAGE_LIST_SUBJECT = "Использование: /list_subject <название предмета>"

USAGE_DONE = "Использование: /done <номер>"
USAGE_UNDONE = "Использование: /undone <номер>"
DONE_OK = "Задание #{n} отмечено как выполненное."
DONE_ALREADY = "Задание #{n} уже отмечено выполненным."
DONE_NOT_FOUND = "Задание #{n} не найдено или удалено."
UNDONE_OK = "Отметка о выполнении задания #{n} снята."
UNDONE_NOT_MARKED = "Задание #{n} не было отмечено."

USAGE_EDIT = "Использование: /edit <номер>"
EDIT_FORBIDDEN = "Редактировать задание может только его автор или создатель группы."
EDIT_KEEP_HINT = "Отправь - чтобы оставить старое значение."
EDIT_STEP_TITLE = "Введи название задания (текущее: {current}). " + EDIT_KEEP_HINT
EDIT_STEP_DESCRIPTION = (
    "Введи описание (текущее: {current}). " + EDIT_KEEP_HINT + " Отправь _ чтобы очистить."
)
EDIT_STEP_DEADLINE = (
    "Введи дедлайн (текущий: {current}) в одном из форматов:\n"
    "• сегодня 18:00\n"
    "• завтра 23:59\n"
    "• 15.12 18:00\n"
    "• 15.12.2026 18:00\n" + EDIT_KEEP_HINT
)
EDIT_STEP_LINKS = (
    "Введи ссылки (текущие: {current}). " + EDIT_KEEP_HINT + " Отправь _ чтобы очистить."
)
EDIT_CONFIRM_HEADER = "Проверь и подтверди изменения:"
EDIT_OK = "Задание #{n} обновлено."
EDIT_CANCELLED = "Редактирование отменено."

CREATOR_ONLY = "Команда доступна только создателю группы."
TRANSFER_NO_OTHER_MEMBERS = "В группе нет других участников для передачи прав."
TRANSFER_PICK_HEADER = "Выбери нового создателя группы:"
TRANSFER_CONFIRM = "Передать роль создателя пользователю {name}? Это действие можно отменить только новым /transfer_ownership."
TRANSFER_OK = "Роль создателя передана: {name}."
TRANSFER_CANCELLED = "Передача отменена."

DELETE_GROUP_CONFIRM_1 = (
    "Удалить группу «{name}»? Все задания, отметки и членства будут удалены безвозвратно."
)
DELETE_GROUP_CONFIRM_2 = "Точно удалить «{name}»? Это последний шанс отменить."
DELETE_GROUP_OK = "Группа «{name}» удалена."
DELETE_GROUP_CANCELLED = "Удаление отменено."

BIND_CHAT_NOT_GROUP = "Команда /bind_chat должна быть выполнена в групповом чате."
BIND_CHAT_NO_ACTIVE_GROUP = (
    "Не нашёл твоих групп. Сделай нужную группу активной (/my_groups) и повтори в чате."
)
BIND_CHAT_NOT_CREATOR = "Привязать чат может только создатель группы."
BIND_CHAT_BOT_CANT_WRITE = "У бота нет прав отправлять сообщения в этом чате."
BIND_CHAT_PICK = "К какой группе привязать этот чат?"
BIND_CHAT_NO_CREATOR_GROUPS = "У тебя нет групп, в которых ты создатель. Привязка невозможна."
BIND_CHAT_ALREADY_HERE = "Этот чат уже привязан к группе «{group}»."
BIND_CHAT_OK = "Чат «{chat}» привязан к группе «{group}»."
BIND_CHAT_MOVED = "Чат переехал на группу «{group}» (был привязан к «{from_group}»)."
BIND_CHAT_REPLACE_CONFIRM = (
    "К группе «{group}» уже привязан другой чат. Заменить на текущий?"
)
BIND_CHAT_REPLACED = "Привязка чата заменена."
BIND_CHAT_CANCELLED = "Привязка отменена."

UNBIND_CHAT_NONE = "У группы нет привязанного чата."
UNBIND_CHAT_OK = "Чат отвязан от группы «{group}»."

SETTINGS_HEADER = "Личные настройки в группе «{group}»:\nЛичные напоминания: {state}"
SETTINGS_TOGGLE_ON = "Включить напоминания"
SETTINGS_TOGGLE_OFF = "Выключить напоминания"
SETTINGS_REMINDERS_ON = "включены"
SETTINGS_REMINDERS_OFF = "выключены"
SETTINGS_UPDATED = "Настройки обновлены."

GROUP_SETTINGS_HEADER = (
    "Настройки группы «{group}»:\n"
    "TZ: {tz}\n"
    "Дайджест: {weekday} в {hour:02d}:00"
)
GROUP_SETTINGS_PICK_TZ = "Изменить TZ"
GROUP_SETTINGS_PICK_DAY = "День дайджеста"
GROUP_SETTINGS_PICK_HOUR = "Час дайджеста"
GROUP_SETTINGS_TZ_PROMPT = (
    "Введи название TZ в формате IANA, например: Europe/Moscow, Asia/Tbilisi."
)
GROUP_SETTINGS_TZ_INVALID = "Не распознал TZ. Пример: Europe/Moscow."
GROUP_SETTINGS_TZ_OK = "TZ обновлена: {tz}."
GROUP_SETTINGS_DAY_PROMPT = "Выбери день недели для дайджеста:"
GROUP_SETTINGS_HOUR_PROMPT = "Выбери час дайджеста (по локальному времени группы):"
GROUP_SETTINGS_DAY_OK = "День дайджеста: {weekday}."
GROUP_SETTINGS_HOUR_OK = "Час дайджеста: {hour:02d}:00."

REGENERATE_CODE_OK = "Новый invite-код: {code}\n\nСтарый больше не работает."

LEAVE_NEED_TRANSFER = (
    "Ты — создатель группы и в ней есть другие участники. Сначала выполни /transfer_ownership."
)
LEAVE_OK = "Ты вышел из группы «{group}»."
LEAVE_DELETED = "Ты был единственным участником группы «{group}» — она удалена."
LEAVE_NO_GROUP = "Ты не состоишь в активной группе."
