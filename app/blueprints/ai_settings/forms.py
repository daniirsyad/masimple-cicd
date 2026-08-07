from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

PROVIDER_TYPE_CHOICES = [
    ("qwen", "Qwen"),
    ("claude", "Claude (not yet implemented)"),
    ("gemini", "Gemini (not yet implemented)"),
    ("custom", "Custom API (not yet implemented)"),
]


class AIProviderConfigForm(FlaskForm):
    provider_type = SelectField("Provider", choices=PROVIDER_TYPE_CHOICES, validators=[DataRequired()])
    model_name = StringField("Model Name", validators=[DataRequired(), Length(max=200)])
    endpoint_url = StringField(
        "Endpoint URL (Custom API only)", validators=[Optional(), Length(max=500)]
    )
    api_key = PasswordField("API Key", validators=[Optional(), Length(max=4000)])
    is_default = BooleanField("Set as Default")
    is_active = BooleanField("Active", default=True)
    submit = SubmitField("Save Provider")


class PromptTemplateForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    template_text = TextAreaField("Template", validators=[DataRequired()])
    is_default = BooleanField("Set as Default")
    is_active = BooleanField("Active", default=True)
    submit = SubmitField("Save Template")
