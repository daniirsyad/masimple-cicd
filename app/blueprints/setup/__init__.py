from flask import Blueprint

setup_bp = Blueprint("setup", __name__)

from app.blueprints.setup import routes  # noqa: E402,F401
