import os
import uuid
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, got_request_exception, request
from flask_login import current_user

load_dotenv()

from config import config  # noqa: E402  must load after dotenv so env vars are set
from app.extensions import csrf, db, login_manager, migrate  # noqa: E402
from app.utils.error_logger import log_error  # noqa: E402
from app.utils.menu_builder import menu_builder  # noqa: E402
from app.utils.system_config import get_system_config  # noqa: E402
from app.utils.timezone import format_local  # noqa: E402

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
        context = menu_builder(current_user)
        context["system_config"] = get_system_config()
        return context

    app.jinja_env.filters["localtime"] = format_local

    @app.before_request
    def _apply_session_timeout():
        # Skip static assets — no point querying SystemConfig on every CSS/JS/
        # image request. PERMANENT_SESSION_LIFETIME is read fresh by Flask
        # each time the response's session cookie is written, so refreshing it
        # here (before the view runs) keeps it in sync with the DB-configured
        # value without needing a restart when an admin changes it.
        if request.endpoint != "static" and current_user.is_authenticated:
            app.permanent_session_lifetime = timedelta(
                minutes=get_system_config().session_timeout_minutes
            )

    def _log_unhandled_exception(sender, exception, **extra):
        # got_request_exception only fires for genuinely unhandled errors —
        # HTTPExceptions raised via abort()/permission_required (403, 404,
        # ...) are resolved before Flask ever sends this signal, so normal
        # app flow doesn't spam this as "an accident".
        log_error(source=request.endpoint or request.path, exc=exception)

    # weak=False: this is a local closure with no other strong reference —
    # blinker's default weak-reference connection would let it get garbage
    # collected the moment create_app() returns, silently disconnecting it.
    got_request_exception.connect(_log_unhandled_exception, app, weak=False)

    from app.blueprints.auth import auth_bp
    from app.blueprints.main import main_bp
    from app.blueprints.users import users_bp
    from app.blueprints.roles import roles_bp
    from app.blueprints.logs import logs_bp
    from app.blueprints.menus import menus_bp
    from app.blueprints.permissions import permissions_bp
    from app.blueprints.ai_settings import ai_settings_bp
    from app.blueprints.git_sources import git_sources_bp
    from app.blueprints.registries import registries_bp
    from app.blueprints.versions import versions_bp
    from app.blueprints.builders import builders_bp
    from app.blueprints.images import images_bp
    from app.blueprints.documentation import documentation_bp
    from app.blueprints.system_config import system_config_bp
    from app.services.build.worker import start_worker

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(users_bp, url_prefix="/users")
    app.register_blueprint(roles_bp, url_prefix="/roles")
    app.register_blueprint(logs_bp, url_prefix="/logs")
    app.register_blueprint(menus_bp, url_prefix="/menus")
    app.register_blueprint(permissions_bp, url_prefix="/permissions")
    app.register_blueprint(ai_settings_bp, url_prefix="/ai-settings")
    app.register_blueprint(git_sources_bp, url_prefix="/github")
    app.register_blueprint(registries_bp, url_prefix="/registries")
    app.register_blueprint(versions_bp, url_prefix="/versions")
    app.register_blueprint(builders_bp, url_prefix="/builders")
    app.register_blueprint(images_bp, url_prefix="/images")
    app.register_blueprint(documentation_bp, url_prefix="/documentation")
    app.register_blueprint(system_config_bp, url_prefix="/config")

    start_worker(app)

    return app
