from flask_wtf import FlaskForm
from wtforms import BooleanField, FieldList, Form, FormField, HiddenField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, Regexp

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
