from flask_wtf import FlaskForm
from wtforms import HiddenField, SelectField, SelectMultipleField, SubmitField, TextAreaField
from wtforms.validators import Optional


class DocumentationForm(FlaskForm):
    change_type_id = SelectField("Change Type", validators=[Optional()])
    # Object(s) aren't a WTForms field — same reasoning as the build-trigger
    # form: it's a multi-value picker (existing Object ids + free-typed new
    # names), read straight off request.form ("object_ids"/"new_object_names")
    # in the route, then resolved via Object.resolve().
    description = TextAreaField("Description", validators=[Optional()])
    # Populated by the AI-generate JS only when a fresh draft was actually
    # requested this submission — left blank on a plain hand-edit resave, so
    # the route knows to keep whatever draft was already stored.
    ai_description = HiddenField()
    ai_provider_used = HiddenField()
    linked_batch_ids = SelectMultipleField("Linked Batches", validators=[Optional()])
    submit = SubmitField("Save Documentation")
