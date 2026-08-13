from flask_wtf import FlaskForm
from wtforms import FieldList, Form, FormField, IntegerField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, Regexp

from app.blueprints.deployment_pods.forms import DNS1123_LABEL_RE, DNS1123_MESSAGE


class OptionalIntegerField(IntegerField):
    """Plain IntegerField.process_formdata() calls int() on the raw string
    unconditionally, so a genuinely blank optional field ("") raises "Not a
    valid integer value" before Optional() ever gets a chance to skip
    it — a well-known WTForms gotcha. This subclass treats a blank string
    as no value at all, used for every Optional() integer field below
    (container_port, target_port) — the required ones (port, replicas,
    backend_service_port) correctly keep failing validation on blank input.
    """

    def process_formdata(self, valuelist):
        if valuelist and valuelist[0].strip() == "":
            self.data = None
            return
        super().process_formdata(valuelist)


# Plain (non-Flask) WTForms Form for FieldList rows — a FlaskForm subform
# would otherwise try to render its own CSRF field per row; the parent
# form's single hidden_tag() already covers the whole submission. Mirrors
# app/blueprints/deployment_pods/forms.py's SecretKeyValueForm.
class KeyValueRowForm(Form):
    key = StringField("Key", validators=[Optional(), Length(max=253)])
    value = StringField("Value", validators=[Optional(), Length(max=2000)])


class PortRowForm(Form):
    port = IntegerField("Port", validators=[DataRequired(), NumberRange(min=1, max=65535)])
    target_port = OptionalIntegerField("Target Port", validators=[Optional(), NumberRange(min=1, max=65535)])
    protocol = SelectField("Protocol", choices=[("TCP", "TCP"), ("UDP", "UDP")], default="TCP")


class DeploymentForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)]
    )
    namespace = StringField(
        "Namespace",
        validators=[Optional(), Length(max=63), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)],
        default="default",
    )
    replicas = IntegerField("Replicas", validators=[DataRequired(), NumberRange(min=1, max=100)], default=1)
    # Free text, deliberately no image-reference format validator — must
    # accept a literal {{SYS:VERSION}} / {{SYS:VERSION:key}} placeholder
    # token (see app/services/deployment/resolver.py) if the generated
    # YAML is later saved as a real DeploymentManifest.
    image = StringField("Image", validators=[DataRequired(), Length(max=500)])
    container_port = OptionalIntegerField("Container Port", validators=[Optional(), NumberRange(min=1, max=65535)])
    labels = FieldList(FormField(KeyValueRowForm), min_entries=1)
    env_vars = FieldList(FormField(KeyValueRowForm), min_entries=0)
    submit = SubmitField("Generate")


class ServiceForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)]
    )
    namespace = StringField(
        "Namespace",
        validators=[Optional(), Length(max=63), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)],
        default="default",
    )
    service_type = SelectField(
        "Type",
        choices=[("ClusterIP", "ClusterIP"), ("NodePort", "NodePort"), ("LoadBalancer", "LoadBalancer")],
        default="ClusterIP",
    )
    selector = FieldList(FormField(KeyValueRowForm), min_entries=1)
    ports = FieldList(FormField(PortRowForm), min_entries=1)
    submit = SubmitField("Generate")


class ConfigMapForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)]
    )
    namespace = StringField(
        "Namespace",
        validators=[Optional(), Length(max=63), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)],
        default="default",
    )
    data = FieldList(FormField(KeyValueRowForm), min_entries=1)
    submit = SubmitField("Generate")


class SecretForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)]
    )
    namespace = StringField(
        "Namespace",
        validators=[Optional(), Length(max=63), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)],
        default="default",
    )
    # Opaque only for v1 — kept as its own field (not hardcoded) so a
    # second secret kind (e.g. dockerconfigjson, per deployment_pods'
    # ImagePullSecretCreateForm) is a choices-list addition later, not a
    # form-shape change.
    secret_type = SelectField("Type", choices=[("Opaque", "Opaque")], default="Opaque")
    data = FieldList(FormField(KeyValueRowForm), min_entries=1)
    submit = SubmitField("Generate")


class IngressForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)]
    )
    namespace = StringField(
        "Namespace",
        validators=[Optional(), Length(max=63), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)],
        default="default",
    )
    host = StringField("Host", validators=[DataRequired(), Length(max=253)])
    path = StringField("Path", validators=[Optional(), Length(max=500)], default="/")
    path_type = SelectField(
        "Path Type",
        choices=[("Prefix", "Prefix"), ("Exact", "Exact"), ("ImplementationSpecific", "ImplementationSpecific")],
        default="Prefix",
    )
    backend_service_name = StringField(
        "Backend Service Name",
        validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)],
    )
    backend_service_port = IntegerField(
        "Backend Service Port", validators=[DataRequired(), NumberRange(min=1, max=65535)]
    )
    tls_secret_name = StringField("TLS Secret Name (optional)", validators=[Optional(), Length(max=253)])
    submit = SubmitField("Generate")
