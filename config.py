import os
import tempfile

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Base configuration shared across all environments."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True

    # Browsers keep static files for a year without asking again: every
    # url_for('static') carries ?v=<file mtime> (app/__init__.py), so a
    # changed file gets a new URL. Without this, each page change under
    # hx-boost re-checked every page <script> with the server.
    SEND_FILE_MAX_AGE_DEFAULT = 31536000

    # Per gunicorn process: 8 request threads (entrypoint.sh) plus the
    # background worker threads, each of which can hold a connection.
    # SQLAlchemy's default (5 + 10 overflow) would leave requests waiting for
    # one. 3 processes x 20 stays well under Postgres' default 100.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_size": 10, "max_overflow": 10}

    # Persistent clones of registered repos live here, one subdirectory per
    # Repository id (see "Repo registration & persistent clones"). Kept
    # outside app/static so it's never served publicly. Defaults to a path
    # inside this app's own directory tree (not a separate top-level path)
    # so all app data lives in one place — mount external/persistent storage
    # onto this directory yourself if you need it to survive container
    # recreation; nothing here assumes a particular mount is present.
    REPO_CLONE_ROOT = os.environ.get("REPO_CLONE_ROOT", os.path.join(basedir, "data", "repos"))

    # How many reverse proxies (nginx, Traefik, Cloudflare...) sit in front of
    # the app. Their X-Forwarded-Proto/Host/For headers are trusted, so behind
    # an HTTPS proxy redirects and url_for() say https:// (else the browser
    # blocks htmx's requests as mixed content) and logs show the real client
    # IP. Set 0 if clients reach gunicorn directly, or they could fake their IP.
    TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "1"))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    DEBUG = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", os.environ.get("DATABASE_URL"))
    # Never touch the real app-local clone directory during tests.
    REPO_CLONE_ROOT = os.path.join(tempfile.gettempdir(), "masimple-cicd-test-repos")


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}
