from flask_wtf import FlaskForm
from wtforms import SelectMultipleField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional
from wtforms.widgets import CheckboxInput, ListWidget


class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class DeploymentManifestForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    yaml_content = TextAreaField("YAML", validators=[DataRequired()])
    group_name = StringField("Group (optional)", validators=[Optional(), Length(max=120)])
    # Version bindings (one row per {{SYS:VERSION[:key]}} placeholder) are
    # submitted as parallel arrays (binding_key / binding_builder_id /
    # binding_pinned_build_id) and parsed directly in routes.py, the same
    # way builders.forms.BuilderForm's default_build_args are handled via
    # request.form.getlist rather than a WTForms field list — the row count
    # varies per manifest and is only knowable client-side after the YAML is
    # scanned for placeholders.
    # DataRequired() on a SelectMultipleField checks the submitted list is
    # non-empty — a manifest must target at least one server, since one with
    # none can never actually be deployed (see deployment_manifests.deploy's
    # own "no target servers configured" guard, which this prevents ever
    # being reachable from the save path).
    server_ids = MultiCheckboxField("Target Servers", validators=[DataRequired(message="Select at least one Target Server.")])
    # Optional, unlike server_ids — an empty selection means "unrestricted"
    # (see DeploymentManifest.allowed_users' docstring for why this
    # deliberately differs from DeploymentServer.allowed_role_ids, which
    # locks down to managers when left empty).
    allowed_user_ids = MultiCheckboxField(
        "Allowed Users (leave empty to allow anyone with deploy/update/stop/restart permission)",
        validators=[Optional()],
    )
    submit = SubmitField("Save Manifest")
