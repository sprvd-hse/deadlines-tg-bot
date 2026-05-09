from aiogram.fsm.state import State, StatesGroup


class AddAssignment(StatesGroup):
    waiting_type = State()
    waiting_subject = State()
    waiting_title = State()
    waiting_description = State()
    waiting_deadline = State()
    waiting_links = State()
    waiting_confirm = State()


class GroupSettings(StatesGroup):
    waiting_tz = State()


class Subjects(StatesGroup):
    waiting_name = State()


class EditAssignment(StatesGroup):
    waiting_type = State()
    waiting_subject = State()
    waiting_title = State()
    waiting_description = State()
    waiting_deadline = State()
    waiting_links = State()
    waiting_confirm = State()
