from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, SelectMultipleField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional
from wtforms.widgets import CheckboxInput, ListWidget


class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


CONNECTION_TYPE_CHOICES = [
    ("kube", "Kubernetes (kubeconfig)"),
    ("api", "Custom API (agent endpoint)"),
]


class DeploymentServerForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    connection_type = SelectField("Connection Type", choices=CONNECTION_TYPE_CHOICES, validators=[DataRequired()])
    # Only one of these is actually required, depending on connection_type —
    # enforced in routes.py (mirrors registries.routes's provider_type-
    # specific validation), not here, since WTForms choice-conditional
    # validation reads worse than one clear check in the view.
    kubeconfig = TextAreaField("Kubeconfig (YAML)", validators=[Optional(), Length(max=100000)])
    api_url = StringField("Agent API URL", validators=[Optional(), Length(max=500)])
    api_token = PasswordField("Agent API Token", validators=[Optional(), Length(max=4000)])
    allowed_role_ids = MultiCheckboxField(
        "Allowed Roles (leave empty to restrict to deployment_server.manage users only)",
        validators=[Optional()],
    )
    submit = SubmitField("Save Server")
