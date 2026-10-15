"""The current time and date, in one place.

- now():   the exact moment, in UTC. Used for timestamps (created, expires...).
- local(): a stored UTC time shown in campus time (for emails).
- today(): the calendar date on campus (India). Used for checks like
           "the found date can't be in the future". The server itself may
           run in another time zone (e.g. a cloud server in UTC), so we
           convert explicitly instead of trusting the server's clock zone.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def now() -> datetime:
    return datetime.now(UTC)


def today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def local(moment: datetime) -> datetime:
    return moment.astimezone(ZoneInfo(get_settings().timezone))
