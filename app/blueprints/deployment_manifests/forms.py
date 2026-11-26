from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import (
    IntegerField,
    PasswordField,
    RadioField,
    SelectField,
    SelectMultipleField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.services.deployment.helm import NAMESPACE_MAX, RELEASE_NAME_MAX, RELEASE_NAME_RE
from wtforms.widgets import CheckboxInput, ListWidget


class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class DeploymentManifestForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    source_type = RadioField(
        "Type", choices=[("yaml", "Kubernetes YAML"), ("helm", "Helm chart")], default="yaml"
    )
    # Required for "yaml" (checked in validate()); for "helm" it's the
    # release's values file, which may legitimately be empty.
    yaml_content = TextAreaField("YAML", validators=[Optional()])
    helm_chart_source = RadioField(
        "Chart source", choices=[("repo", "Chart repository"), ("upload", "Upload a chart")], default="repo"
    )
    # Upload source: a new .tgz, and/or which already-uploaded version to
    # deploy (choices set per manifest in routes; checked there, with the DB).
    helm_chart_file = FileField("Chart file (.tgz)")
    helm_chart_upload_version = SelectField("Chart version to deploy", choices=[], validate_choice=False)
    helm_chart_repo = StringField("Chart repository", validators=[Optional(), Length(max=500)])
    helm_chart_name = StringField("Chart name", validators=[Optional(), Length(max=200)])
    helm_chart_version = StringField("Chart version", validators=[Optional(), Length(max=100)])
    helm_release_name = StringField("Release name", validators=[Optional(), Length(max=RELEASE_NAME_MAX)])
    helm_namespace = StringField("Namespace", validators=[Optional(), Length(max=NAMESPACE_MAX)])
    helm_repo_username = StringField("Username (private repository only)", validators=[Optional(), Length(max=200)])
    # Never rendered back; left blank on edit keeps the stored password.
    helm_repo_password = PasswordField("Password or token", validators=[Optional()])
    # After a successful apply, Deploy/Update/Restart waits up to this long
    # for this manifest's Deployment/StatefulSet/DaemonSet resources to
    # report Ready before counting as success — see
    # DeploymentManifest.wait_for_ready_timeout_seconds's own docstring. 0
    # skips the wait; capped at 1800s to protect the app-wide single-flight
    # deploy slot this wait runs inside of.
    wait_for_ready_timeout_seconds = IntegerField(
        "Rollout Wait Timeout (seconds)",
        default=300,
        validators=[
            # Optional, not DataRequired — DataRequired treats a coerced
            # value of 0 as "missing" (falsy check on field.data), which
            # would wrongly reject the meaningful "0 = skip the wait"
            # value. Optional() only short-circuits on a genuinely absent/
            # blank submission (field.data then stays at the `default=300`
            # above, since IntegerField.process_formdata() leaves data
            # untouched when raw_data is empty) — an explicit "0" is a
            # non-empty raw string, so it reaches NumberRange normally.
            Optional(),
            NumberRange(min=0, max=1800, message="Must be between 0 and 1800 seconds (0 skips the wait)."),
        ],
    )
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

    def validate(self, extra_validators=None):
        valid = super().validate(extra_validators)
        if self.source_type.data == "helm":
            checks = []
            if self.helm_chart_source.data != "upload":
                checks += [
                    (self.helm_chart_repo, "Enter the chart repository (https://... or oci://...).",
                     lambda v: v.startswith(("https://", "http://", "oci://"))),
                    (self.helm_chart_name, "Enter the chart name.", bool),
                    (self.helm_chart_version, "Enter the chart version, e.g. 1.2.3.", bool),
                ]
            checks += [
                (self.helm_release_name, "Use lowercase letters, numbers and dashes, starting and ending with a "
                 "letter or number.", RELEASE_NAME_RE.match),
                (self.helm_namespace, "Use lowercase letters, numbers and dashes, starting and ending with a "
                 "letter or number.", RELEASE_NAME_RE.match),
            ]
            for field, message, ok in checks:
                if not ok((field.data or "").strip()):
                    field.errors = [*field.errors, message]
                    valid = False
        elif not (self.yaml_content.data or "").strip():
            self.yaml_content.errors = [*self.yaml_content.errors, "This field is required."]
            valid = False
        return valid
