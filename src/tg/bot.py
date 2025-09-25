from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage


def create_bot(token: str) -> Bot:
    return Bot(token=token, parse_mode=None)


def create_dispatcher() -> Dispatcher:
    storage = MemoryStorage()
    return Dispatcher(storage=storage)
