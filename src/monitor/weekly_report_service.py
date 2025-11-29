from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot

from ..config import LoggingConfig
from ..db.repo import Repository
from ..tg.auth_state import AuthState

LOGGER = logging.getLogger(__name__)


class WeeklyReportService:
    def __init__(
        self,
        *,
        repository: Repository,
        bot: Bot,
        auth_state: AuthState,
        logging_config: LoggingConfig,
    ) -> None:
        self._repo = repository
        self._bot = bot
        self._auth_state = auth_state
        self._timezone = ZoneInfo(logging_config.timezone)

    async def send_report(self) -> None:
        targets = self._auth_state.all_targets()
        if not targets:
            LOGGER.info("Skip weekly report: no authorized targets")
            return

        report_text = await self.build_report()
        await self._broadcast(report_text, targets)

    async def build_report(self) -> str:
        now_utc = datetime.now(timezone.utc)
        since_utc = now_utc - timedelta(days=7)
        detections, notifications, pending = await asyncio.gather(
            self._repo.count_detections(since=since_utc.replace(tzinfo=None)),
            self._repo.count_notifications_global(since=since_utc.replace(tzinfo=None)),
            self._repo.count_pending_detail(),
        )

        now_local = now_utc.astimezone(self._timezone)
        since_local = since_utc.astimezone(self._timezone)
        lines = [
            "📊 Еженедельный отчёт",
            f"Период: {since_local:%d.%m.%Y} — {now_local:%d.%m.%Y}",
            "",
            f"• Найдено закупок: {detections}",
            f"• Отправлено уведомлений: {notifications}",
            f"• В очереди на детальный разбор: {pending}",
        ]
        return "\n".join(lines)

    async def _broadcast(self, text: str, targets: list[int]) -> None:
        for chat_id in targets:
            try:
                await self._bot.send_message(chat_id=chat_id, text=text)
            except Exception:  # pragma: no cover - defensive
                LOGGER.exception("Failed to send weekly report", extra={"chat_id": chat_id})
