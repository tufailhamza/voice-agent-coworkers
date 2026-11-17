"""Shared OpenAI API key loading (strip whitespace/newlines from env secrets)."""

import os


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def get_openai_api_key_v2() -> str | None:
    """OPENAI_API_KEY_2, stripped. None if unset or whitespace-only."""
    return _clean(os.getenv("OPENAI_API_KEY_2"))


def get_openai_api_key_primary(default: str = "") -> str:
    """OPENAI_API_KEY, stripped. Returns default if unset or whitespace-only."""
    return _clean(os.getenv("OPENAI_API_KEY")) or default


def get_openai_api_key_v2_or_primary() -> str | None:
    """OPENAI_API_KEY_2, else OPENAI_API_KEY; both stripped."""
    return get_openai_api_key_v2() or _clean(os.getenv("OPENAI_API_KEY"))
