from flask_wtf import FlaskForm
from wtforms import SelectField, SelectMultipleField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional
from wtforms.widgets import CheckboxInput, ListWidget


class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class BuilderForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    version_id = SelectField("Version", validators=[DataRequired()])
    repository_id = SelectField("Repository", validators=[DataRequired()])
    # Choices depend on whichever repository is picked (via list_branches()),
    # discovered client-side by JS after a repo is selected — not knowable
    # server-side ahead of time for the create form, so membership isn't
    # statically validated here.
    default_branch = SelectField("Default Branch", validators=[DataRequired()], validate_choice=False)
    group_name = StringField("Group (optional)", validators=[Optional(), Length(max=120)])
    image_name = StringField(
        "Docker Image Name (optional)", validators=[Optional(), Length(max=255)]
    )
    dockerfile_path = StringField(
        "Dockerfile Path", default="Dockerfile", validators=[DataRequired(), Length(max=300)]
    )
    registry_target_id = SelectField("Registry Target", validators=[DataRequired()])
    allowed_role_ids = MultiCheckboxField(
        "Allowed Roles (leave empty to restrict to builder.manage users only)", validators=[Optional()]
    )
    submit = SubmitField("Save Builder")
