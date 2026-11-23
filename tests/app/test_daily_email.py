"""The daily email's sender: secret parsing and SMTP, against a fake server (no network)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import pytest

from tradingbot.app.daily_email import SMTP_HOST, SMTP_PORT, EmailError, Sender, read_sender, send

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
    assert str(error.value) == f"sending failed ({type(fail).__name__})"
    assert smtp.quit_called


@pytest.mark.parametrize("to", ["owner.example.com", "a@x.com, b@y.com", "a@x.com\r\nBcc: c@z.com"])
def test_the_recipient_must_be_one_address(to: str) -> None:
    smtp = FakeSmtp()
    with pytest.raises(EmailError, match="single address"):
        send(SENDER, to, "s", "b", connect=smtp)
    assert smtp.connected is None
