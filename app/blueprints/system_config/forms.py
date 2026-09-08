import zoneinfo

from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

TIMEZONE_CHOICES = [(tz, tz) for tz in sorted(zoneinfo.available_timezones())]

# "docker" shells out to `docker buildx build` against a mounted Docker
# socket — needs a real Docker daemon (or Docker-API-compatible one, e.g.
# Podman's) reachable from this app's own container. "kaniko" instead
# launches kaniko-executor as its own Kubernetes Job (see KanikoBuildEngine,
# app/services/build/engine.py) — no daemon/socket of any kind needed, so
# it's the engine to pick on a cluster (e.g. CRI-O-backed) that doesn't
# expose a Docker-compatible socket at all. Needs the app pod's own
# ServiceAccount to have Job/Secret permissions in its namespace, and
# KANIKO_WORKSPACE_HOST_PATH set — see k8s/deployment.yaml.
BUILD_ENGINE_CHOICES = [
    ("docker", "Docker"),
    ("kaniko", "Kaniko (Kubernetes Job)"),
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
    telegram_bot_commands_enabled = BooleanField("Enable Telegram Bot Commands (/run, /status)")
    discord_notifications_enabled = BooleanField("Enable Discord Notifications")
    # Same write-only pattern as telegram_bot_token above.
    discord_bot_token = StringField(
        "Discord Bot Token (leave blank to keep the current one)", validators=[Optional(), Length(max=255)]
    )
    discord_bot_commands_enabled = BooleanField("Enable Discord Bot Commands (/run, /status)")
    # NOT secret (a public identifier, not a credential) — unlike the bot
    # token above, pre-filled normally via SystemConfigForm(obj=config).
    discord_application_id = StringField("Discord Application ID", validators=[Optional(), Length(max=64)])
    # Choices (every user) are populated in the route, same as
    # app.blueprints.users.routes._role_choices() — a "— None —" option is
    # always first so this can be explicitly disabled. Shared across both
    # Telegram and Discord — whichever provider(s) that user has linked and
    # enabled deliver the alert.
    security_notification_user_id = SelectField("Security Notification Recipient", validators=[Optional()])
    submit = SubmitField("Save Configuration")
