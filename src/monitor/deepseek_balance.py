from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from aiogram import Bot

from ..config import DeepSeekConfig, LoggingConfig
from ..db.repo import Repository
from ..tg.auth_state import AuthState

LOGGER = logging.getLogger(__name__)


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


@dataclass(slots=True)
class DeepSeekBalanceInfo:
    currency: str
    total_balance: Decimal | None
    granted_balance: Decimal | None
    topped_up_balance: Decimal | None

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "total_balance": str(self.total_balance) if self.total_balance is not None else None,
            "granted_balance": str(self.granted_balance) if self.granted_balance is not None else None,
            "topped_up_balance": str(self.topped_up_balance) if self.topped_up_balance is not None else None,
        }


@dataclass(slots=True)
class DeepSeekBalanceReport:
    checked_at: datetime
    is_available: bool
    status: str
    low_threshold: Decimal
    balances: list[DeepSeekBalanceInfo]

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at.isoformat(),
            "is_available": self.is_available,
            "status": self.status,
            "low_threshold": str(self.low_threshold),
            "balances": [item.as_snapshot() for item in self.balances],
        }


class DeepSeekBalanceClient:
    def __init__(self, config: DeepSeekConfig) -> None:
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def get_balance(self) -> dict[str, Any]:
        session = await self._ensure_session()
        async with session.get(self._config.balance_api_url) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(f"DeepSeek balance API returned {response.status}: {body[:500]}")
            data = await response.json()
            if not isinstance(data, dict):
                raise RuntimeError("DeepSeek balance API returned malformed payload")
            return data

    async def _ensure_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None:
                if not self._config.api_key:
                    raise RuntimeError("DeepSeek API key is not configured")
                timeout = aiohttp.ClientTimeout(total=self._config.timeout_seconds)
                headers = {
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Accept": "application/json",
                }
                self._session = aiohttp.ClientSession(timeout=timeout, headers=headers)
            return self._session


class DeepSeekBalanceService:
    def __init__(
        self,
        *,
        client: DeepSeekBalanceClient,
        repository: Repository,
        bot: Bot,
        auth_state: AuthState,
        deepseek_config: DeepSeekConfig,
        logging_config: LoggingConfig,
    ) -> None:
        self._client = client
        self._repo = repository
        self._bot = bot
        self._auth_state = auth_state
        self._config = deepseek_config
        self._timezone = ZoneInfo(logging_config.timezone)

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled and self._config.api_key and self._config.balance_check_enabled)

    async def get_report(self) -> DeepSeekBalanceReport:
        payload = await self._client.get_balance()
        return self._build_report(payload)

    async def run_check(self) -> None:
        if not self.enabled:
            return
        try:
            report = await self.get_report()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Failed to fetch DeepSeek balance")
            return

        state = await self._repo.get_balance_alert_state()
        await self._repo.update_balance_alert_state(
            last_checked_at=report.checked_at,
            last_snapshot=report.as_snapshot(),
        )
        if report.status == "ok":
            return

        today = report.checked_at.astimezone(self._timezone).date().isoformat()
        if state.last_alert_date == today and state.last_alert_status == report.status:
            LOGGER.debug("DeepSeek balance alert skipped: already sent today", extra={"status": report.status})
            return

        targets = self._auth_state.all_targets()
        if not targets:
            LOGGER.debug("DeepSeek balance alert skipped: no authorized targets")
            return

        text = self.format_alert_message(report)
        sent = False
        for target in targets:
            try:
                await self._bot.send_message(chat_id=target, text=text, disable_web_page_preview=True)
                sent = True
            except Exception:
                LOGGER.exception("Failed to send DeepSeek balance alert", extra={"chat_id": target})

        if sent:
            await self._repo.update_balance_alert_state(
                last_checked_at=report.checked_at,
                last_snapshot=report.as_snapshot(),
                last_alert_date=today,
                last_alert_status=report.status,
            )

    def format_status_message(self, report: DeepSeekBalanceReport) -> str:
        lines = [
            "Баланс DeepSeek",
            f"Доступность API: {'доступен' if report.is_available else 'недоступен'}",
            f"Статус: {self._status_label(report.status)}",
            f"Порог предупреждения: {self._format_decimal(report.low_threshold)}",
        ]
        if report.balances:
            lines.append("")
            lines.append("Счета:")
            for item in report.balances:
                lines.append(
                    "• "
                    f"{item.currency}: total={self._format_decimal(item.total_balance)}, "
                    f"granted={self._format_decimal(item.granted_balance)}, "
                    f"topped_up={self._format_decimal(item.topped_up_balance)}"
                )
        else:
            lines.append("")
            lines.append("Счета: API не вернул данные по balance_infos.")
        return "\n".join(lines)

    def format_alert_message(self, report: DeepSeekBalanceReport) -> str:
        title = "DeepSeek: баланс закончился" if report.status == "exhausted" else "DeepSeek: низкий баланс"
        lines = [title]
        if report.status == "exhausted":
            lines.append("API сообщает, что баланс недоступен или исчерпан.")
        else:
            lines.append(
                f"Баланс опустился до порога {self._format_decimal(report.low_threshold)} "
                "или ниже."
            )
        if report.balances:
            lines.append("")
            lines.append("Текущие значения:")
            for item in report.balances:
                marker = ""
                if item.total_balance is not None and item.total_balance <= report.low_threshold:
                    marker = " [внимание]"
                lines.append(f"• {item.currency}: total={self._format_decimal(item.total_balance)}{marker}")
        return "\n".join(lines)

    def _build_report(self, payload: dict[str, Any]) -> DeepSeekBalanceReport:
        is_available = bool(payload.get("is_available"))
        raw_balances = payload.get("balance_infos")
        balances: list[DeepSeekBalanceInfo] = []
        if isinstance(raw_balances, list):
            for entry in raw_balances:
                if not isinstance(entry, dict):
                    continue
                balances.append(
                    DeepSeekBalanceInfo(
                        currency=str(entry.get("currency") or "unknown"),
                        total_balance=_parse_decimal(entry.get("total_balance")),
                        granted_balance=_parse_decimal(entry.get("granted_balance")),
                        topped_up_balance=_parse_decimal(entry.get("topped_up_balance")),
                    )
                )

        threshold = Decimal(str(self._config.balance_low_threshold))
        status = "ok"
        if not is_available:
            status = "exhausted"
        elif any(
            balance.total_balance is not None and balance.total_balance <= threshold
            for balance in balances
        ):
            status = "low"

        return DeepSeekBalanceReport(
            checked_at=datetime.now(self._timezone),
            is_available=is_available,
            status=status,
            low_threshold=threshold,
            balances=balances,
        )

    @staticmethod
    def _format_decimal(value: Decimal | None) -> str:
        if value is None:
            return "n/a"
        return format(value.normalize(), "f")

    @staticmethod
    def _status_label(status: str) -> str:
        mapping = {
            "ok": "норма",
            "low": "низкий баланс",
            "exhausted": "баланс исчерпан",
        }
        return mapping.get(status, status)
