import uuid
from datetime import datetime

from flask import flash, redirect, render_template, url_for

from app.blueprints.deployment_servers import deployment_servers_bp
from app.blueprints.deployment_servers.forms import DeploymentServerForm
from app.extensions import db
from app.models import DeploymentExecution, DeploymentServer, Role
from app.services.deployment.helpers import API_CONNECTION_TYPE, KUBE_CONNECTION_TYPE, encode_api_credentials, provider_for_server
from app.utils.crypto import encrypt
from app.utils.decorators import permission_required
from app.utils.error_logger import error_detail_link, log_error
from app.utils.logger import log_activity

CREATE_PREFIX = "create-server-"


def _edit_prefix(server_id):
    return f"server-{server_id}-"


def _role_choices():
    return [(str(r.id), r.name) for r in Role.query.order_by(Role.name).all()]


def _apply_allowed_roles(server, form):
    selected_ids = {uuid.UUID(rid) for rid in form.allowed_role_ids.data}
    server.allowed_roles = Role.query.filter(Role.id.in_(selected_ids)).all() if selected_ids else []


def _encode_credentials(form):
    """Plaintext credentials blob to store (encrypted) for the connection_type
    chosen on `form`, or None if nothing usable was provided — a full
    kubeconfig for "kube", or an api_url (token optional) for "api".
    """
    if form.connection_type.data == KUBE_CONNECTION_TYPE:
        return form.kubeconfig.data or None
    if form.connection_type.data == API_CONNECTION_TYPE:
        if not form.api_url.data:
            return None
        return encode_api_credentials(form.api_url.data, form.api_token.data or "")
    return None


def _render_index(create_form=None, open_modal=None, invalid_edit=None):
    if create_form is None:
        create_form = DeploymentServerForm(prefix=CREATE_PREFIX)
    create_form.allowed_role_ids.choices = _role_choices()

    servers = DeploymentServer.query.filter_by(is_active=True).order_by(DeploymentServer.name).all()

    # Same guards delete() itself checks before rejecting the request —
    # computed here so the button can be disabled up front.
    delete_reasons = {}
    for server in servers:
        manifest_count = server.manifests.count()
        execution_count = DeploymentExecution.query.filter_by(server_id=server.id).count()
        if manifest_count:
            delete_reasons[server.id] = f"{manifest_count} manifest(s) still target it."
        elif execution_count:
            delete_reasons[server.id] = f"Has {execution_count} recorded deployment(s)."
        else:
            delete_reasons[server.id] = None

    invalid_id, invalid_form = invalid_edit or (None, None)
    edit_forms = {}
    for server in servers:
        if server.id == invalid_id:
            invalid_form.allowed_role_ids.choices = _role_choices()
            edit_forms[server.id] = invalid_form
        else:
            form = DeploymentServerForm(obj=server, prefix=_edit_prefix(server.id))
            form.allowed_role_ids.choices = _role_choices()
            form.allowed_role_ids.data = [str(role.id) for role in server.allowed_roles]
            # Never re-populate stored secrets into the edit form.
            form.kubeconfig.data = ""
            form.api_url.data = ""
            form.api_token.data = ""
            edit_forms[server.id] = form

    return render_template(
        "deployment_servers/index.html",
        servers=servers,
        delete_reasons=delete_reasons,
        create_form=create_form,
        edit_forms=edit_forms,
        open_modal=open_modal,
    )


def _render_archived():
    servers = DeploymentServer.query.filter_by(is_active=False).order_by(DeploymentServer.name).all()
    return render_template("deployment_servers/archived.html", servers=servers)


@deployment_servers_bp.route("/")
@permission_required("deployment_server.view")
def index():
    return _render_index()


@deployment_servers_bp.route("/create", methods=["POST"])
@permission_required("deployment_server.manage")
def create():
    form = DeploymentServerForm(prefix=CREATE_PREFIX)
    form.allowed_role_ids.choices = _role_choices()

    if form.validate_on_submit():
        credentials = _encode_credentials(form)
        if credentials is None:
            flash("Credentials are required for a new server.", "error")
            return _render_index(create_form=form, open_modal="create-server-modal")

        server = DeploymentServer(
            name=form.name.data,
            connection_type=form.connection_type.data,
            encrypted_credentials=encrypt(credentials),
            status="unverified",
        )
        _apply_allowed_roles(server, form)
        db.session.add(server)
        db.session.commit()

        log_activity(
            action="CREATE_DEPLOYMENT_SERVER",
            target_type="deployment_server",
            target_id=str(server.id),
            description=f"Created deployment server '{server.name}'",
        )

        flash(f"Server '{server.name}' added.", "success")
        return redirect(url_for("deployment_servers.index"))

    return _render_index(create_form=form, open_modal="create-server-modal")


