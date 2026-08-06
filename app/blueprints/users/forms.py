from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from app.models import User


class CreateUserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    password = PasswordField("Initial Password", validators=[DataRequired(), Length(min=8)])
    full_name = StringField("Full Name", validators=[Optional(), Length(max=120)])
    role_id = SelectField("Role", validators=[DataRequired()])
    submit = SubmitField("Create User")

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError("Username already taken.")


class EditUserForm(FlaskForm):
    full_name = StringField("Full Name", validators=[Optional(), Length(max=120)])
    role_id = SelectField("Role", validators=[DataRequired()])
    is_active = BooleanField("Active")
    new_password = PasswordField("Reset Password", validators=[Optional(), Length(min=8)])
    submit = SubmitField("Save Changes")
