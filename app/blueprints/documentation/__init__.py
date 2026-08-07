from flask import Blueprint

documentation_bp = Blueprint("documentation", __name__)

from app.blueprints.documentation import routes  # noqa: E402,F401
