from flask import flash, redirect, render_template, url_for

from app.blueprints.ai_settings import ai_settings_bp
from app.blueprints.ai_settings.forms import (
    AIProviderConfigForm,
    PromptTemplateForm,
    TestCommitMessageForm,
)
from app.extensions import db
from app.models import AIProviderConfig, PromptTemplate
from app.services.ai.build_prefill import suggest_metadata
from app.services.build.bump_heuristic import suggest_bump_type
from app.utils.crypto import encrypt
from app.utils.decorators import permission_required
from app.utils.logger import log_activity

CREATE_PROVIDER_PREFIX = "create-provider-"
CREATE_TEMPLATE_PREFIX = "create-template-"


def _provider_edit_prefix(provider_id):
    return f"provider-{provider_id}-"


def _template_edit_prefix(template_id):
    return f"template-{template_id}-"


def _make_only_default(model, exclude_id=None):
    """Clear is_default on every other row of `model` before the caller sets it
    True on its own (new or existing) row. Must run BEFORE that row is
    written, not after — the partial unique index checks each INSERT/UPDATE
    immediately, not deferred to commit, so clearing "the others" only works
    if there are no others left with is_default=True by the time this row's
    own write happens (including via autoflush, which would otherwise flush a
    pending is_default=True change on the target row before this query runs).
    """
    query = model.query
    if exclude_id is not None:
        query = query.filter(model.id != exclude_id)
    query.update({"is_default": False})


def _render_settings(
    provider_create_form=None,
    template_create_form=None,
    open_modal=None,
    invalid_provider_edit=None,
    invalid_template_edit=None,
    test_form=None,
    test_result=None,
    open_test_panel=False,
):
    if provider_create_form is None:
        provider_create_form = AIProviderConfigForm(prefix=CREATE_PROVIDER_PREFIX)
    if template_create_form is None:
        template_create_form = PromptTemplateForm(prefix=CREATE_TEMPLATE_PREFIX)
    if test_form is None:
        test_form = TestCommitMessageForm()

    providers = AIProviderConfig.query.order_by(AIProviderConfig.provider_type).all()
    templates = PromptTemplate.query.order_by(PromptTemplate.name).all()

    invalid_provider_id, invalid_provider_form = invalid_provider_edit or (None, None)
    provider_edit_forms = {}
    for provider in providers:
        if provider.id == invalid_provider_id:
            provider_edit_forms[provider.id] = invalid_provider_form
        else:
            form = AIProviderConfigForm(obj=provider, prefix=_provider_edit_prefix(provider.id))
            form.api_key.data = ""  # never re-populate the encrypted value into the form
            provider_edit_forms[provider.id] = form

    invalid_template_id, invalid_template_form = invalid_template_edit or (None, None)
    template_edit_forms = {}
    for template in templates:
        if template.id == invalid_template_id:
            template_edit_forms[template.id] = invalid_template_form
        else:
            template_edit_forms[template.id] = PromptTemplateForm(
                obj=template, prefix=_template_edit_prefix(template.id)
            )

    return render_template(
        "ai_settings/index.html",
        providers=providers,
        templates=templates,
        provider_create_form=provider_create_form,
        template_create_form=template_create_form,
        provider_edit_forms=provider_edit_forms,
        template_edit_forms=template_edit_forms,
        open_modal=open_modal,
        test_form=test_form,
        test_result=test_result,
        open_test_panel=open_test_panel,
    )


@ai_settings_bp.route("/")
@permission_required("aiprovider.manage")
def index():
    return _render_settings()


@ai_settings_bp.route("/providers/create", methods=["POST"])
@permission_required("aiprovider.manage")
def create_provider():
    form = AIProviderConfigForm(prefix=CREATE_PROVIDER_PREFIX)

    if form.validate_on_submit():
        if form.is_default.data:
            _make_only_default(AIProviderConfig)

        provider = AIProviderConfig(
            provider_type=form.provider_type.data,
            model_name=form.model_name.data,
            endpoint_url=form.endpoint_url.data or None,
            encrypted_api_key=encrypt(form.api_key.data) if form.api_key.data else None,
            is_active=form.is_active.data,
            is_default=form.is_default.data,
        )
        db.session.add(provider)
        db.session.commit()

        log_activity(
            action="CREATE_AI_PROVIDER_CONFIG",
            target_type="ai_provider_config",
            target_id=str(provider.id),
            description=f"Created AI provider config '{provider.provider_type}'",
        )

        flash(f"AI provider '{provider.provider_type}' created.", "success")
        return redirect(url_for("ai_settings.index"))

    return _render_settings(provider_create_form=form, open_modal="create-provider-modal")


