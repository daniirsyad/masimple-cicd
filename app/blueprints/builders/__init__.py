from flask import Blueprint

builders_bp = Blueprint("builders", __name__)

from app.blueprints.builders import routes  # noqa: E402,F401
