"""Seeds the default Version Types.

Safe to re-run: existing version types (matched by name) are detected and left untouched.

Usage:
    python seeds/seed_version_types.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import VersionType

VERSION_TYPES = [
    "DEV",
    "STAGING",
    "PROD",
]


def get_or_create(name):
    version_type = VersionType.query.filter_by(name=name).first()
    if version_type is None:
        version_type = VersionType(name=name, is_active=True)
        db.session.add(version_type)
        print(f"Created version type: {name}")
    return version_type


def run():
    app = create_app()
    with app.app_context():
        for name in VERSION_TYPES:
            get_or_create(name)
        db.session.commit()


if __name__ == "__main__":
    run()
