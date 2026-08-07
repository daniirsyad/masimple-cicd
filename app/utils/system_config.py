from app.extensions import db
from app.models import SystemConfig


def get_system_config():
    """Return the single SystemConfig row, creating it with defaults on first
    access. There's no seed-ordering guarantee callers can rely on (e.g. the
    test suite calls db.create_all() with no seed scripts at all), so every
    caller needs a real row back rather than handling `None`.
    """
    config = SystemConfig.query.first()
    if config is None:
        config = SystemConfig()
        db.session.add(config)
        db.session.commit()
    return config
