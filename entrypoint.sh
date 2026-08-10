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
python seeds/seed_change_types.py
python seeds/seed_version_types.py
python seeds/seed_ai_provider.py
python seeds/seed_system_config.py

echo "Starting application..."
# --worker-class gthread + --threads: plain sync workers (the previous
# setting) each block entirely on one request at a time, so a single open
# pod-logs SSE connection (see app/blueprints/deployment_pods/routes.py
# pod_logs_stream) would pin a whole worker for as long as the modal stays
# open — with 3 workers, 3 concurrently open log views would leave nothing
# to serve anyone else. gthread lets each worker hold several requests
# concurrently via a thread pool; safe here because the long-lived request
# (reading a subprocess's stdout line by line) is I/O-bound and releases the
# GIL while blocked, same as any other blocking read. --timeout is bumped
# well past the default 30s so a quiet-but-open log stream isn't mistaken
# for a hung worker.
exec gunicorn --bind 0.0.0.0:8000 --workers 3 --worker-class gthread --threads 4 --timeout 120 run:app
