from flask_wtf import FlaskForm
from wtforms import IntegerField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class VersionForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    # Manually typed, not picked from a dropdown — an unrecognized name
    # creates a new VersionType on save rather than being rejected, since the
    # spec calls Version Type "extensible without a code change".
    version_type = StringField("Version Type", validators=[DataRequired(), Length(max=100)])
    major = IntegerField("Major", default=0, validators=[Optional(), NumberRange(min=0)])
    minor = IntegerField("Minor", default=0, validators=[Optional(), NumberRange(min=0)])
    patch = IntegerField("Patch", default=0, validators=[Optional(), NumberRange(min=0)])
    submit = SubmitField("Save Version")
