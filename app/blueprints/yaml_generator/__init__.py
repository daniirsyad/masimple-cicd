from flask import Blueprint

yaml_generator_bp = Blueprint("yaml_generator", __name__)

from app.blueprints.yaml_generator import routes  # noqa: E402,F401
