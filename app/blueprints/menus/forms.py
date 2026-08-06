from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class MenuForm(FlaskForm):
    label = StringField("Label", validators=[DataRequired(), Length(max=80)])
    icon = TextAreaField("Icon", validators=[Optional(), Length(max=5000)])
    url = StringField("URL", validators=[Optional(), Length(max=200)])
    parent_id = SelectField("Parent Menu", validators=[Optional()])
    permission_code = SelectField("Required Permission", validators=[Optional()])
    show_in_navbar = BooleanField("Show in Navbar")
    show_in_sidebar = BooleanField("Show in Sidebar", default=True)
    submit = SubmitField("Save Menu")
