from flask import Blueprint

git_sources_bp = Blueprint("git_sources", __name__)

from app.blueprints.git_sources import routes  # noqa: E402,F401
