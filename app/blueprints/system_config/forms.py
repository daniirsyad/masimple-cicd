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
    submit = SubmitField("Save Configuration")
