from flask import Blueprint

versions_bp = Blueprint("versions", __name__)

from app.blueprints.versions import routes  # noqa: E402,F401
