import os
import tempfile

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Base configuration shared across all environments."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True

    # Persistent clones of registered repos live here, one subdirectory per
    # Repository id (see "Repo registration & persistent clones"). Kept
    # outside app/static so it's never served publicly. Defaults to a path
    # inside this app's own directory tree (not a separate top-level path)
    # so all app data lives in one place — mount external/persistent storage
    # onto this directory yourself if you need it to survive container
    # recreation; nothing here assumes a particular mount is present.
    REPO_CLONE_ROOT = os.environ.get("REPO_CLONE_ROOT", os.path.join(basedir, "data", "repos"))


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
