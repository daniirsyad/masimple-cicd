import zoneinfo

from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

TIMEZONE_CHOICES = [(tz, tz) for tz in sorted(zoneinfo.available_timezones())]

# "kaniko" deliberately left out of the selectable choices — see
# app/services/build/factory.py's _ENGINES for why (kaniko-executor has no
# daemon/chroot of its own; running it as a raw subprocess of this app, like
# KanikoBuildEngine currently does, extracts each FROM image's layers
# directly onto *this app's own* running container filesystem, corrupting
# it — confirmed in practice: a Kaniko build overwrote /etc/os-release and
# dropped Alpine binaries into a live app container). The KanikoBuildEngine
# implementation itself is untouched, just not reachable through this form,
# pending a rewrite that runs it in its own throwaway container instead.
BUILD_ENGINE_CHOICES = [
    ("docker", "Docker"),
]


class SystemConfigForm(FlaskForm):
    timezone = SelectField("Timezone", choices=TIMEZONE_CHOICES, validators=[DataRequired()])
    session_timeout_minutes = IntegerField(
        "Session Timeout (minutes)",
        validators=[DataRequired(), NumberRange(min=1, max=10080)],
    )
    build_engine = SelectField(
        "Build Engine", choices=BUILD_ENGINE_CHOICES, validators=[DataRequired()]
    )
    hide_navbar_title_when_sidebar_open = BooleanField("Hide duplicate app title")
    deployment_status_check_interval_seconds = IntegerField(
        "Deployment Live-Status Check Interval (seconds)",
        validators=[DataRequired(), NumberRange(min=5, max=86400)],
    )
    commit_log_limit = IntegerField(
        "Commit Log Limit (max commits read when there's no prior build to diff against)",
        validators=[DataRequired(), NumberRange(min=1, max=500)],
    )
    max_login_attempts = IntegerField(
        "Max Failed Login Attempts (before an account is locked)",
        validators=[DataRequired(), NumberRange(min=1, max=100)],
    )
    telegram_notifications_enabled = BooleanField("Enable Telegram Notifications")
    # Write-only, same "leave blank to keep the existing value" pattern as
    # AIProviderConfigForm.api_key — never pre-filled from the stored
    # (encrypted) value, since SystemConfigForm(obj=config) only populates
    # fields whose name matches a model attribute and this form field is
    # deliberately named differently from encrypted_telegram_bot_token.
    telegram_bot_token = StringField(
        "Telegram Bot Token (leave blank to keep the current one)", validators=[Optional(), Length(max=255)]
    )
    # Choices (every user) are populated in the route, same as
    # app.blueprints.users.routes._role_choices() — a "— None —" option is
    # always first so this can be explicitly disabled.
    security_notification_user_id = SelectField("Security Notification Recipient", validators=[Optional()])
    submit = SubmitField("Save Configuration")
