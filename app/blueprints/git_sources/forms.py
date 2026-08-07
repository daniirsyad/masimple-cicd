from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional


class GitSourceForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    # Required on create, optional on edit (blank keeps the current token) —
    # enforced explicitly in create_connection() rather than here, since a
    # single form class is shared by both routes.
    token = PasswordField("Token", validators=[Optional(), Length(max=4000)])
    submit = SubmitField("Save Connection")
