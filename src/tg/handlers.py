from __future__ import annotations

import logging
from textwrap import dedent
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import hashlib
from aiogram.filters import StateFilter

from ..config import ProviderConfig, AppConfig
from ..db.repo import Repository, AppPreferences
from .auth_state import AuthState
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


class LoginForm(StatesGroup):
    waiting_for_login = State()
    waiting_for_password = State()

class KeywordAddForm(StatesGroup):
    waiting_for_keyword = State()


def create_router(
    repo: Repository,
    monitor_scheduler: MonitorScheduler,
    detail_scheduler: DetailScanScheduler,
    detail_service: DetailScanService,
    provider_config: ProviderConfig,
    auth: AppConfig.AuthConfig,
    auth_state: AuthState,
) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def command_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        # Не создаём пользователя до авторизации
        if not auth_state.is_authorized(message.chat.id):
            await message.answer(
                dedent(
                    """
                    Доступ к боту ограничен. Выполните авторизацию:
                    /login <логин> <пароль>
                    """
                ).strip()
            )
            return

        prefs = await repo.get_or_create_settings(
            default_interval=provider_config.check_interval_default,
            default_pages=provider_config.pages_default,
        )
        await message.answer(
            dedent(
                """
                Привет! Я бот для мониторинга закупок goszakupki.by.
                
                Быстрый старт:
                1) Нажми «Настройки» и задай «Ключевые слова» (каждое с новой строки)
                2) Укажи «Интервал» и «Страницы» (при необходимости)
                3) Вернись «Назад» и нажми «Включить»
                4) Проверить состояние: «Статус»
                
                Подсказки:
                • /help — список команд
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
                Быстрый старт:
                1) «Настройки» → «Ключевые слова» — пришли список (по одному на строку)
                2) «Интервал»/«Страницы» — при необходимости
                3) «Назад» → «Включить»
                
                Команды:
                /settings — открыть настройки
                /set_keywords — задать ключевые слова сообщением
                /keywords — управление по одному (добавление/удаление)
                /set_interval <интервал> — например: 5m, 1h, 30s
                /set_pages <число> — количество страниц для проверки
                /enable — включить мониторинг
                /disable — выключить мониторинг
                /status — показать статус
                /test — тестовое уведомление
                /cancel — отменить текущий ввод
                """
            ).strip()
        )

    @router.message(Command("login"))
    async def command_login(message: Message, state: FSMContext, command: CommandObject) -> None:
        args = (command.args or "").strip()
        if not (auth.login and auth.password):
            await message.answer("AUTH_LOGIN/AUTH_PASSWORD не настроены в окружении контейнера.")
            return
        if args:
            parts = args.split()
            if len(parts) >= 2:
                login, password = parts[0], " ".join(parts[1:])
                user_id = message.from_user.id if message.from_user else None
                if await auth_state.try_login(message.chat.id, login, password, user_id=user_id):
                    await state.clear()
                    await message.answer("Успешная авторизация. Отправьте /start.")
                else:
                    await message.answer("Неверные учётные данные.")
                return
        # Wizard mode
        await state.set_state(LoginForm.waiting_for_login)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel_login")]])
        await message.answer("Введите логин:", reply_markup=kb)

    @router.message(StateFilter(LoginForm.waiting_for_login), F.text & ~F.text.startswith("/"))
    async def login_receive_login(message: Message, state: FSMContext) -> None:
        login = (message.text or "").strip()
        await state.update_data(login=login)
        await state.set_state(LoginForm.waiting_for_password)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel_login")]])
        await message.answer("Введите пароль:", reply_markup=kb)

    @router.message(StateFilter(LoginForm.waiting_for_password), F.text & ~F.text.startswith("/"))
    async def login_receive_password(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        login = str(data.get("login") or "")
        password = (message.text or "").strip()
        user_id = message.from_user.id if message.from_user else None
        if await auth_state.try_login(message.chat.id, login, password, user_id=user_id):
            await state.clear()
            await message.answer("Успешная авторизация. Отправьте /start.")
        else:
            await state.clear()
            await message.answer("Неверные учётные данные. Повторите: /login")

    @router.callback_query(F.data == "cancel_login")
    async def login_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.message.answer("Авторизация отменена. Отправьте /login для повтора.")
        await callback.answer()

    @router.message(Command("settings"))
    async def command_settings(message: Message) -> None:
        prefs = await repo.get_preferences()
        if not prefs:
            await message.answer("Сначала отправь /start")
            return
        text = _format_preferences(prefs)
        await message.answer(text, reply_markup=settings_menu_keyboard(prefs.enabled))

    @router.message(Command("status"))
    async def command_status(message: Message) -> None:
        prefs = await repo.get_preferences()
        if not prefs:
            await message.answer("Сначала отправь /start")
            return
        text = await _format_status(repo, prefs, provider_config)
        await message.answer(text, reply_markup=main_menu_keyboard(prefs.enabled))

    # Русские кнопки (ReplyKeyboard) — эквиваленты команд
    @router.message(F.text.casefold() == "настройки")
    async def ru_settings_menu(message: Message) -> None:
        prefs = await repo.get_preferences()
        if not prefs:
            await message.answer("Сначала отправь /start")
            return
        text = _format_preferences(prefs)
        await message.answer(text, reply_markup=settings_menu_keyboard(prefs.enabled))

    @router.message(F.text.casefold() == "статус")
    async def ru_status(message: Message) -> None:
        await command_status(message)

    @router.message(F.text.casefold() == "помощь")
    async def ru_help(message: Message) -> None:
        await command_help(message)

    @router.message(F.text.casefold() == "назад")
    async def ru_back(message: Message) -> None:
        prefs = await repo.get_preferences()
        await message.answer("Главное меню", reply_markup=main_menu_keyboard(prefs.enabled if prefs else False))

    # Очистка детекций: подтверждение через inline-кнопки
    @router.message(F.text.casefold() == "очистить детекции")
    async def ru_clear_detections_prompt(message: Message) -> None:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Подтвердить очистку", callback_data="confirm_clear_det"),
                    InlineKeyboardButton(text="Отмена", callback_data="cancel_clear_det"),
                ]
            ]
        )
        await message.answer(
            "Внимание: будут удалены все детекции для текущего источника. Уведомления не трогаем.",
            reply_markup=kb,
        )

    @router.callback_query(F.data == "confirm_clear_det")
    async def clear_detections_cb(callback: CallbackQuery) -> None:
        try:
            deleted = await repo.clear_detections(source_id=provider_config.source_id)
            await callback.message.answer(f"Очистка завершена. Удалено записей: {deleted}")
        except Exception:
            LOGGER.exception("Failed to clear detections")
            await callback.message.answer("Ошибка при очистке детекций")
        await callback.answer()

    @router.callback_query(F.data == "cancel_clear_det")
    async def clear_detections_cancel_cb(callback: CallbackQuery) -> None:
        await callback.message.answer("Очистка отменена")
        await callback.answer()

    # Глобальная отмена доступна в любом состоянии
    @router.message(Command("cancel"), StateFilter("*"))
    @router.message(F.text.casefold() == "отмена", StateFilter("*"))
    async def command_cancel_any(message: Message, state: FSMContext) -> None:
        await state.clear()
        prefs = await repo.get_preferences()
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

    # Управление ключевыми словами по одному
    @router.message(Command("keywords"))
    async def command_keywords_manage(message: Message) -> None:
        await _send_keywords_page(message, repo, page=1)

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
        await repo.update_keywords(lines)
        await state.clear()
        prefs2 = await repo.get_preferences()
        await message.answer("Ключевые слова обновлены", reply_markup=main_menu_keyboard(prefs2.enabled if prefs2 else False))

    @router.callback_query(F.data == "cancel_keywords")
    async def cancel_keywords_cb(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        prefs = await repo.get_preferences()
        await callback.message.answer("Операция отменена", reply_markup=main_menu_keyboard(prefs.enabled if prefs else False))
        await callback.answer()

    @router.message(F.text.casefold() == "ключевые слова")
    async def ru_set_keywords(message: Message, state: FSMContext) -> None:
        # Покажем меню управления по одному + оставим старый способ отдельной командой
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить", callback_data="kw_add"), InlineKeyboardButton(text="📃 Список", callback_data="kw_list:1")],
                [InlineKeyboardButton(text="✏ Заменить списком", callback_data="kw_replace")],
                [InlineKeyboardButton(text="🗑 Очистить все", callback_data="kw_clear_all:1")],
            ]
        )
        await message.answer("Управление ключевыми словами", reply_markup=kb)

    # Старт режима замены списком из меню
    @router.callback_query(F.data == "kw_replace")
    async def kw_replace_cb(callback: CallbackQuery, state: FSMContext) -> None:
        await command_set_keywords(callback.message, state)  # type: ignore[arg-type]
        await callback.answer()

    # Добавление одного ключевого слова
    @router.callback_query(F.data == "kw_add")
    async def kw_add_cb(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(KeywordAddForm.waiting_for_keyword)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="kw_cancel_add")]])
        await callback.message.answer("Введите ключевое слово для добавления:", reply_markup=kb)
        await callback.answer()

    @router.callback_query(F.data == "kw_cancel_add")
    async def kw_cancel_add_cb(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await _send_keywords_page(callback.message, repo, page=1, edit=True)  # type: ignore[arg-type]
        await callback.answer()

    @router.message(StateFilter(KeywordAddForm.waiting_for_keyword), F.text & ~F.text.startswith("/"))
    async def kw_add_receive(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Пустая строка. Введите ключевое слово или нажмите ‘Отмена’.")
            return
        added = await repo.add_keyword(text)
        await state.clear()
        if added:
            await message.answer(f"Добавлено ключевое слово: {text}")
        else:
            await message.answer("Такое ключевое слово уже есть.")
        await _send_keywords_page(message, repo, page=1)

    # Пагинация и удаление
    @router.callback_query(F.data.startswith("kw_list:"))
    async def kw_list_cb(callback: CallbackQuery) -> None:
        try:
            _, page_str = (callback.data or "").split(":", 1)
            page = int(page_str)
            if page < 1:
                page = 1
        except Exception:
            page = 1
        await _send_keywords_page(callback.message, repo, page=page, edit=True)  # type: ignore[arg-type]
        await callback.answer()

    @router.callback_query(F.data == "kw_back_menu")
    async def kw_back_menu_cb(callback: CallbackQuery) -> None:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить", callback_data="kw_add"), InlineKeyboardButton(text="📃 Список", callback_data="kw_list:1")],
                [InlineKeyboardButton(text="✏ Заменить списком", callback_data="kw_replace")],
                [InlineKeyboardButton(text="🗑 Очистить все", callback_data="kw_clear_all:1")],
            ]
        )
        try:
            await callback.message.edit_text("Управление ключевыми словами", reply_markup=kb)
        except Exception:
            await callback.message.answer("Управление ключевыми словами", reply_markup=kb)
        await callback.answer()

    @router.callback_query(F.data.startswith("kw_clear_all:"))
    async def kw_clear_all_cb(callback: CallbackQuery) -> None:
        # Ask for confirmation in-place
        data = (callback.data or "")
        page = 1
        try:
            _, page_str = data.split(":", 1)
            page = int(page_str)
        except Exception:
            page = 1
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Подтвердить очистку", callback_data=f"kw_clear_all_confirm:{page}"),
                    InlineKeyboardButton(text="⬅ Назад", callback_data="kw_back_menu"),
                ]
            ]
        )
        try:
            await callback.message.edit_text("Удалить все ключевые слова?", reply_markup=kb)
        except Exception:
            await callback.message.answer("Удалить все ключевые слова?", reply_markup=kb)
        await callback.answer()

    @router.callback_query(F.data.startswith("kw_clear_all_confirm:"))
    async def kw_clear_all_confirm_cb(callback: CallbackQuery) -> None:
        data = (callback.data or "")
        page = 1
        try:
            _, page_str = data.split(":", 1)
            page = int(page_str)
        except Exception:
            page = 1
        await repo.clear_keywords()
        await callback.answer("Очищено")
        await _send_keywords_page(callback.message, repo, page=page, edit=True)  # type: ignore[arg-type]

    @router.callback_query(F.data.startswith("kw_del:"))
    async def kw_del_cb(callback: CallbackQuery) -> None:
        data = (callback.data or "")
        # format: kw_del:<page>:<hash>
        parts = data.split(":", 2)
        page = 1
        kw_hash = ""
        if len(parts) == 3:
            try:
                page = int(parts[1])
            except Exception:
                page = 1
            kw_hash = parts[2]
        prefs = await repo.get_preferences()
        candidates = (prefs.keywords if prefs else [])
        target = None
        for k in candidates:
            if _kw_hash(k) == kw_hash:
                target = k
                break
        if not target:
            await callback.answer("Элемент не найден", show_alert=True)
            return
        removed = await repo.remove_keyword(target)
        if removed:
            await callback.answer("Удалено", show_alert=False)
        else:
            await callback.answer("Не удалось удалить", show_alert=False)
        await _send_keywords_page(callback.message, repo, page=page, edit=True)  # type: ignore[arg-type]

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
        await repo.set_interval(seconds)
        await monitor_scheduler.refresh_schedule()
        await detail_scheduler.refresh_schedule()
        prefs = await repo.get_preferences()
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
        await repo.set_interval(seconds)
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
        await repo.set_pages(pages)
        prefs = await repo.get_preferences()
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
        await repo.set_pages(pages)
        await message.answer(f"Количество страниц обновлено: {pages}")

    @router.message(Command("enable"))
    async def command_enable(message: Message) -> None:
        await repo.set_enabled(True)
        # Избежать лавины: пометить текущие детекции как уже уведомлённые
        try:
            await repo.seed_notifications_global_for_existing(provider_config.source_id)
        except Exception:
            LOGGER.exception("Failed to seed notifications for existing detections")
        await monitor_scheduler.refresh_schedule()
        await detail_scheduler.refresh_schedule()
        prefs = await repo.get_preferences()
        await message.answer("Мониторинг включён", reply_markup=main_menu_keyboard(prefs.enabled if prefs else False))

    @router.message(F.text.casefold() == "включить")
    async def ru_enable(message: Message) -> None:
        await command_enable(message)

    @router.message(Command("disable"))
    async def command_disable(message: Message) -> None:
        await repo.set_enabled(False)
        await monitor_scheduler.refresh_schedule()
        await detail_scheduler.refresh_schedule()
        prefs = await repo.get_preferences()
        await message.answer("Мониторинг выключен", reply_markup=main_menu_keyboard(prefs.enabled if prefs else False))

    @router.message(F.text.casefold() == "выключить")
    async def ru_disable(message: Message) -> None:
        await command_disable(message)

    @router.message(F.text.casefold() == "тест")
    async def ru_test(message: Message) -> None:
        await command_test(message)

    @router.message(Command("test"))
    async def command_test(message: Message) -> None:
        prefs = await repo.get_preferences()
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

    # Команды управления детсканером доступны только через переключатель мониторинга

    return router


