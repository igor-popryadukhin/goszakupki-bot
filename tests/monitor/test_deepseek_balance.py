from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from src.config import DeepSeekConfig, LoggingConfig
from src.monitor.deepseek_balance import DeepSeekBalanceService


class DummyClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def get_balance(self) -> dict:
        return self.payload


@dataclass
class DummyState:
    last_checked_at: datetime | None = None
    last_alert_date: str | None = None
    last_alert_status: str | None = None
    last_snapshot: dict | None = None


class DummyRepo:
    def __init__(self, state: DummyState | None = None) -> None:
        self.state = state or DummyState()
        self.updates: list[dict] = []

    async def get_balance_alert_state(self) -> DummyState:
        return self.state

    async def update_balance_alert_state(self, **kwargs) -> None:
        self.updates.append(kwargs)
        self.state.last_checked_at = kwargs["last_checked_at"]
        self.state.last_snapshot = kwargs["last_snapshot"]
        if "last_alert_date" in kwargs:
            self.state.last_alert_date = kwargs["last_alert_date"]
        if "last_alert_status" in kwargs:
            self.state.last_alert_status = kwargs["last_alert_status"]


class DummyBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, disable_web_page_preview: bool = True) -> None:
        self.messages.append((chat_id, text))


class DummyAuthState:
    def __init__(self, targets: list[int]) -> None:
        self._targets = targets

    def all_targets(self) -> list[int]:
        return list(self._targets)


def make_service(payload: dict, *, threshold: float = 5.0, state: DummyState | None = None) -> tuple[DeepSeekBalanceService, DummyRepo, DummyBot]:
    repo = DummyRepo(state=state)
    bot = DummyBot()
    service = DeepSeekBalanceService(
        client=DummyClient(payload),
        repository=repo,
        bot=bot,
        auth_state=DummyAuthState([1001, 1002]),
        deepseek_config=DeepSeekConfig(
            api_key="secret",
            enabled=True,
            balance_check_enabled=True,
            balance_low_threshold=threshold,
        ),
        logging_config=LoggingConfig(level="INFO", timezone="UTC"),
    )
    return service, repo, bot


def test_build_report_marks_low_balance() -> None:
    service, _, _ = make_service(
        {
            "is_available": True,
            "balance_infos": [
                {"currency": "USD", "total_balance": "4.50", "granted_balance": "0", "topped_up_balance": "4.50"}
            ],
        }
    )

    report = asyncio.run(service.get_report())

    assert report.status == "low"
    assert report.balances[0].currency == "USD"
    assert report.balances[0].total_balance is not None
    assert str(report.balances[0].total_balance) == "4.50"


def test_build_report_marks_exhausted_when_api_unavailable() -> None:
    service, _, _ = make_service(
        {
            "is_available": False,
            "balance_infos": [
                {"currency": "USD", "total_balance": "100", "granted_balance": "0", "topped_up_balance": "100"}
            ],
        }
    )

    report = asyncio.run(service.get_report())

    assert report.status == "exhausted"


def test_run_check_sends_alert_once_per_day() -> None:
    today = datetime.now().date().isoformat()
    state = DummyState(last_alert_date=today, last_alert_status="low")
    service, repo, bot = make_service(
        {
            "is_available": True,
            "balance_infos": [
                {"currency": "USD", "total_balance": "2", "granted_balance": "0", "topped_up_balance": "2"}
            ],
        },
        state=state,
    )

    asyncio.run(service.run_check())

    assert bot.messages == []
    assert len(repo.updates) == 1
    assert "last_alert_date" not in repo.updates[0]


def test_status_message_lists_all_balances() -> None:
    service, _, _ = make_service(
        {
            "is_available": True,
            "balance_infos": [
                {"currency": "USD", "total_balance": "7", "granted_balance": "1", "topped_up_balance": "6"},
                {"currency": "CNY", "total_balance": "20", "granted_balance": "5", "topped_up_balance": "15"},
            ],
        }
    )

    report = asyncio.run(service.get_report())
    message = service.format_status_message(report)

    assert "Баланс DeepSeek" in message
    assert "USD: total=7" in message
    assert "CNY: total=20" in message
