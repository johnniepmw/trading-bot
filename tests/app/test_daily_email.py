"""The daily email's sender: secret parsing and SMTP, against a fake server (no network)."""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

import pytest

from tradingbot.app.calendar import TradingCalendar
from tradingbot.app.daily_email import (
    SMTP_HOST,
    SMTP_PORT,
    EmailError,
    Sender,
    glance_input,
    read_sender,
    send,
)
from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, Money
from tradingbot.ledger.store import IntentState, Ledger, PositionRecord, PreviewRecord
from tradingbot.report.glance import GlanceInput
from tradingbot.risk.limits import Limits

FAKE_SECRET = "bot@example.com:abcd efgh ijkl mnop"  # noqa: S105 - made up


def test_the_sender_is_read_from_the_secret_without_spaces(tmp_path: Path) -> None:
    (tmp_path / "value").write_text(FAKE_SECRET + "\n")
    assert read_sender(tmp_path / "value") == Sender("bot@example.com", "abcdefghijklmnop")


@pytest.mark.parametrize(
    "value", ["", "__unset__", "bot@example.com", "bot.example.com:pw", "bot@example.com:", "a b@x.com:pw"]
)
def test_a_bad_secret_is_refused_without_echoing_it(tmp_path: Path, value: str) -> None:
    (tmp_path / "value").write_text(value)
    with pytest.raises(EmailError, match="ADDRESS:APP_PASSWORD") as error:
        read_sender(tmp_path / "value")
    assert "pw" not in str(error.value)


def test_a_missing_secret_is_an_email_error(tmp_path: Path) -> None:
    with pytest.raises(EmailError, match="unreadable"):
        read_sender(tmp_path / "missing")


def test_the_password_never_shows_in_a_repr() -> None:
    assert "abcdefghijklmnop" not in repr(Sender("bot@example.com", "abcdefghijklmnop"))


class FakeSmtp:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.connected: tuple[str, int, float] | None = None
        self.logins: list[tuple[str, str]] = []
        self.sent: list[EmailMessage] = []
        self.quit_called = False

    def __call__(self, host: str, port: int, timeout: float, context: ssl.SSLContext) -> FakeSmtp:
        assert context.verify_mode is ssl.CERT_REQUIRED
        self.connected = (host, port, timeout)
        return self

    def login(self, user: str, password: str) -> None:
        self.logins.append((user, password))
        if self.fail is not None:
            raise self.fail

    def send_message(self, msg: EmailMessage) -> None:
        self.sent.append(msg)

    def quit(self) -> None:
        self.quit_called = True
        if self.fail is not None:  # as Gmail: after refusing a login it has hung up
            raise smtplib.SMTPServerDisconnected("please run connect() first")


SENDER = Sender("bot@example.com", "abcdefghijklmnop")


def test_an_email_goes_over_tls_to_gmail_from_the_bot() -> None:
    smtp = FakeSmtp()
    send(SENDER, "owner@example.com", "Trading bot (prod): all OK", "[OK] line", connect=smtp)
    assert smtp.connected is not None and smtp.connected[:2] == (SMTP_HOST, SMTP_PORT) == (
        "smtp.gmail.com",
        465,
    )
    assert smtp.logins == [("bot@example.com", "abcdefghijklmnop")]
    (msg,) = smtp.sent
    assert (msg["From"], msg["To"], msg["Subject"]) == (
        "bot@example.com",
        "owner@example.com",
        "Trading bot (prod): all OK",
    )
    assert msg.get_content().strip() == "[OK] line"
    assert smtp.quit_called


@pytest.mark.parametrize(
    "fail", [smtplib.SMTPAuthenticationError(535, b"bad password abcdefghijklmnop"), TimeoutError("slow")]
)
def test_a_refusal_or_timeout_is_an_email_error_naming_only_its_type(fail: Exception) -> None:
    smtp = FakeSmtp(fail)
    with pytest.raises(EmailError) as error:
        send(SENDER, "owner@example.com", "s", "b", connect=smtp)
    code = ", SMTP 535" if isinstance(fail, smtplib.SMTPAuthenticationError) else ""
    assert str(error.value) == f"sending failed at login ({type(fail).__name__}{code})"
    assert "abcdefghijklmnop" not in str(error.value)
    assert smtp.quit_called


@pytest.mark.parametrize("to", ["owner.example.com", "a@x.com, b@y.com", "a@x.com\r\nBcc: c@z.com"])
def test_the_recipient_must_be_one_address(to: str) -> None:
    smtp = FakeSmtp()
    with pytest.raises(EmailError, match="single address"):
        send(SENDER, to, "s", "b", connect=smtp)
    assert smtp.connected is None


# --- the glance's figures, read from a real ledger -----------------------------------
VWRP, SGLN, EMIM = (
    Instrument("VWRP", Currency.GBP),
    Instrument("SGLN", Currency.GBX),
    Instrument("EMIM", Currency.GBX),
)
UNIVERSE = {VWRP: Decimal("0.001"), SGLN: Decimal("0.001"), EMIM: Decimal("0.001")}
TODAY = date(2026, 11, 24)
START = datetime(2026, 11, 24, 7, 15, tzinfo=UTC)
END = datetime(2026, 11, 24, 7, 17, tzinfo=UTC)
CAL = TradingCalendar(frozenset(), until=date(2027, 12, 31))


