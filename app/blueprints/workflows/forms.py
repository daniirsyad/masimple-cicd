from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, SelectMultipleField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional
from wtforms.widgets import CheckboxInput, ListWidget

ON_FAILURE_CHOICES = [("stop", "Stop the run"), ("continue", "Continue to the next step")]


class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class WorkflowForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    description = TextAreaField("Description (optional)", validators=[Optional()])
    is_active = BooleanField("Active (inactive workflows can't be run)", default=True)
    allowed_role_ids = MultiCheckboxField(
        "Allowed Roles (leave empty to restrict to workflow.manage users only)", validators=[Optional()]
    )
    submit = SubmitField("Save Workflow")


class BuildStepForm(FlaskForm):
    # Choices assigned in routes.py (group_names from distinct
    # Builder.group_name; builder_ids restricted to ungrouped Builders only
    # — same "no per-row action on a grouped item" rule builders/index.html
    # already enforces) — mirrors BuilderForm's version_id/repository_id.
    group_names = MultiCheckboxField("Builder Groups", validators=[Optional()])
    builder_ids = MultiCheckboxField("Individual Builders (ungrouped only)", validators=[Optional()])
    # bump_type/change_type_id/object are Optional() here, not DataRequired()
    # — auto_generate_build_metadata=True leaves all three blank on purpose
    # (see WorkflowStep's own docstring); add_build_step enforces they're
    # filled in when auto-generate is off, same as today, since a form-level
    # DataRequired() can't be conditional on another field's value here.
    bump_type = SelectField("Version Bump", validators=[Optional()])
    change_type_id = SelectField("Change Type", validators=[Optional()])
    object = StringField("Object", validators=[Optional(), Length(max=255)])
    additional_description = TextAreaField("Additional Description (optional)", validators=[Optional()])
    auto_generate_build_metadata = BooleanField(
        "Auto-generate Version Bump / Change Type / Object / Message at run time (AI + commit history)",
        default=False,
    )
    require_review_before_build = BooleanField(
        "Require review before building (only applies when auto-generating)", default=True
    )
    on_failure = SelectField("If this step fails", choices=ON_FAILURE_CHOICES, validators=[DataRequired()])
    submit = SubmitField("Add Build Step")


class ApproveBuildStepForm(FlaskForm):
    """Edits an "awaiting_review" WorkflowStepRun's AI-suggested values
    before they're actually applied — see workflows.routes.approve_step_run.
    Same field shape as BuildStepForm's own build-metadata fields, minus
    everything about which builders/on_failure a step targets (already fixed
    at step-authoring time, not editable per-run here).
    """

    bump_type = SelectField("Version Bump", validators=[DataRequired()])
    change_type_id = SelectField("Change Type", validators=[DataRequired()])
    object = StringField("Object(s) (comma-separated)", validators=[Optional(), Length(max=1000)])
    description = TextAreaField("Message", validators=[Optional()])
    submit = SubmitField("Approve & Build")


class DeployStepForm(FlaskForm):
    group_names = MultiCheckboxField("Manifest Groups", validators=[Optional()])
    manifest_ids = MultiCheckboxField("Individual Manifests (ungrouped only)", validators=[Optional()])
    on_failure = SelectField("If this step fails", choices=ON_FAILURE_CHOICES, validators=[DataRequired()])
    submit = SubmitField("Add Deploy Step")
