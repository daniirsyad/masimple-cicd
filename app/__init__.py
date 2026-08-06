import os
import uuid

from dotenv import load_dotenv
from flask import Flask
from flask_login import current_user

load_dotenv()

from config import config  # noqa: E402  must load after dotenv so env vars are set
from app.extensions import csrf, db, login_manager, migrate  # noqa: E402
from app.utils.menu_builder import menu_builder  # noqa: E402


def create_app(config_name=None):
    """Application factory."""
    config_name = config_name or os.environ.get("FLASK_ENV", "default")

    app = Flask(__name__)
    app.config.from_object(config[config_name])

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"

    from app import models  # noqa: F401  ensure models are registered for migrations

    @login_manager.user_loader
    def load_user(user_id):
        return models.User.query.get(uuid.UUID(user_id))

    @app.context_processor
    def inject_menus():
        return menu_builder(current_user)

    from app.blueprints.auth import auth_bp
    from app.blueprints.main import main_bp
    from app.blueprints.users import users_bp
    from app.blueprints.roles import roles_bp
    from app.blueprints.logs import logs_bp
    from app.blueprints.menus import menus_bp
    from app.blueprints.permissions import permissions_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(users_bp, url_prefix="/users")
    app.register_blueprint(roles_bp, url_prefix="/roles")
    app.register_blueprint(logs_bp, url_prefix="/logs")
    app.register_blueprint(menus_bp, url_prefix="/menus")
    app.register_blueprint(permissions_bp, url_prefix="/permissions")

    return app
