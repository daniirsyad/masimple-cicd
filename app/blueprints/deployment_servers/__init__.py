from flask import Blueprint

deployment_servers_bp = Blueprint("deployment_servers", __name__)

from app.blueprints.deployment_servers import routes  # noqa: E402,F401
