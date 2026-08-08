from flask import Blueprint

deployment_pods_bp = Blueprint("deployment_pods", __name__)

from app.blueprints.deployment_pods import routes  # noqa: E402,F401