def _format_preferences(prefs: AppPreferences) -> str:
    kws = prefs.keywords or []
    if not kws:
        kws_display = "(не заданы)"
    else:
        shown = kws[:10]
        kws_display = "\n".join(shown)
        if len(kws) > 10:
            kws_display += f"\n… и ещё {len(kws) - 10}"

    lines = [
        "Настройки:",
        f"• Интервал проверки: {prefs.interval_seconds} сек.",
        f"• Страниц для проверки: {prefs.pages}",
        "",
        "Ключевые слова:",
        kws_display,
        "",
        "Подсказки:",
        "• Кнопка ‘Ключевые слова’ — пришлите список, по одному на строку",
        "• ‘Интервал’ — например: 5m, 1h, 30s",
        "• ‘Страницы’ — положительное целое число",
        "• ‘Назад’ — вернуться в главное меню",
    ]
    return "\n".join(lines)


async def _format_status(repo: Repository, prefs: AppPreferences, provider_config: ProviderConfig) -> str:
    status = "включён" if prefs.enabled else "выключен"
    # Сегодня с полуночи по UTC (упрощённо)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    # Счётчики
    det_total = await repo.count_detections(source_id=provider_config.source_id)
    det_today = await repo.count_detections(source_id=provider_config.source_id, since=today_start)
    pending_detail = await repo.count_pending_detail()
    notif_total = await repo.count_notifications_global(source_id=provider_config.source_id)
    notif_today = await repo.count_notifications_global(source_id=provider_config.source_id, since=today_start)
    last_det = await repo.last_detection_time(source_id=provider_config.source_id)
    last_notif = await repo.last_notification_time_global(source_id=provider_config.source_id)

    kws = prefs.keywords or []
    kws_display = "\n".join(kws[:10]) if kws else "(нет)"
    if kws and len(kws) > 10:
        kws_display += f"\n… и ещё {len(kws) - 10}"

    lines = [
        f"Статус мониторинга: {status}",
        f"Интервал опроса: {prefs.interval_seconds} сек.",
        f"Интервал детсканера: {provider_config.detail.interval_seconds} сек.",
        f"Страниц для проверки: {prefs.pages}",
        "",
        "Данные:",
        f"• Детекции: всего {det_total}, сегодня {det_today}",
        f"• Очередь детсканера: {pending_detail}",
        f"• Отправленные уведомления: всего {notif_total}, сегодня {notif_today}",
    ]
    if last_det:
        lines.append(f"• Последняя детекция: {last_det}")
    if last_notif:
        lines.append(f"• Последнее уведомление: {last_notif}")
    lines.extend([
        "",
        "Ключевые слова:",
        kws_display,
    ])
    return "\n".join(lines)


