from flask import Blueprint

menus_bp = Blueprint("menus", __name__)

from app.blueprints.menus import routes  # noqa: E402,F401
