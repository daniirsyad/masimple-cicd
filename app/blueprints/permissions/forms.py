from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional


class PermissionForm(FlaskForm):
    code = StringField("Code", validators=[DataRequired(), Length(max=80)])
    description = StringField("Description", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Save Permission")