@ai_settings_bp.route("/providers/<uuid:provider_id>/edit", methods=["POST"])
@permission_required("aiprovider.manage")
def edit_provider(provider_id):
    provider = AIProviderConfig.query.get_or_404(provider_id)
    form = AIProviderConfigForm(prefix=_provider_edit_prefix(provider_id))

    if form.validate_on_submit():
        if form.is_default.data:
            _make_only_default(AIProviderConfig, exclude_id=provider.id)

        provider.provider_type = form.provider_type.data
        provider.model_name = form.model_name.data
        provider.endpoint_url = form.endpoint_url.data or None
        if form.api_key.data:
            provider.encrypted_api_key = encrypt(form.api_key.data)
        provider.is_active = form.is_active.data
        provider.is_default = form.is_default.data

        db.session.commit()

        log_activity(
            action="UPDATE_AI_PROVIDER_CONFIG",
            target_type="ai_provider_config",
            target_id=str(provider.id),
            description=f"Updated AI provider config '{provider.provider_type}'",
        )

        flash(f"AI provider '{provider.provider_type}' updated.", "success")
        return redirect(url_for("ai_settings.index"))

    return _render_settings(
        open_modal=f"edit-provider-modal-{provider_id}", invalid_provider_edit=(provider_id, form)
    )


@ai_settings_bp.route("/providers/<uuid:provider_id>/delete", methods=["POST"])
@permission_required("aiprovider.manage")
def delete_provider(provider_id):
    provider = AIProviderConfig.query.get_or_404(provider_id)
    provider_type = provider.provider_type
    provider_id_str = str(provider.id)

    db.session.delete(provider)
    db.session.commit()

    log_activity(
        action="DELETE_AI_PROVIDER_CONFIG",
        target_type="ai_provider_config",
        target_id=provider_id_str,
        description=f"Deleted AI provider config '{provider_type}'",
    )

    flash(f"AI provider '{provider_type}' deleted.", "success")
    return redirect(url_for("ai_settings.index"))


@ai_settings_bp.route("/templates/create", methods=["POST"])
@permission_required("aiprovider.manage")
def create_template():
    form = PromptTemplateForm(prefix=CREATE_TEMPLATE_PREFIX)

    if form.validate_on_submit():
        if form.is_default.data:
            _make_only_default(PromptTemplate)

        template = PromptTemplate(
            name=form.name.data,
            template_text=form.template_text.data,
            is_active=form.is_active.data,
            is_default=form.is_default.data,
        )
        db.session.add(template)
        db.session.commit()

        log_activity(
            action="CREATE_PROMPT_TEMPLATE",
            target_type="prompt_template",
            target_id=str(template.id),
            description=f"Created prompt template '{template.name}'",
        )

        flash(f"Prompt template '{template.name}' created.", "success")
        return redirect(url_for("ai_settings.index"))

    return _render_settings(template_create_form=form, open_modal="create-template-modal")


@ai_settings_bp.route("/templates/<uuid:template_id>/edit", methods=["POST"])
@permission_required("aiprovider.manage")
def edit_template(template_id):
    template = PromptTemplate.query.get_or_404(template_id)
    form = PromptTemplateForm(prefix=_template_edit_prefix(template_id))

    if form.validate_on_submit():
        if form.is_default.data:
            _make_only_default(PromptTemplate, exclude_id=template.id)

        template.name = form.name.data
        template.template_text = form.template_text.data
        template.is_active = form.is_active.data
        template.is_default = form.is_default.data

        db.session.commit()

        log_activity(
            action="UPDATE_PROMPT_TEMPLATE",
            target_type="prompt_template",
            target_id=str(template.id),
            description=f"Updated prompt template '{template.name}'",
        )

        flash(f"Prompt template '{template.name}' updated.", "success")
        return redirect(url_for("ai_settings.index"))

    return _render_settings(
        open_modal=f"edit-template-modal-{template_id}", invalid_template_edit=(template_id, form)
    )


@ai_settings_bp.route("/templates/<uuid:template_id>/delete", methods=["POST"])
@permission_required("aiprovider.manage")
def delete_template(template_id):
    template = PromptTemplate.query.get_or_404(template_id)
    name = template.name
    template_id_str = str(template.id)

    db.session.delete(template)
    db.session.commit()

    log_activity(
        action="DELETE_PROMPT_TEMPLATE",
        target_type="prompt_template",
        target_id=template_id_str,
        description=f"Deleted prompt template '{name}'",
    )

    flash(f"Prompt template '{name}' deleted.", "success")
    return redirect(url_for("ai_settings.index"))


@ai_settings_bp.route("/test-commit-message", methods=["POST"])
@permission_required("aiprovider.manage")
def test_commit_message():
    """Runs the exact same Bump Type heuristic + Object(s)/Change Type/
    Description logic a real "Preview from Git" would (suggest_bump_type,
    suggest_metadata), against commit messages typed in here instead of
    read from a real git repo — lets someone check how a commit message
    would actually get classified without needing to make a real commit.
    Never persists anything; renders the page directly (no redirect) so
    the result shows up in the same response.
    """
    form = TestCommitMessageForm()
    test_result = None

    if form.validate_on_submit():
        # DataRequired already rejects an all-whitespace submission before
        # this point (it strips before checking truthiness), so at least
        # one non-empty line is guaranteed here.
        messages = [line.strip() for line in form.commit_messages.data.splitlines() if line.strip()]
        metadata = suggest_metadata(messages)
        test_result = {
            "bump_type": suggest_bump_type(messages),
            "matched_object_names": metadata["matched_object_names"],
            "new_object_names": metadata["new_object_names"],
            "change_type_name": metadata["change_type_name"],
            "description": metadata["description"],
        }

    return _render_settings(test_form=form, test_result=test_result, open_test_panel=True)
