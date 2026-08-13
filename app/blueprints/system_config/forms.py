import zoneinfo

from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, SubmitField
from wtforms.validators import DataRequired, NumberRange

TIMEZONE_CHOICES = [(tz, tz) for tz in sorted(zoneinfo.available_timezones())]

BUILD_ENGINE_CHOICES = [
    ("docker", "Docker"),
    ("kaniko", "Kaniko (daemonless, no docker.sock needed)"),
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
    submit = SubmitField("Save Configuration")
