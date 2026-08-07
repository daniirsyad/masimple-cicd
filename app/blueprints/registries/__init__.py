from flask import Blueprint

registries_bp = Blueprint("registries", __name__)

from app.blueprints.registries import routes  # noqa: E402,F401
