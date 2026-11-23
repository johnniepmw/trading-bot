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
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from tradingbot.report.glance import GlanceInput, glance, render

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
            "the stored app password isn't Google's 16 letters (spaces ignored): store it again"
            " (secret gmail-sender)"
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


def compose(g: GlanceInput, environment: str) -> tuple[str, str]:
    return render(glance(g), as_of=g.as_of, environment=environment)
