from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, Regexp, ValidationError

from app.models import User


class CreateUserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    password = PasswordField("Initial Password", validators=[DataRequired(), Length(min=8)])
    full_name = StringField("Full Name", validators=[Optional(), Length(max=120)])
    role_id = SelectField("Role", validators=[DataRequired()])
    # Numeric Telegram chat ID — see app/services/telegram/. Not validated
    # against Telegram itself (that would mean an outbound API call just to
    # save a user), only that it's plausibly numeric.
    telegram_chat_id = StringField(
        "Telegram Chat ID (optional)", validators=[Optional(), Length(max=64), Regexp(r"^-?\d+$", message="Must be a numeric Telegram chat ID.")]
    )
    # Discord snowflake user ID — see app/services/discord/. Same
    # "plausibly numeric only" validation as telegram_chat_id above; unlike
    # a Telegram chat ID, a Discord snowflake is never negative.
    discord_user_id = StringField(
        "Discord User ID (optional)", validators=[Optional(), Length(max=32), Regexp(r"^\d{17,20}$", message="Must be a numeric Discord user ID.")]
    )
    submit = SubmitField("Create User")

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError("Username already taken.")


class EditUserForm(FlaskForm):
    full_name = StringField("Full Name", validators=[Optional(), Length(max=120)])
    role_id = SelectField("Role", validators=[DataRequired()])
    is_active = BooleanField("Active")
    new_password = PasswordField("Reset Password", validators=[Optional(), Length(min=8)])
    telegram_chat_id = StringField(
        "Telegram Chat ID (optional)", validators=[Optional(), Length(max=64), Regexp(r"^-?\d+$", message="Must be a numeric Telegram chat ID.")]
    )
    discord_user_id = StringField(
        "Discord User ID (optional)", validators=[Optional(), Length(max=32), Regexp(r"^\d{17,20}$", message="Must be a numeric Discord user ID.")]
    )
    submit = SubmitField("Save Changes")
