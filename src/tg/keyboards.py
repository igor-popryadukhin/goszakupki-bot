from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu_keyboard(enabled: bool = False) -> ReplyKeyboardMarkup:
    toggle_text = "Выключить" if enabled else "Включить"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Настройки"), KeyboardButton(text="Статус")],
            [KeyboardButton(text=toggle_text), KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
    )


def settings_menu_keyboard(enabled: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Ключевые слова")],
            [KeyboardButton(text="Интервал"), KeyboardButton(text="Страницы")],
            [KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )
