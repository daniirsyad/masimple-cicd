#!/bin/sh
set -e

echo "Waiting for database..."
python <<'PY'
import os
import sys
import time

import sqlalchemy

url = os.environ["DATABASE_URL"]
for attempt in range(30):
    try:
        sqlalchemy.create_engine(url).connect().close()
        break
    except Exception:
        time.sleep(1)
else:
    sys.exit("Database not reachable after 30 attempts, giving up.")
PY

echo "Running database migrations..."
flask db upgrade

echo "Seeding initial data..."
python seeds/seed_admin.py
python seeds/seed_menu.py

echo "Starting application..."
exec gunicorn --bind 0.0.0.0:8000 --workers 3 run:app
