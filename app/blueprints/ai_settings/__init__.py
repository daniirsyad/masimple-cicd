from flask import Blueprint

ai_settings_bp = Blueprint("ai_settings", __name__)

from app.blueprints.ai_settings import routes  # noqa: E402,F401
