from flask import Blueprint

system_config_bp = Blueprint("system_config", __name__)

from app.blueprints.system_config import routes  # noqa: E402,F401
