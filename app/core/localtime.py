"""Restaurant-local dates. The server runs in UTC but restaurants are in India,
so "today" must start at local midnight (00:00 IST = 18:30 UTC the day before),
matching the nightly report."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.core.config import settings

TZ = ZoneInfo(settings.TIMEZONE)


def local_today() -> date:
    return datetime.now(TZ).date()


def local_midnight_utc(d: date | None = None) -> datetime:
    """Aware UTC datetime for 00:00 local time on day d (default today)."""
    return datetime.combine(d or local_today(), time.min, tzinfo=TZ).astimezone(timezone.utc)


def local_date_col(col):
    """SQL expression: a timestamptz column as a local calendar date."""
    return func.date(func.timezone(settings.TIMEZONE, col))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["TZ", "local_today", "local_midnight_utc", "local_date_col", "utc_now", "timedelta"]
