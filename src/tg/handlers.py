from __future__ import annotations

import logging
from textwrap import dedent

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from ..config import ProviderConfig
from ..db.repo import ChatPreferences, Repository
from ..monitor.scheduler import MonitorScheduler
from ..util.timeparse import parse_duration
from .keyboards import main_menu_keyboard

LOGGER = logging.getLogger(__name__)


class KeywordsForm(StatesGroup):
    waiting_for_keywords = State()


def create_router(repo: Repository, scheduler: MonitorScheduler, provider_config: ProviderConfig) -> Router:
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
            reply_markup=main_menu_keyboard(),
        )
        await scheduler.refresh_schedule()

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

    @router.message(Command("set_keywords"))
    async def command_set_keywords(message: Message, state: FSMContext) -> None:
        await state.set_state(KeywordsForm.waiting_for_keywords)
        await message.answer(
            "Пришли ключевые слова одним сообщением, каждое с новой строки. Пустые строки будут проигнорированы."
        )

    @router.message(KeywordsForm.waiting_for_keywords)
    async def receive_keywords(message: Message, state: FSMContext) -> None:
        lines = [line.strip() for line in message.text.splitlines()] if message.text else []
        await repo.update_keywords(message.chat.id, lines)
        await state.clear()
        await message.answer("Ключевые слова обновлены")

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
        await scheduler.refresh_schedule()
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
        await message.answer(f"Количество страниц обновлено: {pages}")

    @router.message(Command("enable"))
    async def command_enable(message: Message) -> None:
        await repo.set_enabled(message.chat.id, True)
        await scheduler.refresh_schedule()
        await message.answer("Мониторинг включён")

    @router.message(Command("disable"))
    async def command_disable(message: Message) -> None:
        await repo.set_enabled(message.chat.id, False)
        await scheduler.refresh_schedule()
        await message.answer("Мониторинг выключен")

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
