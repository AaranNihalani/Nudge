"""Shared utilities used across bot modules."""
from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def format_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def normalize_sender(from_addr: str) -> str:
    raw = (from_addr or "").strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw.split(":", 1)[1]
    return raw.strip()


def is_keyword(text: str, *, keyword: str) -> bool:
    return text.strip().lower() == keyword.lower()


def starts_with_keyword(text: str, *, keyword: str) -> bool:
    return text.strip().lower().startswith(keyword.lower())


def strip_prefix(raw: str, prefix: str) -> str:
    remaining = raw[len(prefix):].strip()
    while remaining.startswith(":") or remaining.startswith(","):
        remaining = remaining[1:].strip()
    return remaining
