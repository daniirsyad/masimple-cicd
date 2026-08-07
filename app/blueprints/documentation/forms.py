from flask_wtf import FlaskForm
from wtforms import HiddenField, SelectField, SelectMultipleField, StringField, SubmitField, TextAreaField
from wtforms.validators import Length, Optional


class DocumentationForm(FlaskForm):
    change_type_id = SelectField("Change Type", validators=[Optional()])
    object = StringField("Object", validators=[Optional(), Length(max=200)])
    description = TextAreaField("Description", validators=[Optional()])
    # Populated by the AI-generate JS only when a fresh draft was actually
    # requested this submission — left blank on a plain hand-edit resave, so
    # the route knows to keep whatever draft was already stored.
    ai_description = HiddenField()
    ai_provider_used = HiddenField()
    linked_batch_ids = SelectMultipleField("Linked Batches", validators=[Optional()])
    submit = SubmitField("Save Documentation")