def gbp(x: str) -> Money:
    return Money(Decimal(x), Currency.GBP)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    led = Ledger.create(tmp_path / "ledger.sqlite")
    led.seed_high_water_mark(gbp("1000"), datetime(2026, 11, 20, 9, tzinfo=UTC))
    led.update_high_water_mark(gbp("1050"), datetime(2026, 11, 21, 9, tzinfo=UTC))
    led.record_equity(date(2026, 11, 23), gbp("1040"))  # yesterday: today's day start
    led.record_equity(TODAY, gbp("1020"))
    led.start_run("run-2", START, "v")
    led.record_snapshot(
        "run-2",
        START,
        gbp("400"),
        [
            PositionRecord(VWRP, Decimal("3"), Money(Decimal(100), Currency.GBP), gbp("300")),
            PositionRecord(SGLN, Decimal("8"), Money(Decimal(4000), Currency.GBX), gbp("320")),
            PositionRecord(EMIM, Decimal("0"), None, None),
        ],
        equity_checked=True,
    )
    earlier = led.record_intent(
        "run-1", Order(VWRP, Side.BUY, Decimal(1)), datetime(2026, 11, 23, 7, 15, tzinfo=UTC)
    )
    led.mark_sent(earlier, "100")  # still open from yesterday
    filled = led.record_intent("run-2", Order(SGLN, Side.BUY, Decimal(1)), START)
    led.settle(filled, IntentState.FILLED, "101", Decimal(1))
    waiting = led.record_intent("run-2", Order(VWRP, Side.BUY, Decimal(1)), START)
    led.mark_sent(waiting, "102")  # sent before the open
    unsent = led.record_intent("run-2", Order(EMIM, Side.BUY, Decimal(1)), START)
    led.settle(unsent, IntentState.NOT_SENT)
    led.record_preview(
        "run-2",
        [
            PreviewRecord(TODAY, VWRP, True, 0.25),
            PreviewRecord(TODAY, SGLN, True, 0.25),
            PreviewRecord(TODAY, EMIM, False, 0.0),
        ],
    )
    led.finish_run("run-2", END, "completed", None)
    yield led
    led.close()


def facts(ledger: Ledger, run_id: str = "run-2", stopped: str | None = None) -> GlanceInput:
    return glance_input(
        ledger,
        run_id,
        today=TODAY,
        calendar=CAL,
        universe=UNIVERSE,
        limits=Limits(),
        now=END,
        stopped=stopped,
    )


def test_every_figure_comes_from_the_ledger(ledger: Ledger) -> None:
    g = facts(ledger)
    assert (g.as_of, g.outcome, g.reason) == (END, "completed", None)
    assert (g.equity, g.day_start, g.mark, g.seed) == (gbp("1020"), gbp("1040"), gbp("1050"), gbp("1000"))
    assert (g.cash, g.invested, g.snapshot_checked) == (gbp("400"), gbp("620"), True)
    assert (g.funds_held, g.funds_total) == (2, 3)  # the zero position doesn't count
    assert (g.sent_today, g.awaiting_fill, g.open_orders) == (2, 1, 1)  # NOT_SENT isn't sent
    assert (g.next_month_end, g.in_trend) == (date(2026, 11, 30), 2)
    assert (g.daily_loss_limit, g.drawdown_halve, g.drawdown_stop) == (
        Decimal("0.02"),
        Decimal("0.10"),
        Decimal("0.20"),
    )


def test_the_day_start_is_the_risk_layers_five_day_window(ledger: Ledger) -> None:
    g = glance_input(
        ledger, "run-2", today=date(2026, 11, 30), calendar=CAL, universe=UNIVERSE, limits=Limits(), now=END
    )
    assert g.day_start is None  # nothing in the 5 days before: buys were paused


def test_the_runs_own_stop_wins_over_its_record(ledger: Ledger) -> None:
    g = facts(ledger, stopped="ledger not saved")
    assert (g.outcome, g.reason) == ("stopped", "ledger not saved")


def test_a_run_without_a_snapshot_preview_or_record(ledger: Ledger) -> None:
    g = facts(ledger, run_id="run-3")
    assert (g.outcome, g.reason) == ("stopped", "no run record")
    assert (g.cash, g.invested, g.snapshot_checked, g.funds_held) == (None, None, None, 0)
    assert g.in_trend is None


def test_an_unchecked_snapshot_is_reported(ledger: Ledger) -> None:
    ledger.record_snapshot("run-4", END, gbp("1"), [], equity_checked=False)
    assert facts(ledger, run_id="run-4").snapshot_checked is False


def test_a_failed_connection_names_the_stage() -> None:
    def refuse(*_: object) -> FakeSmtp:
        raise ConnectionRefusedError("no route")

    with pytest.raises(EmailError, match=r"^sending failed at connect \(ConnectionRefusedError\)$"):
        send(SENDER, "owner@example.com", "s", "b", connect=refuse)


@pytest.mark.parametrize(
    "password", ["abcdefghijklmnopq", "abcdefghijklmno", "abcdefghijklmno.", "abcdefgh1jklmnop"]
)
def test_an_app_password_that_isnt_16_letters_is_named_clearly(tmp_path: Path, password: str) -> None:
    (tmp_path / "value").write_text(f"bot@example.com:{password}")
    with pytest.raises(EmailError, match="16 letters") as error:
        read_sender(tmp_path / "value")
    assert password not in str(error.value)
    assert not any(ch.isdigit() for ch in str(error.value).replace("16", ""))  # no length either
