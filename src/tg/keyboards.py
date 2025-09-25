from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/settings"), KeyboardButton(text="/status")],
            [KeyboardButton(text="/set_keywords"), KeyboardButton(text="/enable")],
            [KeyboardButton(text="/disable"), KeyboardButton(text="/help")],
        ],
        resize_keyboard=True,
    )
