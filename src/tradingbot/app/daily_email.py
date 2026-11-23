"""The daily email (monitoring step 5; docs/exec-plans/active/2026-11-23-daily-email.md).

After a scheduled run that opened the ledger, the job gathers the glance's figures from the
ledger it just wrote and sends seven lines from the bot's own Gmail account (owner's choice)
over SMTP with TLS. The sender's address and app password come from the mounted secret
`gmail-sender` (`ADDRESS:APP_PASSWORD`); the recipient from `EMAIL_TO`, which OpenTofu sets
from the bootstrap's alert email, so no address is written in this public repository.
Nothing here may change a run's outcome: the job catches every failure (`email_failed`).
"""

from __future__ import annotations

import contextlib
import re
import smtplib
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from tradingbot.app.calendar import TradingCalendar
from tradingbot.domain.market import Instrument
from tradingbot.domain.money import Currency, Money
from tradingbot.ledger.store import IntentState, Ledger, LedgerError
from tradingbot.report.glance import GlanceInput, glance, render
from tradingbot.risk.limits import Limits

SENDER_PATH = Path("/secrets/gmail-sender/value")  # mounted in staging and prod (infra)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # implicit TLS
TIMEOUT_SECONDS = 30.0


class EmailError(Exception):
    """The email couldn't be prepared or sent. Messages never hold the password."""


@dataclass(frozen=True, slots=True)
class Sender:
    address: str
    password: str

    def __repr__(self) -> str:  # never print the password
        return f"Sender({self.address!r})"


def read_sender(path: Path | None = None) -> Sender:
    try:
        value = (path or SENDER_PATH).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise EmailError(f"email sender unreadable ({type(error).__name__})") from error
    address, sep, password = value.partition(":")
    password = password.replace(" ", "")
    if (
        not sep
        or "@" not in address
        or " " in address
        or not password
        or not value.isprintable()
        or not value.isascii()
    ):
        raise EmailError("email sender missing or not ADDRESS:APP_PASSWORD (secret gmail-sender)")
    if not re.fullmatch(r"[A-Za-z]{16}", password):  # Google's app passwords: 16 letters
        raise EmailError(
            "the app password must be Google's 16 letters (spaces ignored); the stored one has"
            f" {len(password)} characters, or ones that aren't letters (secret gmail-sender)"
        )
    return Sender(address, password)


class Smtp(Protocol):
    def login(self, user: str, password: str) -> object: ...
    def send_message(self, msg: EmailMessage) -> object: ...
    def quit(self) -> object: ...


Connect = Callable[[str, int, float, ssl.SSLContext], Smtp]


def _smtp_ssl(host: str, port: int, timeout: float, context: ssl.SSLContext) -> Smtp:
    return smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)


def send(sender: Sender, to: str, subject: str, body: str, connect: Connect = _smtp_ssl) -> None:
    if "@" not in to or any(c in to for c in "\r\n,; "):
        raise EmailError("EMAIL_TO is not a single address")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender.address, to, subject
    msg.set_content(body)
    stage = "connect"
    try:
        smtp = connect(SMTP_HOST, SMTP_PORT, TIMEOUT_SECONDS, ssl.create_default_context())
        try:
            stage = "login"
            smtp.login(sender.address, sender.password)
            stage = "send"
            smtp.send_message(msg)
        finally:
            _quit_quietly(smtp)  # a server that already hung up mustn't hide the real error
    except (smtplib.SMTPException, OSError) as error:
        code = getattr(error, "smtp_code", None)  # e.g. 535: login refused; never the server's text
        detail = f", SMTP {code}" if isinstance(code, int) else ""
        raise EmailError(f"sending failed at {stage} ({type(error).__name__}{detail})") from error


def _quit_quietly(smtp: Smtp) -> None:
    with contextlib.suppress(smtplib.SMTPException, OSError):
        smtp.quit()


def _gbp(amount: Decimal) -> Money:
    return Money(amount, Currency.GBP)


def glance_input(  # noqa: PLR0913 - the run's facts and the config the lines compare with
    ledger: Ledger,
    run_id: str,
    *,
    today: date,
    calendar: TradingCalendar,
    universe: Mapping[Instrument, Decimal],
    limits: Limits,
    now: datetime,
    stopped: str | None = None,
) -> GlanceInput:
    """The glance's figures from the ledger. `stopped` is the run's own stop reason, which
    wins over the run record (a final save that failed leaves the record "completed")."""
    run = next((r for r in ledger.runs() if r.run_id == run_id), None)
    try:
        mark = ledger.high_water_mark()
    except LedgerError:
        mark = None
    try:
        snapshot = ledger.snapshot(run_id)
    except LedgerError:
        snapshot = None
    held = [p for p in snapshot.positions if p.quantity > 0] if snapshot else []
    preview = ledger.preview(run_id)
    open_intents = ledger.open_intents()
    intents = open_intents + ledger.settled()
    sent_today = sum(
        1 for i in intents if i.created_at.date() == today and i.state is not IntentState.NOT_SENT
    )
    awaiting = [i for i in open_intents if i.run_id == run_id and i.state is IntentState.SENT]
    return GlanceInput(
        as_of=run.ended_at if run and run.ended_at else now,
        outcome="stopped" if stopped else (run.outcome if run and run.outcome else "stopped"),
        reason=stopped or (run.reason if run else "no run record"),
        equity=ledger.equity_on(today),
        day_start=ledger.previous_day_equity(today),
        mark=mark,
        seed=ledger.first_high_water_mark(),
        cash=snapshot.cash if snapshot else None,
        invested=_gbp(sum((p.value_gbp.amount for p in held if p.value_gbp), Decimal(0)))
        if snapshot
        else None,
        funds_held=len(held),
        funds_total=len(universe),
        snapshot_checked=snapshot.equity_checked if snapshot else None,
        sent_today=sent_today,
        awaiting_fill=len(awaiting),
        open_orders=len(open_intents) - len(awaiting),
        next_month_end=calendar.next_month_end(today),
        in_trend=sum(1 for p in preview if p.in_trend) if preview else None,
        daily_loss_limit=limits.daily_loss,
        drawdown_halve=limits.drawdown_halve,
        drawdown_stop=limits.drawdown_stop,
    )


def compose(g: GlanceInput, environment: str) -> tuple[str, str]:
    return render(glance(g), as_of=g.as_of, environment=environment)
