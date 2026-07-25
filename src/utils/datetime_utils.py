"""Timezone-aware date and time helpers."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def kst_timezone():
    """Return Asia/Seoul, falling back to a fixed UTC+9 timezone."""
    try:
        return ZoneInfo("Asia/Seoul")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9), name="KST")


def now_kst() -> datetime:
    """Return the current timezone-aware datetime in South Korea."""
    return datetime.now(kst_timezone())
