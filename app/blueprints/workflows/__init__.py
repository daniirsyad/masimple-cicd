from flask import Blueprint

workflows_bp = Blueprint("workflows", __name__)

from app.blueprints.workflows import routes  # noqa: E402,F401
