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
