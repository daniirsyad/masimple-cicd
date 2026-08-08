from flask import Blueprint

deployment_runs_bp = Blueprint("deployment_runs", __name__)

from app.blueprints.deployment_runs import routes  # noqa: E402,F401
