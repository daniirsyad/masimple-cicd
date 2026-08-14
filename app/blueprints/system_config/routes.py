import uuid

from flask import flash, redirect, render_template, url_for

from app.blueprints.system_config import system_config_bp
from app.blueprints.system_config.forms import SystemConfigForm
from app.extensions import db
from app.models import User
from app.utils.crypto import encrypt
from app.utils.decorators import permission_required
from app.utils.logger import log_activity
from app.utils.system_config import get_system_config

NO_SECURITY_CONTACT_VALUE = ""


def _security_contact_choices():
    return [(NO_SECURITY_CONTACT_VALUE, "— None (disabled) —")] + [
        (str(user.id), user.username) for user in User.query.order_by(User.username).all()
    ]


@system_config_bp.route("/", methods=["GET", "POST"])
@permission_required("system.manage")
def index():
    config = get_system_config()
    form = SystemConfigForm(obj=config)
    form.security_notification_user_id.choices = _security_contact_choices()
    # obj=config's populate doesn't reliably coerce a UUID column into the
    # string this SelectField's choices use (same gotcha EditUserForm's
    # role_id already works around in app/blueprints/users/routes.py) — set
    # it explicitly so the current recipient shows as selected.
    if not form.is_submitted():
        form.security_notification_user_id.data = (
            str(config.security_notification_user_id) if config.security_notification_user_id else NO_SECURITY_CONTACT_VALUE
        )

    if form.validate_on_submit():
        config.timezone = form.timezone.data
        config.session_timeout_minutes = form.session_timeout_minutes.data
        config.build_engine = form.build_engine.data
        config.hide_navbar_title_when_sidebar_open = form.hide_navbar_title_when_sidebar_open.data
        config.deployment_status_check_interval_seconds = form.deployment_status_check_interval_seconds.data
        config.commit_log_limit = form.commit_log_limit.data
        config.max_login_attempts = form.max_login_attempts.data
        config.telegram_notifications_enabled = form.telegram_notifications_enabled.data
        if form.telegram_bot_token.data:
            config.encrypted_telegram_bot_token = encrypt(form.telegram_bot_token.data)
        config.security_notification_user_id = (
            uuid.UUID(form.security_notification_user_id.data) if form.security_notification_user_id.data else None
        )
        db.session.commit()

        log_activity(
            action="UPDATE_SYSTEM_CONFIG",
            target_type="system_config",
            target_id=str(config.id),
            description=(
                f"Updated system configuration (timezone={config.timezone}, "
                f"session_timeout_minutes={config.session_timeout_minutes}, "
                f"build_engine={config.build_engine}, "
                f"hide_navbar_title_when_sidebar_open={config.hide_navbar_title_when_sidebar_open}, "
                f"deployment_status_check_interval_seconds={config.deployment_status_check_interval_seconds}, "
                f"commit_log_limit={config.commit_log_limit}, "
                f"max_login_attempts={config.max_login_attempts}, "
                f"telegram_notifications_enabled={config.telegram_notifications_enabled}, "
                f"security_notification_user_id={config.security_notification_user_id})"
            ),
        )

        flash("System configuration updated.", "success")
        return redirect(url_for("system_config.index"))

    return render_template("system_config/index.html", form=form)