# Helpers for keywords management
def _kw_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


async def _send_keywords_page(target: Message, repo: Repository, *, page: int, per_page: int = 5, edit: bool = False) -> None:
    prefs = await repo.get_preferences()
    items = prefs.keywords if prefs else []
    total = len(items)
    if total == 0:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="➕ Добавить", callback_data="kw_add"), InlineKeyboardButton(text="⬅ Назад", callback_data="kw_back_menu")]]
        )
        if edit:
            try:
                await target.edit_text("Ключевых слов пока нет", reply_markup=kb)
            except Exception:
                await target.answer("Ключевых слов пока нет", reply_markup=kb)
        else:
            await target.answer("Ключевых слов пока нет", reply_markup=kb)
        return
    # clamp page
    max_page = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, max_page))
    start = (page - 1) * per_page
    end = min(start + per_page, total)
    view = items[start:end]
    rows: list[list[InlineKeyboardButton]] = []
    for idx, k in enumerate(view, start=start + 1):
        label = f"❌ {idx}. {k}"
        if len(label) > 64:
            label = label[:61] + "…"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"kw_del:{page}:{_kw_hash(k)}")])
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅", callback_data=f"kw_list:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"Стр. {page}/{max_page}", callback_data=f"kw_list:{page}"))
    if page < max_page:
        nav.append(InlineKeyboardButton(text="➡", callback_data=f"kw_list:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="kw_add"), InlineKeyboardButton(text="⬅ Назад", callback_data="kw_back_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        try:
            await target.edit_text("Текущие ключевые слова:", reply_markup=kb)
        except Exception:
            # fallback to sending new message if edit fails (e.g., old message not found)
            await target.answer("Текущие ключевые слова:", reply_markup=kb)
    else:
        await target.answer("Текущие ключевые слова:", reply_markup=kb)
