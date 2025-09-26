from __future__ import annotations

import logging
from textwrap import dedent

from aiogram import Router, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import StateFilter

from ..config import ProviderConfig
from ..db.repo import ChatPreferences, Repository
from ..monitor.scheduler import MonitorScheduler
from ..monitor.detail_scheduler import DetailScanScheduler
from ..monitor.detail_service import DetailScanService
from ..util.timeparse import parse_duration
from .keyboards import main_menu_keyboard, settings_menu_keyboard

LOGGER = logging.getLogger(__name__)


class KeywordsForm(StatesGroup):
    waiting_for_keywords = State()
    waiting_for_interval = State()
    waiting_for_pages = State()


def create_router(
    repo: Repository,
    monitor_scheduler: MonitorScheduler,
    detail_scheduler: DetailScanScheduler,
    detail_service: DetailScanService,
    provider_config: ProviderConfig,
) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def command_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        prefs = await repo.get_or_create_user(
            message.chat.id,
            message.from_user.username if message.from_user else None,
            default_interval=provider_config.check_interval_default,
            default_pages=provider_config.pages_default,
        )
        await message.answer(
            dedent(
                """
                Привет! Я бот для мониторинга закупок goszakupki.by.
                Используй /help, чтобы увидеть список команд.
                """
            ).strip(),
            reply_markup=main_menu_keyboard(prefs.enabled),
        )
        await monitor_scheduler.refresh_schedule()
        await detail_scheduler.refresh_schedule()

    @router.message(Command("help"))
    async def command_help(message: Message) -> None:
        await message.answer(
            dedent(
                """
                Доступные команды:
                /settings — показать текущие настройки
                /set_keywords — задать ключевые слова (одно сообщение, каждое на новой строке)
                /set_interval <интервал> — интервал проверки (например, 5m, 1h)
                /set_pages <число> — количество страниц для проверки
                /enable — включить мониторинг
                /disable — выключить мониторинг
                /test — отправить тестовое уведомление
                /status — показать статус мониторинга
                /cancel — отменить текущий ввод
                """
            ).strip()
        )

    @router.message(Command("settings"))
    @router.message(Command("status"))
    async def command_settings(message: Message) -> None:
        prefs = await repo.get_preferences(message.chat.id)
        if not prefs:
            await message.answer("Сначала отправь /start")
            return
        text = _format_preferences(prefs)
        await message.answer(text)

    # Русские кнопки (ReplyKeyboard) — эквиваленты команд
    @router.message(F.text.casefold() == "настройки")
    async def ru_settings_menu(message: Message) -> None:
        prefs = await repo.get_preferences(message.chat.id)
        if not prefs:
            await message.answer("Сначала отправь /start")
            return
        text = _format_preferences(prefs)
        await message.answer(text, reply_markup=settings_menu_keyboard(prefs.enabled))

    @router.message(F.text.casefold() == "статус")
    async def ru_status(message: Message) -> None:
        await command_settings(message)

    @router.message(F.text.casefold() == "помощь")
    async def ru_help(message: Message) -> None:
        await command_help(message)

    @router.message(F.text.casefold() == "назад")
    async def ru_back(message: Message) -> None:
        prefs = await repo.get_preferences(message.chat.id)
        await message.answer("Главное меню", reply_markup=main_menu_keyboard(prefs.enabled if prefs else False))

    # Глобальная отмена доступна в любом состоянии
    @router.message(Command("cancel"), StateFilter("*"))
    @router.message(F.text.casefold() == "отмена", StateFilter("*"))
    async def command_cancel_any(message: Message, state: FSMContext) -> None:
        await state.clear()
        prefs = await repo.get_preferences(message.chat.id)
        await message.answer("Операция отменена", reply_markup=main_menu_keyboard(prefs.enabled if prefs else False))

    @router.message(Command("set_keywords"))
    async def command_set_keywords(message: Message, state: FSMContext) -> None:
        await state.set_state(KeywordsForm.waiting_for_keywords)
        # Уберём reply-клавиатуру, чтобы кнопки не мешали вводу
        await message.answer("Ввод ключевых слов начат", reply_markup=ReplyKeyboardRemove())
        # Сообщение с инструкцией и инлайн-кнопкой отмены
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel_keywords")]])
        await message.answer(
            "Пришли ключевые слова одним сообщением, каждое с новой строки. Пустые строки будут проигнорированы.",
            reply_markup=kb,
        )

    @router.message(StateFilter(KeywordsForm.waiting_for_keywords), F.text & ~F.text.startswith("/"))
    async def receive_keywords(message: Message, state: FSMContext) -> None:
        # Предохранитель: не перезаписывать ключевые слова, если пользователь нажал кнопку или ввёл команду
        if not message.text:
            await message.answer("Отправь список ключевых слов текстом или нажми ‘Отмена’")
            return
        raw = (message.text or "").strip()
        lower = raw.casefold()
        known_buttons = {
            "настройки",
            "статус",
            "ключевые слова",
            "включить",
            "выключить",
            "помощь",
            "интервал",
            "страницы",
            "отмена",
        }
        if raw.startswith("/") or lower in known_buttons:
            await message.answer("Сейчас идёт ввод ключевых слов. Отправь список или нажми ‘Отмена’.")
            return
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        await repo.update_keywords(message.chat.id, lines)
        await state.clear()
        prefs2 = await repo.get_preferences(message.chat.id)
        await message.answer("Ключевые слова обновлены", reply_markup=main_menu_keyboard(prefs2.enabled if prefs2 else False))

    @router.callback_query(F.data == "cancel_keywords")
    async def cancel_keywords_cb(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        prefs = await repo.get_preferences(callback.message.chat.id)
        await callback.message.answer("Операция отменена", reply_markup=main_menu_keyboard(prefs.enabled if prefs else False))
        await callback.answer()

    @router.message(F.text.casefold() == "ключевые слова")
    async def ru_set_keywords(message: Message, state: FSMContext) -> None:
        await command_set_keywords(message, state)

    @router.message(Command("set_interval"))
    async def command_set_interval(message: Message, command: CommandObject) -> None:
        if not command.args:
            await message.answer("Укажи интервал, например: /set_interval 5m")
            return
        try:
            seconds = parse_duration(command.args)
        except ValueError as exc:
            await message.answer(f"Не удалось распознать интервал: {exc}")
            return
        await repo.set_interval(message.chat.id, seconds)
        await monitor_scheduler.refresh_schedule()
        await detail_scheduler.refresh_schedule()
        prefs = await repo.get_preferences(message.chat.id)
        await message.answer(f"Интервал обновлён: {seconds} секунд", reply_markup=main_menu_keyboard(prefs.enabled if prefs else False))

    # Обработчик /cancel ниже оставлен для совместимости (глобальный выше перехватит)

    # Кнопка: Инвервал (запрос значения)
    @router.message(F.text.casefold() == "интервал")
    async def ru_interval_prompt(message: Message, state: FSMContext) -> None:
        await state.set_state(KeywordsForm.waiting_for_interval)
        await message.answer("Укажи интервал проверки (например: 5m, 1h, 30s)")

    @router.message(KeywordsForm.waiting_for_interval)
    async def ru_interval_receive(message: Message, state: FSMContext) -> None:
        try:
            seconds = parse_duration(message.text or "")
        except ValueError as exc:
            await message.answer(f"Не удалось распознать интервал: {exc}. Попробуй ещё раз.")
            return
        await state.clear()
        await repo.set_interval(message.chat.id, seconds)
        await monitor_scheduler.refresh_schedule()
        await detail_scheduler.refresh_schedule()
        await message.answer(f"Интервал обновлён: {seconds} секунд")

    @router.message(Command("set_pages"))
    async def command_set_pages(message: Message, command: CommandObject) -> None:
        if not command.args:
            await message.answer("Укажи число страниц, например: /set_pages 2")
            return
        try:
            pages = int(command.args.strip())
            if pages <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Число страниц должно быть положительным целым")
            return
        await repo.set_pages(message.chat.id, pages)
        prefs = await repo.get_preferences(message.chat.id)
        await message.answer(f"Количество страниц обновлено: {pages}", reply_markup=main_menu_keyboard(prefs.enabled if prefs else False))

    # Кнопка: Страницы (запрос значения)
    @router.message(F.text.casefold() == "страницы")
    async def ru_pages_prompt(message: Message, state: FSMContext) -> None:
        await state.set_state(KeywordsForm.waiting_for_pages)
        await message.answer("Укажи число страниц для проверки (положительное целое)")

    @router.message(KeywordsForm.waiting_for_pages)
    async def ru_pages_receive(message: Message, state: FSMContext) -> None:
        try:
            pages = int((message.text or "").strip())
            if pages <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Число страниц должно быть положительным целым. Попробуй ещё раз.")
            return
        await state.clear()
        await repo.set_pages(message.chat.id, pages)
        await message.answer(f"Количество страниц обновлено: {pages}")

    @router.message(Command("enable"))
    async def command_enable(message: Message) -> None:
        await repo.set_enabled(message.chat.id, True)
        await monitor_scheduler.refresh_schedule()
        await detail_scheduler.refresh_schedule()
        prefs = await repo.get_preferences(message.chat.id)
        await message.answer("Мониторинг включён", reply_markup=main_menu_keyboard(prefs.enabled if prefs else False))

    @router.message(F.text.casefold() == "включить")
    async def ru_enable(message: Message) -> None:
        await command_enable(message)

    @router.message(Command("disable"))
    async def command_disable(message: Message) -> None:
        await repo.set_enabled(message.chat.id, False)
        await monitor_scheduler.refresh_schedule()
        await detail_scheduler.refresh_schedule()
        prefs = await repo.get_preferences(message.chat.id)
        await message.answer("Мониторинг выключен", reply_markup=main_menu_keyboard(prefs.enabled if prefs else False))

    @router.message(F.text.casefold() == "выключить")
    async def ru_disable(message: Message) -> None:
        await command_disable(message)

    @router.message(F.text.casefold() == "тест")
    async def ru_test(message: Message) -> None:
        await command_test(message)

    @router.message(Command("test"))
    async def command_test(message: Message) -> None:
        prefs = await repo.get_preferences(message.chat.id)
        if not prefs:
            await message.answer("Сначала отправь /start")
            return
        text = "\n".join(
            [
                f"🛒 Тестовое уведомление ({provider_config.source_id})",
                "Название: Пример закупки",
                f"Ссылка: {provider_config.base_url}",
                "Номер: auc0000000000",
            ]
        )
        await message.answer(text)

    @router.message(Command("detail_status"))
    async def command_detail_status(message: Message) -> None:
        count = await repo.count_pending_detail()
        await message.answer(f"Детсканер: ожиданий в очереди: {count}")

    @router.message(Command("detail_run"))
    async def command_detail_run(message: Message) -> None:
        await message.answer("Запускаю детальный скан...")
        await detail_service.run_scan()
        count = await repo.count_pending_detail()
        await message.answer(f"Готово. Осталось в очереди: {count}")

    return router


def _format_preferences(prefs: ChatPreferences) -> str:
    keywords_display = "\n".join(prefs.keywords) if prefs.keywords else "(нет)"
    status = "включён" if prefs.enabled else "выключен"
    return (
        "\n".join(
            [
                f"Статус: {status}",
                f"Интервал: {prefs.interval_seconds} сек.",
                f"Страниц: {prefs.pages}",
                "Ключевые слова:",
                keywords_display,
            ]
        )
    )
