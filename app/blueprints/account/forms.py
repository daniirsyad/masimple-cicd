from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import EqualTo, Length, Optional, Regexp

# Same pattern as app/blueprints/users/forms.py's telegram_chat_id (not
# imported from there — this is a small, self-contained blueprint, not
# meant to depend on the admin Users blueprint).
TELEGRAM_CHAT_ID_RE = r"^-?\d+$"
TELEGRAM_CHAT_ID_MESSAGE = "Must be a numeric Telegram chat ID."


class AccountForm(FlaskForm):
    """Self-service "My Account" editor — deliberately a much narrower
    surface than the admin EditUserForm: no username, role, or is_active
    (those stay admin-only, via /users). Changing the password requires the
    current one, unlike an admin's blank-to-skip "Reset Password" field on
    /users, since there's no separate permission gate here to fall back on.
    """

    full_name = StringField("Full Name", validators=[Optional(), Length(max=120)])
    telegram_chat_id = StringField(
        "Telegram Chat ID (optional)", validators=[Optional(), Length(max=64), Regexp(TELEGRAM_CHAT_ID_RE, message=TELEGRAM_CHAT_ID_MESSAGE)]
    )
    current_password = PasswordField("Current Password", validators=[Optional()])
    new_password = PasswordField("New Password", validators=[Optional(), Length(min=8)])
    confirm_new_password = PasswordField(
        "Confirm New Password", validators=[Optional(), EqualTo("new_password", message="Passwords must match.")]
    )
    submit = SubmitField("Save Changes")
