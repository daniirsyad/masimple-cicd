"""Seeds the default version-documentation change types.

Safe to re-run: existing change types (matched by name) are detected and left untouched.

Usage:
    python seeds/seed_change_types.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import ChangeType

CHANGE_TYPES = [
    "New Program",
    "Update",
    "Bug Fix",
    "Hot Fix",
    "Cherry Pick",
]


def get_or_create(name):
    change_type = ChangeType.query.filter_by(name=name).first()
    if change_type is None:
        change_type = ChangeType(name=name, is_active=True)
        db.session.add(change_type)
        print(f"Created change type: {name}")
    return change_type


def run():
    app = create_app()
    with app.app_context():
        for name in CHANGE_TYPES:
            get_or_create(name)
        db.session.commit()


if __name__ == "__main__":
    run()
