from flask_wtf import FlaskForm
from wtforms import SelectField, SelectMultipleField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional
from wtforms.widgets import CheckboxInput, ListWidget


class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


DOCKERFILE_SOURCE_CHOICES = [
    ("repo", "From Repository"),
    ("managed", "Managed Dockerfile"),
]


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
    # Exactly one of dockerfile_path/managed_dockerfile_id actually applies,
    # picked via dockerfile_source — both stay Optional() here (not
    # DataRequired) since only one panel is ever shown/relevant client-side;
    # which one is actually required is checked manually in routes.py once
    # dockerfile_source itself is known.
    dockerfile_source = SelectField(
        "Dockerfile Source", choices=DOCKERFILE_SOURCE_CHOICES, default="repo", validators=[DataRequired()]
    )
    dockerfile_path = StringField(
        "Dockerfile Path", default="Dockerfile", validators=[Optional(), Length(max=300)]
    )
    managed_dockerfile_id = SelectField("Managed Dockerfile", validators=[Optional()])
    registry_target_id = SelectField("Registry Target", validators=[DataRequired()])
    allowed_role_ids = MultiCheckboxField(
        "Allowed Roles (leave empty to restrict to builder.manage users only)", validators=[Optional()]
    )
    submit = SubmitField("Save Builder")
