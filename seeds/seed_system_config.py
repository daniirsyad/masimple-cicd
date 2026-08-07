"""Seeds the single SystemConfig row (timezone, session timeout) with defaults.

Safe to re-run: does nothing if a row already exists.

Usage:
    python seeds/seed_system_config.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import SystemConfig


def run():
    app = create_app()
    with app.app_context():
        if SystemConfig.query.count() == 0:
            db.session.add(SystemConfig())
            db.session.commit()
            print("Created default system config (timezone=UTC, session_timeout_minutes=60)")


if __name__ == "__main__":
    run()
