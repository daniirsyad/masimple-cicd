from flask import Blueprint

permissions_bp = Blueprint("permissions", __name__)

from app.blueprints.permissions import routes  # noqa: E402,F401
