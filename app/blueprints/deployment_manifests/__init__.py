from flask import Blueprint

deployment_manifests_bp = Blueprint("deployment_manifests", __name__)

from app.blueprints.deployment_manifests import routes  # noqa: E402,F401
