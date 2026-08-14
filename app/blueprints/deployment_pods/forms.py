from flask_wtf import FlaskForm
from wtforms import BooleanField, FieldList, Form, FormField, HiddenField, IntegerField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, Regexp

# Kubernetes DNS-1123 label rules (namespace/secret names): lowercase
# alphanumeric or '-', must start/end alphanumeric, max 63 chars — validating
# here gives a clean inline error instead of surfacing kubectl's raw rejection.
DNS1123_LABEL_RE = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
DNS1123_MESSAGE = "Must be lowercase alphanumeric or '-', and start/end with an alphanumeric character."

# Kubernetes Secret data key charset.
SECRET_KEY_RE = r"^[-._a-zA-Z0-9]+$"
SECRET_KEY_MESSAGE = "Keys may only contain letters, digits, '-', '_', and '.'."

# No email-validator package in this project's dependencies (WTForms' own
# Email() validator requires it) — a plain regex is enough for the optional
# image-pull-secret email field.
EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
EMAIL_MESSAGE = "Enter a valid email address."


def parse_labels(text):
    """"key=value" one per line -> {key: value}; blank lines ignored."""
    labels = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        key, _, value = line.partition("=")
        if key.strip():
            labels[key.strip()] = value.strip()
    return labels


class NamespaceCreateForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=63), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)]
    )
    labels = TextAreaField("Labels (key=value, one per line)", validators=[Optional()])
    submit = SubmitField("Create Namespace")


class NamespaceEditForm(FlaskForm):
    labels = TextAreaField("Labels (key=value, one per line)", validators=[Optional()])
    submit = SubmitField("Save Namespace")


# Plain (non-Flask) WTForms Form for FieldList rows — a FlaskForm subform
# would otherwise try to render its own CSRF field per row; the parent
# form's single hidden_tag() already covers the whole submission.
class SecretKeyValueForm(Form):
    key = StringField(
        "Key", validators=[Optional(), Length(max=253), Regexp(SECRET_KEY_RE, message=SECRET_KEY_MESSAGE)]
    )
    value = TextAreaField("Value", validators=[Optional()])


class ExistingSecretEntryForm(Form):
    key = HiddenField()
    value = TextAreaField("Value (leave blank to keep the existing value)", validators=[Optional()])
    remove = BooleanField("Remove this key")


class OpaqueSecretCreateForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)]
    )
    namespace = SelectField("Namespace", validators=[DataRequired()])
    entries = FieldList(FormField(SecretKeyValueForm), min_entries=1)
    submit = SubmitField("Create Secret")


class OpaqueSecretEditForm(FlaskForm):
    existing_entries = FieldList(FormField(ExistingSecretEntryForm))
    new_entries = FieldList(FormField(SecretKeyValueForm), min_entries=0)
    submit = SubmitField("Save Secret")


class ImagePullSecretCreateForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)]
    )
    namespace = SelectField("Namespace", validators=[DataRequired()])
    registry_server = StringField("Registry Server", validators=[DataRequired(), Length(max=255)])
    username = StringField("Username", validators=[DataRequired(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=1000)])
    email = StringField("Email (optional)", validators=[Optional(), Length(max=255), Regexp(EMAIL_RE, message=EMAIL_MESSAGE)])
    submit = SubmitField("Create Image Pull Secret")


class ImagePullSecretEditForm(FlaskForm):
    registry_server = StringField("Registry Server", validators=[DataRequired(), Length(max=255)])
    username = StringField("Username", validators=[DataRequired(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=1000)])
    email = StringField("Email (optional)", validators=[Optional(), Length(max=255), Regexp(EMAIL_RE, message=EMAIL_MESSAGE)])
    submit = SubmitField("Save Image Pull Secret")


class ConfigMapCreateForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)]
    )
    namespace = SelectField("Namespace", validators=[DataRequired()])
    # ConfigMap data isn't sensitive, so — unlike OpaqueSecretEditForm's
    # existing_entries/new_entries split (needed only to keep hidden values
    # hidden) — the same single `entries` FieldList shape works for both
    # create and edit; edit just pre-fills it with the real current values.
    entries = FieldList(FormField(SecretKeyValueForm), min_entries=1)
    submit = SubmitField("Create ConfigMap")


class ConfigMapEditForm(FlaskForm):
    entries = FieldList(FormField(SecretKeyValueForm), min_entries=0)
    submit = SubmitField("Save ConfigMap")


# Field names (path/path_type/backend_service_name/backend_service_port)
# deliberately match app/blueprints/yaml_generator/forms.py's IngressForm —
# both are converted into the same fields dict shape consumed by the shared
# app/services/yaml_generator/ingress.py's build().
INGRESS_PATH_TYPE_CHOICES = [
    ("Prefix", "Prefix"),
    ("Exact", "Exact"),
    ("ImplementationSpecific", "ImplementationSpecific"),
]

class IngressPathRowForm(Form):
    path = StringField("Path", validators=[Optional(), Length(max=500)], default="/")
    path_type = SelectField("Path Type", choices=INGRESS_PATH_TYPE_CHOICES, default="Prefix")
    backend_service_name = StringField(
        "Backend Service Name",
        validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)],
    )
    backend_service_port = IntegerField(
        "Backend Service Port", validators=[DataRequired(), NumberRange(min=1, max=65535)]
    )


class IngressCreateForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)]
    )
    namespace = SelectField("Namespace", validators=[DataRequired()])
    host = StringField("Host", validators=[DataRequired(), Length(max=253)])
    ingress_class_name = StringField("Ingress Class (optional)", validators=[Optional(), Length(max=253)])
    tls_secret_name = StringField("TLS Secret Name (optional)", validators=[Optional(), Length(max=253)])
    paths = FieldList(FormField(IngressPathRowForm), min_entries=1)
    submit = SubmitField("Create Ingress")


class IngressEditForm(FlaskForm):
    # name/namespace are not editable — same convention as every other
    # edit form in this file (implied by the URL path segments instead).
    host = StringField("Host", validators=[DataRequired(), Length(max=253)])
    ingress_class_name = StringField("Ingress Class (optional)", validators=[Optional(), Length(max=253)])
    tls_secret_name = StringField("TLS Secret Name (optional)", validators=[Optional(), Length(max=253)])
    paths = FieldList(FormField(IngressPathRowForm), min_entries=1)
    submit = SubmitField("Save Ingress")


class IngressYamlForm(FlaskForm):
    """The raw-YAML editing mode for Ingress create/edit — a CodeMirror-
    backed textarea whose text is passed straight to
    KubernetesProvider.apply(), unparsed (kubectl accepts YAML directly).
    Routes still parse it once with PyYAML purely to confirm `kind:
    Ingress` before applying, keeping this permission scoped to Ingresses
    rather than becoming a general "apply anything" backdoor.
    """

    yaml_content = TextAreaField("YAML", validators=[DataRequired()])
    submit = SubmitField("Save Ingress")


NETWORK_POLICY_PEER_TYPE_CHOICES = [
    ("pod", "Pod Selector (labels)"),
    ("namespace", "Namespace Selector (labels)"),
    ("ip_block", "IP Block (CIDR)"),
]
NETWORK_POLICY_PROTOCOL_CHOICES = [("TCP", "TCP"), ("UDP", "UDP"), ("SCTP", "SCTP")]


def parse_label_pairs(text):
    """"key=value,key2=value2" (single line, comma-separated — a peer row's
    compact Pod/Namespace Selector value) -> {key: value}. Distinct from
    parse_labels() above, which is "one key=value per line" for a
    full-width textarea (Namespace labels, NetworkPolicy's own
    pod_selector below) — this is the single-line variant for a peer row
    that also needs to fit a Peer Type select next to it.
    """
    labels = {}
    for piece in (text or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        key, _, value = piece.partition("=")
        if key.strip():
            labels[key.strip()] = value.strip()
    return labels


# Field names (peer_type/value, protocol/port) deliberately match
# app/blueprints/yaml_generator/forms.py's own NetworkPolicy peer/port rows
# — both are handed straight through (via .data) to the shared
# app/services/yaml_generator/network_policy.py's build(), same "one
# source of truth" precedent as Ingress's paths.
class NetworkPolicyPeerRowForm(Form):
    peer_type = SelectField("Peer Type", choices=NETWORK_POLICY_PEER_TYPE_CHOICES, default="pod")
    value = StringField("Labels (key=value,key2=value2) or CIDR", validators=[Optional(), Length(max=500)])


class NetworkPolicyPortRowForm(Form):
    protocol = SelectField("Protocol", choices=NETWORK_POLICY_PROTOCOL_CHOICES, default="TCP")
    port = IntegerField("Port", validators=[Optional(), NumberRange(min=1, max=65535)])


class NetworkPolicyCreateForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=253), Regexp(DNS1123_LABEL_RE, message=DNS1123_MESSAGE)]
    )
    namespace = SelectField("Namespace", validators=[DataRequired()])
    pod_selector = TextAreaField(
        "Applies To — Pod Selector Labels (key=value, one per line, blank = all pods)", validators=[Optional()]
    )
    enable_ingress_rules = BooleanField("Restrict Incoming Traffic (Ingress)")
    enable_egress_rules = BooleanField("Restrict Outgoing Traffic (Egress)")
    ingress_peers = FieldList(FormField(NetworkPolicyPeerRowForm), min_entries=0)
    ingress_ports = FieldList(FormField(NetworkPolicyPortRowForm), min_entries=0)
    egress_peers = FieldList(FormField(NetworkPolicyPeerRowForm), min_entries=0)
    egress_ports = FieldList(FormField(NetworkPolicyPortRowForm), min_entries=0)
    submit = SubmitField("Create Network Policy")


class NetworkPolicyEditForm(FlaskForm):
    # name/namespace are not editable — same convention as every other
    # edit form in this file.
    pod_selector = TextAreaField(
        "Applies To — Pod Selector Labels (key=value, one per line, blank = all pods)", validators=[Optional()]
    )
    enable_ingress_rules = BooleanField("Restrict Incoming Traffic (Ingress)")
    enable_egress_rules = BooleanField("Restrict Outgoing Traffic (Egress)")
    ingress_peers = FieldList(FormField(NetworkPolicyPeerRowForm), min_entries=0)
    ingress_ports = FieldList(FormField(NetworkPolicyPortRowForm), min_entries=0)
    egress_peers = FieldList(FormField(NetworkPolicyPeerRowForm), min_entries=0)
    egress_ports = FieldList(FormField(NetworkPolicyPortRowForm), min_entries=0)
    submit = SubmitField("Save Network Policy")


class NetworkPolicyYamlForm(FlaskForm):
    """Same raw-YAML editing mode as IngressYamlForm — text passed straight
    to KubernetesProvider.apply(), with a `kind: NetworkPolicy` check in
    the route before applying.
    """

    yaml_content = TextAreaField("YAML", validators=[DataRequired()])
    submit = SubmitField("Save Network Policy")
