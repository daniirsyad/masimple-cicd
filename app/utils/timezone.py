from datetime import datetime
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.utils.system_config import get_system_config

DEFAULT_FORMAT = "%Y-%m-%d %H:%M:%S"


def to_local(dt):
    """Convert a naive UTC datetime (how every model in this app stores
    timestamps) to the configured system timezone. Returns None for None.
    """
    if dt is None:
        return None

    try:
        tz = ZoneInfo(get_system_config().timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    return dt.replace(tzinfo=dt_timezone.utc).astimezone(tz)


def format_local(dt, fmt=DEFAULT_FORMAT):
    """Jinja filter: `{{ some_dt | localtime }}` or `{{ some_dt | localtime("%Y-%m-%d") }}`."""
    local = to_local(dt)
    return local.strftime(fmt) if local else ""


def time_ago(dt, now=None):
    """Jinja filter: `{{ some_dt | timeago }}` -> "just now", "5 min ago",
    "2 hours ago", "yesterday", "4 days ago", or the local date after a
    week. `dt` is naive UTC like every model timestamp; pair it with
    `localtime` in a title attribute for the exact time."""
    if dt is None:
        return ""
    seconds = int(((now or datetime.utcnow()) - dt).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'' if hours == 1 else 's'} ago"
    days = hours // 24
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    return format_local(dt, "%Y-%m-%d")

