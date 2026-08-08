from flask import flash, redirect, render_template, url_for

from app.blueprints.system_config import system_config_bp
from app.blueprints.system_config.forms import SystemConfigForm
from app.extensions import db
from app.utils.decorators import permission_required
from app.utils.logger import log_activity
from app.utils.system_config import get_system_config


@system_config_bp.route("/", methods=["GET", "POST"])
@permission_required("system.manage")
def index():
    config = get_system_config()
    form = SystemConfigForm(obj=config)

    if form.validate_on_submit():
        config.timezone = form.timezone.data
        config.session_timeout_minutes = form.session_timeout_minutes.data
        config.build_engine = form.build_engine.data
        config.hide_navbar_title_when_sidebar_open = form.hide_navbar_title_when_sidebar_open.data
        config.deployment_status_check_interval_seconds = form.deployment_status_check_interval_seconds.data
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
                f"deployment_status_check_interval_seconds={config.deployment_status_check_interval_seconds})"
            ),
        )

        flash("System configuration updated.", "success")
        return redirect(url_for("system_config.index"))

    return render_template("system_config/index.html", form=form)
