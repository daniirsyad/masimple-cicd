from flask import flash, redirect, render_template, url_for

from app.blueprints.registries import registries_bp
from app.blueprints.registries.forms import RegistryTargetForm
from app.extensions import db
from app.models import Builder, RegistryTarget
from app.services.registry.factory import get_registry_provider
from app.utils.crypto import decrypt, encrypt
from app.utils.decorators import permission_required
from app.utils.error_logger import error_detail_link, log_error
from app.utils.logger import log_activity

CREATE_PREFIX = "create-registry-"
DOCKERHUB = "dockerhub"
GHCR = "ghcr"

# Docker Hub and GHCR have real provider classes; the dropdown lists the
# rest (per spec) so adding them later is just a new class, no schema change.
# Saving a not-yet-implemented provider type is allowed (mirrors how
# AIProviderConfig lets Claude/Gemini/Custom be configured ahead of their
# implementation) — it's just skipped during validate_credentials().
IMPLEMENTED_PROVIDER_TYPES = {DOCKERHUB, GHCR}

# Provider types with a fixed, hardcoded registry host (Docker Hub, GHCR) —
# unlike a self-hosted target (Harbor) or one needing a derived host (ECR),
# these don't need a Registry URL entered at all.
FIXED_HOST_PROVIDER_TYPES = {DOCKERHUB, GHCR}


def _edit_prefix(target_id):
    return f"registry-{target_id}-"


def _validate_credentials(provider_type, username, token, registry_url=None):
    """Returns (ok, error). Unimplemented provider types are treated as ok
    (nothing to validate yet), matching the AI provider settings page's
    handling of not-yet-implemented providers. On failure, `error` is
    already a flash()-ready Markup value (message + a link to the ErrorLog
    row this call just created) — see error_detail_link().
    """
    if provider_type not in IMPLEMENTED_PROVIDER_TYPES:
        return True, None
    try:
        get_registry_provider(
            provider_type, username=username, password=token, registry_url=registry_url
        ).validate_credentials()
        return True, None
    except Exception as exc:
        entry = log_error(
            source="registries.validate_credentials",
            exc=exc,
            description=f"Credential validation failed for provider '{provider_type}': {exc}",
        )
        return False, error_detail_link(f"Could not validate credentials: {exc}", entry)


def _render_index(create_form=None, open_modal=None, invalid_edit=None):
    if create_form is None:
        create_form = RegistryTargetForm(prefix=CREATE_PREFIX)

    targets = RegistryTarget.query.order_by(RegistryTarget.name).all()

    invalid_id, invalid_form = invalid_edit or (None, None)
    edit_forms = {}
    for target in targets:
        if target.id == invalid_id:
            edit_forms[target.id] = invalid_form
        else:
            form = RegistryTargetForm(obj=target, prefix=_edit_prefix(target.id))
            form.token.data = ""  # never re-populate the encrypted value into the form
            edit_forms[target.id] = form

    return render_template(
        "registries/index.html",
        targets=targets,
        create_form=create_form,
        edit_forms=edit_forms,
        open_modal=open_modal,
    )


@registries_bp.route("/")
@permission_required("registry.manage")
def index():
    return _render_index()


@registries_bp.route("/create", methods=["POST"])
@permission_required("registry.manage")
def create():
    form = RegistryTargetForm(prefix=CREATE_PREFIX)

    if form.validate_on_submit():
        if form.provider_type.data not in FIXED_HOST_PROVIDER_TYPES and not form.registry_url.data:
            flash("Registry URL is required for this provider type.", "error")
            return _render_index(create_form=form, open_modal="create-registry-modal")

        if not form.token.data:
            flash("A token/password is required to add a new registry.", "error")
            return _render_index(create_form=form, open_modal="create-registry-modal")

        ok, error = _validate_credentials(
            form.provider_type.data, form.username.data, form.token.data, form.registry_url.data
        )
        if not ok:
            flash(error, "error")
            return _render_index(create_form=form, open_modal="create-registry-modal")

        target = RegistryTarget(
            name=form.name.data,
            provider_type=form.provider_type.data,
            username=form.username.data or None,
            encrypted_token=encrypt(form.token.data),
            registry_url=form.registry_url.data or None,
        )
        db.session.add(target)
        db.session.commit()

        log_activity(
            action="CREATE_REGISTRY_TARGET",
            target_type="registry_target",
            target_id=str(target.id),
            description=f"Added registry target '{target.name}'",
        )

        flash(f"Registry '{target.name}' added.", "success")
        return redirect(url_for("registries.index"))

    return _render_index(create_form=form, open_modal="create-registry-modal")


@registries_bp.route("/<uuid:target_id>/edit", methods=["POST"])
@permission_required("registry.manage")
def edit(target_id):
    target = RegistryTarget.query.get_or_404(target_id)
    form = RegistryTargetForm(prefix=_edit_prefix(target_id))

    if form.validate_on_submit():
        if form.provider_type.data not in FIXED_HOST_PROVIDER_TYPES and not form.registry_url.data:
            flash("Registry URL is required for this provider type.", "error")
            return _render_index(
                open_modal=f"edit-registry-modal-{target_id}", invalid_edit=(target_id, form)
            )

        token_for_validation = form.token.data or decrypt(target.encrypted_token)
        ok, error = _validate_credentials(
            form.provider_type.data, form.username.data, token_for_validation, form.registry_url.data
        )
        if not ok:
            flash(error, "error")
            return _render_index(
                open_modal=f"edit-registry-modal-{target_id}", invalid_edit=(target_id, form)
            )

        target.name = form.name.data
        target.provider_type = form.provider_type.data
        target.username = form.username.data or None
        if form.token.data:
            target.encrypted_token = encrypt(form.token.data)
        target.registry_url = form.registry_url.data or None
        db.session.commit()

        log_activity(
            action="UPDATE_REGISTRY_TARGET",
            target_type="registry_target",
            target_id=str(target.id),
            description=f"Updated registry target '{target.name}'",
        )

        flash(f"Registry '{target.name}' updated.", "success")
        return redirect(url_for("registries.index"))

    return _render_index(open_modal=f"edit-registry-modal-{target_id}", invalid_edit=(target_id, form))


@registries_bp.route("/<uuid:target_id>/delete", methods=["POST"])
@permission_required("registry.manage")
def delete(target_id):
    target = RegistryTarget.query.get_or_404(target_id)

    builder_count = Builder.query.filter_by(registry_target_id=target.id).count()
    if builder_count:
        flash(
            f"Cannot delete '{target.name}' — {builder_count} Builder(s) still reference it.", "error"
        )
        return redirect(url_for("registries.index"))

    name = target.name
    target_id_str = str(target.id)
    db.session.delete(target)
    db.session.commit()

    log_activity(
        action="DELETE_REGISTRY_TARGET",
        target_type="registry_target",
        target_id=target_id_str,
        description=f"Deleted registry target '{name}'",
    )

    flash(f"Registry '{name}' deleted.", "success")
    return redirect(url_for("registries.index"))
