"""Example of the functional core: pure, no I/O, trivially testable. Replace during bootstrap."""

MAX_NAME_LENGTH = 40


def greeting(name: str | None) -> str:
    cleaned = (name or "").strip()[:MAX_NAME_LENGTH]
    return f"Hello, {cleaned}!" if cleaned else "Hello, world!"