@deployment_servers_bp.route("/<uuid:server_id>/edit", methods=["POST"])
@permission_required("deployment_server.manage")
def edit(server_id):
    server = DeploymentServer.query.get_or_404(server_id)
    form = DeploymentServerForm(prefix=_edit_prefix(server_id))
    form.allowed_role_ids.choices = _role_choices()

    if form.validate_on_submit():
        credentials = _encode_credentials(form)
        # A new credentials blob is only required if the connection_type is
        # actually changing — otherwise leaving the credential fields blank
        # just means "keep what's already stored" (mirrors
        # registries.routes.edit's handling of a blank token field).
        if credentials is None and form.connection_type.data != server.connection_type:
            flash("Credentials are required when changing the connection type.", "error")
            return _render_index(open_modal=f"edit-server-modal-{server_id}", invalid_edit=(server_id, form))

        server.name = form.name.data
        server.connection_type = form.connection_type.data
        if credentials is not None:
            server.encrypted_credentials = encrypt(credentials)
            server.status = "unverified"  # credentials changed — re-test before trusting the old status
        _apply_allowed_roles(server, form)
        db.session.commit()

        log_activity(
            action="UPDATE_DEPLOYMENT_SERVER",
            target_type="deployment_server",
            target_id=str(server.id),
            description=f"Updated deployment server '{server.name}'",
        )

        flash(f"Server '{server.name}' updated.", "success")
        return redirect(url_for("deployment_servers.index"))

    return _render_index(open_modal=f"edit-server-modal-{server_id}", invalid_edit=(server_id, form))


@deployment_servers_bp.route("/<uuid:server_id>/delete", methods=["POST"])
@permission_required("deployment_server.manage")
def delete(server_id):
    server = DeploymentServer.query.get_or_404(server_id)

    manifest_count = server.manifests.count()
    if manifest_count:
        flash(
            f"Cannot delete '{server.name}' — {manifest_count} manifest(s) still target it.", "error"
        )
        return redirect(url_for("deployment_servers.index"))

    execution_count = DeploymentExecution.query.filter_by(server_id=server.id).count()
    if execution_count:
        flash(
            f"Cannot delete '{server.name}' — it has {execution_count} recorded deployment(s).", "error"
        )
        return redirect(url_for("deployment_servers.index"))

    name = server.name
    server_id_str = str(server.id)
    db.session.delete(server)
    db.session.commit()

    log_activity(
        action="DELETE_DEPLOYMENT_SERVER",
        target_type="deployment_server",
        target_id=server_id_str,
        description=f"Deleted deployment server '{name}'",
    )

    flash(f"Server '{name}' deleted.", "success")
    return redirect(url_for("deployment_servers.index"))


@deployment_servers_bp.route("/archived")
@permission_required("deployment_server.manage")
def archived():
    return _render_archived()


@deployment_servers_bp.route("/<uuid:server_id>/disable", methods=["POST"])
@permission_required("deployment_server.manage")
def disable(server_id):
    server = DeploymentServer.query.get_or_404(server_id)
    server.is_active = False
    db.session.commit()

    log_activity(
        action="DISABLE_DEPLOYMENT_SERVER",
        target_type="deployment_server",
        target_id=str(server.id),
        description=f"Disabled deployment server '{server.name}'",
    )

    flash(f"'{server.name}' disabled — moved to Archived.", "info")
    return redirect(url_for("deployment_servers.index"))


@deployment_servers_bp.route("/<uuid:server_id>/enable", methods=["POST"])
@permission_required("deployment_server.manage")
def enable(server_id):
    server = DeploymentServer.query.get_or_404(server_id)
    server.is_active = True
    db.session.commit()

    log_activity(
        action="ENABLE_DEPLOYMENT_SERVER",
        target_type="deployment_server",
        target_id=str(server.id),
        description=f"Re-enabled deployment server '{server.name}'",
    )

    flash(f"'{server.name}' re-enabled.", "success")
    return redirect(url_for("deployment_servers.archived"))


@deployment_servers_bp.route("/<uuid:server_id>/test", methods=["POST"])
@permission_required("deployment_server.manage")
def test_connection(server_id):
    server = DeploymentServer.query.get_or_404(server_id)

    try:
        provider_for_server(server).test_connection()
        server.status = "healthy"
        flash(f"Connection to '{server.name}' succeeded.", "success")
    except Exception as exc:
        server.status = "unreachable"
        entry = log_error(
            source="deployment_servers.test_connection",
            exc=exc,
            description=f"Connection test failed for server '{server.name}': {exc}",
        )
        # The full error (kubectl output, connection details, etc.) can be
        # long — it's already captured in full by log_error above and
        # linked directly from the flash below; the flash banner just needs
        # to tell the user something failed, not reproduce it.
        flash(error_detail_link(f"Connection to '{server.name}' failed.", entry), "error")

    server.last_checked_at = datetime.utcnow()
    db.session.commit()

    log_activity(
        action="TEST_DEPLOYMENT_SERVER",
        target_type="deployment_server",
        target_id=str(server.id),
        description=f"Tested connection for deployment server '{server.name}' — status={server.status}",
    )

    return redirect(url_for("deployment_servers.index"))
