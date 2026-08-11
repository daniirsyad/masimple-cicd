from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional

PROVIDER_TYPE_CHOICES = [
    ("dockerhub", "Docker Hub"),
    ("ghcr", "GHCR"),
    ("harbor", "Harbor (not yet implemented)"),
    ("ecr", "ECR (not yet implemented)"),
    ("custom", "Custom (not yet implemented)"),
]


class RegistryTargetForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    provider_type = SelectField("Provider Type", choices=PROVIDER_TYPE_CHOICES, validators=[DataRequired()])
    username = StringField("Username", validators=[Optional(), Length(max=200)])
    token = PasswordField("Token/Password", validators=[Optional(), Length(max=4000)])
    registry_url = StringField(
        "Registry URL (non-Docker-Hub / self-hosted only)", validators=[Optional(), Length(max=500)]
    )
    submit = SubmitField("Save Registry")
