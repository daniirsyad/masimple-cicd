from flask import Blueprint

dockerfiles_bp = Blueprint("dockerfiles", __name__)

from app.blueprints.dockerfiles import routes  # noqa: E402,F401
