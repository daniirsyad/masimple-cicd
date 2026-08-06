from flask_wtf import FlaskForm
from wtforms import SelectMultipleField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional
from wtforms.widgets import CheckboxInput, ListWidget


class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class RoleForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=80)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=500)])
    permissions = MultiCheckboxField("Permissions")
    submit = SubmitField("Save Role")
