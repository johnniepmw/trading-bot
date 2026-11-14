from hypothesis import given
from hypothesis import strategies as st

from app.core.greeting import MAX_NAME_LENGTH, greeting


def test_greets_by_name() -> None:
    assert greeting("Ada") == "Hello, Ada!"


def test_falls_back_for_missing_or_blank_names() -> None:
    assert greeting(None) == "Hello, world!"
    assert greeting("   ") == "Hello, world!"


@given(st.text())
def test_output_is_always_a_bounded_greeting(name: str) -> None:
    result = greeting(name)
    assert result.startswith("Hello, ")
    assert result.endswith("!")
    assert len(result) <= len("Hello, !") + MAX_NAME_LENGTH
