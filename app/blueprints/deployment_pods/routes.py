import json

from flask import Response, abort, flash, jsonify, redirect, render_template, request, stream_with_context, url_for
from flask_login import current_user

from app.blueprints.deployment_pods import deployment_pods_bp
from app.blueprints.deployment_pods.forms import (
    ConfigMapCreateForm,
    ConfigMapEditForm,
    ImagePullSecretCreateForm,
    ImagePullSecretEditForm,
    NamespaceCreateForm,
    NamespaceEditForm,
    OpaqueSecretCreateForm,
    OpaqueSecretEditForm,
    parse_labels,
)
from app.models import DeploymentServer
from app.services.deployment.helpers import KUBE_CONNECTION_TYPE, provider_for_server
from app.utils.decorators import permission_required
from app.utils.error_logger import error_detail_link, log_error
from app.utils.logger import log_activity

DOCKERCONFIGJSON_SECRET_TYPE = "kubernetes.io/dockerconfigjson"


def _kube_server_or_404(server_id):
    """Pod management is a Kubernetes-only concept (namespaces, pods) — an
    "api"-type DeploymentServer has no such surface, see
    app/services/deployment/kubernetes_provider.py's list_pods/pod_logs/
    describe_pod (not part of the provider-agnostic DeploymentProvider base).
    Also enforces the same per-server access gate as triggering a deploy —
    seeing what's running is the same trust boundary as being able to change it.
    """
    server = DeploymentServer.query.get_or_404(server_id)
    if not server.is_accessible_to(current_user):
        abort(403)
    if server.connection_type != KUBE_CONNECTION_TYPE:
        abort(404)
    return server


def _summarize_node(item):
    conditions = item.get("status", {}).get("conditions", []) or []
    ready = next((c for c in conditions if c.get("type") == "Ready"), None)
    status = "Ready" if ready and ready.get("status") == "True" else "NotReady"
    labels = item.get("metadata", {}).get("labels", {}) or {}
    roles = [key.split("/")[-1] for key in labels if key.startswith("node-role.kubernetes.io/")]
    version = item.get("status", {}).get("nodeInfo", {}).get("kubeletVersion")
    return f"{status} · {'/'.join(roles) or 'worker'} · {version or '—'}"


def _summarize_service(item):
    spec = item.get("spec", {}) or {}
    ports = ", ".join(
        f"{port.get('port')}{'/' + port['protocol'] if port.get('protocol') else ''}"
        for port in spec.get("ports", []) or []
    )
    return f"{spec.get('type', '—')} · {spec.get('clusterIP', '—')} · {ports or 'no ports'}"


def _summarize_ingress(item):
    rules = item.get("spec", {}).get("rules", []) or []
    hosts = ", ".join(rule.get("host", "*") for rule in rules)
    return hosts or "—"


def _summarize_persistentvolume(item):
    spec = item.get("spec", {}) or {}
    status = item.get("status", {}) or {}
    capacity = (spec.get("capacity") or {}).get("storage", "—")
    claim = spec.get("claimRef") or {}
    claim_str = f"{claim['namespace']}/{claim['name']}" if claim.get("name") else "unbound"
    return f"{status.get('phase', '—')} · {capacity} · claim={claim_str}"


def _summarize_persistentvolumeclaim(item):
    spec = item.get("spec", {}) or {}
    status = item.get("status", {}) or {}
    capacity = (status.get("capacity") or {}).get("storage", "—")
    return f"{status.get('phase', '—')} · {capacity} · volume={spec.get('volumeName', '—')}"


# Slug (used in URLs) -> how to fetch/summarize that kind. Read-only by
# design (list + describe only, no delete/edit) — same safety level as the
# pod Logs/Describe modals, a deliberate scope choice. Namespaced kinds default
# to --all-namespaces when no ?namespace= filter is given; cluster-scoped
# kinds (nodes, PVs) never pass -n/-A at all. Namespaces themselves have
# their own dedicated CRUD pages/routes below (namespaces()/create_namespace()/
# etc.) instead of living in this read-only registry — same reasoning for
# Secrets (secrets()/create_secret()/etc.), which never appears here at all.
RESOURCE_KINDS = {
    "nodes": {
        "kubectl_kind": "node", "namespaced": False, "label": "Nodes", "summarize": _summarize_node,
    },
    "services": {
        "kubectl_kind": "service", "namespaced": True, "label": "Services", "summarize": _summarize_service,
    },
    "ingresses": {
        "kubectl_kind": "ingress", "namespaced": True, "label": "Ingress", "summarize": _summarize_ingress,
    },
    "persistentvolumes": {
        "kubectl_kind": "pv",
        "namespaced": False,
        "label": "Persistent Volumes",
        "summarize": _summarize_persistentvolume,
    },
    "persistentvolumeclaims": {
        "kubectl_kind": "pvc",
        "namespaced": True,
        "label": "Persistent Volume Claims",
        "summarize": _summarize_persistentvolumeclaim,
    },
}


@deployment_pods_bp.route("/")
@permission_required("deployment_pod.view")
def index():
    """No server picker of its own anymore — /deployment-servers already
    lists every server (with per-row links straight into Pods/Namespaces/
    Secrets/ConfigMaps for kube-type ones), so a separate picker page here
    was just the same list twice. Kept as a redirect, not removed outright,
    so old bookmarks/the (retired) "Deployment Pods" menu entry still land
    somewhere sensible instead of 404ing.
    """
    return redirect(url_for("deployment_servers.index"))


@deployment_pods_bp.route("/<uuid:server_id>")
@permission_required("deployment_pod.view")
def list_pods(server_id):
    server = _kube_server_or_404(server_id)
    namespace = request.args.get("namespace") or ""

    pods = []
    error = None
    try:
        pods = provider_for_server(server).list_pods(namespace or None)
    except Exception as exc:
        entry = log_error(
            source="deployment_pods.list_pods",
            exc=exc,
            description=f"Could not list pods for server '{server.name}' (namespace={namespace or 'all'}): {exc}",
        )
        error = error_detail_link("Could not list pods.", entry)

    return render_template(
        "deployment_pods/list.html",
        server=server,
        pods=pods,
        namespace=namespace,
        error=error,
        resource_kinds=RESOURCE_KINDS,
    )


# Pod logs/describe live under an explicit /pods/ segment (not bare
# /<server_id>/<namespace>/<pod_name>/...) so they can never collide with
# /<server_id>/resources/<kind>/... below — Werkzeug would otherwise treat a
# namespace literally named "resources" as ambiguous between the two route
# families, since a static path segment outranks a dynamic one.
@deployment_pods_bp.route("/<uuid:server_id>/pods/<namespace>/<pod_name>/logs")
@permission_required("deployment_pod.view")
def pod_logs(server_id, namespace, pod_name):
    """JSON, not a page — fetched by the Logs modal on deployment_pods/list.html."""
    server = _kube_server_or_404(server_id)
    container = request.args.get("container") or ""

    try:
        logs = provider_for_server(server).pod_logs(namespace, pod_name, container=container or None)
        return jsonify({"logs": logs, "error": None, "error_log_url": None})
    except Exception as exc:
        entry = log_error(
            source="deployment_pods.pod_logs",
            exc=exc,
            description=f"Could not fetch logs for pod '{pod_name}' ({namespace}) on '{server.name}': {exc}",
        )
        return jsonify(
            {
                "logs": None,
                "error": "Could not fetch logs.",
                "error_log_url": url_for("logs.error_detail", error_id=entry.id),
            }
        )


@deployment_pods_bp.route("/<uuid:server_id>/pods/<namespace>/<pod_name>/logs/stream")
@permission_required("deployment_pod.view")
def pod_logs_stream(server_id, namespace, pod_name):
    """Server-Sent Events — one `kubectl logs -f` per connection (see
    KubernetesProvider.stream_pod_logs), fed to the Logs modal on
    deployment_pods/list.html in place of its old 3s poll-and-refetch of
    pod_logs() above. Each log line is sent as its own `data:` event; a
    genuine kubectl failure is sent as a named `log-error` event instead so
    the client can distinguish it from an ordinary connection drop (which
    EventSource retries on its own) and stop rather than loop.
    """
    server = _kube_server_or_404(server_id)
    container = request.args.get("container") or ""
    provider = provider_for_server(server)

    def generate():
        try:
            for line in provider.stream_pod_logs(namespace, pod_name, container=container or None):
                yield f"data: {json.dumps({'line': line})}\n\n"
        except Exception as exc:
            entry = log_error(
                source="deployment_pods.pod_logs_stream",
                exc=exc,
                description=f"Could not stream logs for pod '{pod_name}' ({namespace}) on '{server.name}': {exc}",
            )
            payload = {
                "error": "Could not stream logs.",
                "error_log_url": url_for("logs.error_detail", error_id=entry.id),
            }
            yield f"event: log-error\ndata: {json.dumps(payload)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@deployment_pods_bp.route("/<uuid:server_id>/pods/<namespace>/<pod_name>/describe")
@permission_required("deployment_pod.view")
def describe_pod(server_id, namespace, pod_name):
    """JSON, not a page — fetched by the Describe modal on deployment_pods/list.html."""
    server = _kube_server_or_404(server_id)

    try:
        description = provider_for_server(server).describe_pod(namespace, pod_name)
        return jsonify({"description": description, "error": None, "error_log_url": None})
    except Exception as exc:
        entry = log_error(
            source="deployment_pods.describe_pod",
            exc=exc,
            description=f"Could not describe pod '{pod_name}' ({namespace}) on '{server.name}': {exc}",
        )
        return jsonify(
            {
                "description": None,
                "error": "Could not describe pod.",
                "error_log_url": url_for("logs.error_detail", error_id=entry.id),
            }
        )


@deployment_pods_bp.route("/<uuid:server_id>/resources/<kind>")
@permission_required("deployment_pod.view")
def list_resources(server_id, kind):
    server = _kube_server_or_404(server_id)
    config = RESOURCE_KINDS.get(kind)
    if config is None:
        abort(404)

    namespace = (request.args.get("namespace") or "") if config["namespaced"] else ""

    resources = []
    error = None
    try:
        items = provider_for_server(server).list_resources(
            config["kubectl_kind"],
            namespace=namespace or None,
            all_namespaces=config["namespaced"] and not namespace,
        )
        for item in items:
            metadata = item.get("metadata", {})
            resources.append(
                {
                    "name": metadata.get("name"),
                    "namespace": metadata.get("namespace"),
                    "created_at": metadata.get("creationTimestamp"),
                    "summary": config["summarize"](item),
                }
            )
    except Exception as exc:
        entry = log_error(
            source="deployment_pods.list_resources",
            exc=exc,
            description=f"Could not list {kind} for server '{server.name}': {exc}",
        )
        error = error_detail_link(f"Could not list {config['label'].lower()}.", entry)

    return render_template(
        "deployment_pods/resources.html",
        server=server,
        kind=kind,
        kind_config=config,
        resource_kinds=RESOURCE_KINDS,
        resources=resources,
        namespace=namespace,
        error=error,
    )


@deployment_pods_bp.route("/<uuid:server_id>/resources/<kind>/describe")
@permission_required("deployment_pod.view")
def describe_resource(server_id, kind):
    """JSON, not a page — fetched by the Describe modal on deployment_pods/resources.html."""
    server = _kube_server_or_404(server_id)
    config = RESOURCE_KINDS.get(kind)
    if config is None:
        abort(404)

    name = request.args.get("name") or ""
    if not name:
        abort(400)
    namespace = request.args.get("namespace") or ""

    try:
        description = provider_for_server(server).describe_resource(
            config["kubectl_kind"], name, namespace=namespace if config["namespaced"] else None
        )
        return jsonify({"description": description, "error": None, "error_log_url": None})
    except Exception as exc:
        entry = log_error(
            source="deployment_pods.describe_resource",
            exc=exc,
            description=f"Could not describe {kind}/{name} on '{server.name}': {exc}",
        )
        return jsonify(
            {
                "description": None,
                "error": "Could not describe resource.",
                "error_log_url": url_for("logs.error_detail", error_id=entry.id),
            }
        )


def _apply_and_respond(
    server, endpoint, action_fn, log_source, failure_desc, success_action, success_target_id,
    success_description, success_flash,
):
    """Shared tail for every namespace/secret create-edit-delete route: run
    `action_fn()` (a provider call returning a DeployResult), and handle the
    three outcomes the same way everywhere — an unexpected exception, a
    clean kubectl failure, or success. Only kubectl's own stderr (`result.log`/
    `result.error`, or the exception `str()`) ever reaches log_error/flash —
    never the request's own field values, which for a Secret would include
    plaintext data.
    """
    try:
        result = action_fn()
    except Exception as exc:
        entry = log_error(source=log_source, exc=exc, description=f"{failure_desc}: {exc}")
        flash(error_detail_link(f"{failure_desc}.", entry), "error")
        return redirect(url_for(endpoint, server_id=server.id))

    if not result.success:
        entry = log_error(source=log_source, detail=result.log, description=f"{failure_desc}: {result.error}")
        flash(error_detail_link(f"{failure_desc}.", entry), "error")
        return redirect(url_for(endpoint, server_id=server.id))

    log_activity(
        action=success_action,
        target_type="deployment_server",
        target_id=success_target_id,
        description=success_description,
    )
    flash(success_flash, "success")
    return redirect(url_for(endpoint, server_id=server.id))


def _namespace_edit_prefix(name):
    return f"namespace-{name}-"


def _render_namespaces(server, create_form=None, open_modal=None, invalid_edit=None):
    if create_form is None:
        create_form = NamespaceCreateForm(prefix="create-namespace-")

    items = []
    error = None
    try:
        items = provider_for_server(server).list_resources("namespace", namespace=None, all_namespaces=False)
    except Exception as exc:
        entry = log_error(
            source="deployment_pods.namespaces",
            exc=exc,
            description=f"Could not list namespaces for server '{server.name}': {exc}",
        )
        error = error_detail_link("Could not list namespaces.", entry)

    invalid_name, invalid_form = invalid_edit or (None, None)
    rows = []
    for item in items:
        metadata = item.get("metadata", {})
        name = metadata.get("name")
        if name == invalid_name:
            edit_form = invalid_form
        else:
            labels = metadata.get("labels") or {}
            edit_form = NamespaceEditForm(prefix=_namespace_edit_prefix(name))
            edit_form.labels.data = "\n".join(f"{key}={value}" for key, value in sorted(labels.items()))
        rows.append(
            {
                "name": name,
                "phase": item.get("status", {}).get("phase") or "—",
                "created_at": metadata.get("creationTimestamp"),
                "edit_form": edit_form,
            }
        )

    return render_template(
        "deployment_pods/namespaces.html",
        server=server,
        rows=rows,
        create_form=create_form,
        error=error,
        open_modal=open_modal,
        resource_kinds=RESOURCE_KINDS,
    )


@deployment_pods_bp.route("/<uuid:server_id>/namespaces")
@permission_required("deployment_pod.view")
def namespaces(server_id):
    server = _kube_server_or_404(server_id)
    return _render_namespaces(server)


@deployment_pods_bp.route("/<uuid:server_id>/namespaces/create", methods=["POST"])
@permission_required("deployment_namespace.manage")
def create_namespace(server_id):
    server = _kube_server_or_404(server_id)
    form = NamespaceCreateForm(prefix="create-namespace-")

    if not form.validate_on_submit():
        return _render_namespaces(server, create_form=form, open_modal="create-namespace-modal")

    name = form.name.data
    labels = parse_labels(form.labels.data)

    return _apply_and_respond(
        server,
        "deployment_pods.namespaces",
        lambda: provider_for_server(server).create_namespace(name, labels=labels or None),
        "deployment_pods.create_namespace",
        f"Could not create namespace '{name}' on server '{server.name}'",
        "CREATE_NAMESPACE",
        str(server.id),
        f"Created namespace '{name}' on server '{server.name}'",
        f"Namespace '{name}' created.",
    )


@deployment_pods_bp.route("/<uuid:server_id>/namespaces/<name>/edit", methods=["POST"])
@permission_required("deployment_namespace.manage")
def edit_namespace(server_id, name):
    server = _kube_server_or_404(server_id)
    form = NamespaceEditForm(prefix=_namespace_edit_prefix(name))

    if not form.validate_on_submit():
        return _render_namespaces(server, open_modal=f"edit-namespace-modal-{name}", invalid_edit=(name, form))

    labels = parse_labels(form.labels.data)

    return _apply_and_respond(
        server,
        "deployment_pods.namespaces",
        lambda: provider_for_server(server).create_namespace(name, labels=labels),
        "deployment_pods.edit_namespace",
        f"Could not update namespace '{name}' on server '{server.name}'",
        "UPDATE_NAMESPACE",
        str(server.id),
        f"Updated namespace '{name}' on server '{server.name}'",
        f"Namespace '{name}' updated.",
    )


@deployment_pods_bp.route("/<uuid:server_id>/namespaces/<name>/delete", methods=["POST"])
@permission_required("deployment_namespace.manage")
def delete_namespace(server_id, name):
    server = _kube_server_or_404(server_id)

    return _apply_and_respond(
        server,
        "deployment_pods.namespaces",
        lambda: provider_for_server(server).delete_namespace(name),
        "deployment_pods.delete_namespace",
        f"Could not delete namespace '{name}' on server '{server.name}'",
        "DELETE_NAMESPACE",
        str(server.id),
        f"Deleted namespace '{name}' on server '{server.name}'",
        f"Namespace '{name}' deleted.",
    )


def _secret_edit_prefix(namespace, name):
    return f"secret-{namespace}-{name}-"


def _namespace_choices(server):
    try:
        items = provider_for_server(server).list_resources("namespace", namespace=None, all_namespaces=False)
    except Exception:
        # The create-secret namespace dropdown just ends up empty — the page
        # itself still renders (and, for the list, surfaces its own error
        # banner separately via list_secrets below).
        return []
    names = {item.get("metadata", {}).get("name") for item in items}
    return sorted(name for name in names if name)


def _render_secrets(
    server, namespace_filter="", create_forms=None, open_modal=None, invalid_edit=None, secret_kind="opaque"
):
    choice_pairs = [(name, name) for name in _namespace_choices(server)]

    if create_forms is None:
        opaque_create_form = OpaqueSecretCreateForm(prefix="create-secret-opaque-")
        pull_create_form = ImagePullSecretCreateForm(prefix="create-secret-pull-")
    else:
        opaque_create_form, pull_create_form = create_forms
    opaque_create_form.namespace.choices = choice_pairs
    pull_create_form.namespace.choices = choice_pairs

    items = []
    error = None
    try:
        items = provider_for_server(server).list_secrets(namespace_filter or None)
    except Exception as exc:
        entry = log_error(
            source="deployment_pods.secrets",
            exc=exc,
            description=(
                f"Could not list secrets for server '{server.name}' "
                f"(namespace={namespace_filter or 'all'}): {exc}"
            ),
        )
        error = error_detail_link("Could not list secrets.", entry)

    invalid_key, invalid_form = invalid_edit or (None, None)
    rows = []
    for item in items:
        name = item["name"]
        item_namespace = item["namespace"]
        secret_type = item["type"]
        is_pull_secret = secret_type == DOCKERCONFIGJSON_SECRET_TYPE
        prefix = _secret_edit_prefix(item_namespace, name)

        if (item_namespace, name) == invalid_key:
            edit_form = invalid_form
        elif is_pull_secret:
            edit_form = ImagePullSecretEditForm(prefix=prefix)
        else:
            edit_form = OpaqueSecretEditForm(prefix=prefix)
            for key in item["keys"]:
                edit_form.existing_entries.append_entry({"key": key, "value": "", "remove": False})

        rows.append(
            {
                "name": name,
                "namespace": item_namespace,
                "type": secret_type,
                "is_pull_secret": is_pull_secret,
                # Named "secret_keys", not "keys" — a plain dict's .keys()
                # builtin method shadows a "keys" attribute in Jinja's
                # dot-access resolution, so `row.keys` in the template would
                # silently return the bound dict method instead of this list.
                "secret_keys": item["keys"],
                "created_at": item["created_at"],
                "edit_form": edit_form,
            }
        )

    return render_template(
        "deployment_pods/secrets.html",
        server=server,
        rows=rows,
        namespace_filter=namespace_filter,
        opaque_create_form=opaque_create_form,
        pull_create_form=pull_create_form,
        error=error,
        open_modal=open_modal,
        secret_kind=secret_kind,
        resource_kinds=RESOURCE_KINDS,
    )


@deployment_pods_bp.route("/<uuid:server_id>/secrets")
@permission_required("deployment_pod.view")
def secrets(server_id):
    server = _kube_server_or_404(server_id)
    namespace_filter = request.args.get("namespace") or ""
    return _render_secrets(server, namespace_filter=namespace_filter)


def _collect_opaque_entries(field_list):
    """FieldList(FormField(SecretKeyValueForm)) rows -> {key: value},
    skipping blank keys (an unused "add another key" row left empty by the
    user, most commonly).
    """
    data = {}
    for entry in field_list.entries:
        key = (entry.form.key.data or "").strip()
        if key:
            data[key] = entry.form.value.data or ""
    return data


@deployment_pods_bp.route("/<uuid:server_id>/secrets/create", methods=["POST"])
@permission_required("deployment_secret.manage")
def create_secret(server_id):
    server = _kube_server_or_404(server_id)
    namespace_choices = [(name, name) for name in _namespace_choices(server)]
    secret_kind = request.form.get("secret_kind") or "opaque"

    opaque_form = OpaqueSecretCreateForm(prefix="create-secret-opaque-")
    pull_form = ImagePullSecretCreateForm(prefix="create-secret-pull-")
    opaque_form.namespace.choices = namespace_choices
    pull_form.namespace.choices = namespace_choices

    if secret_kind == "pull":
        if not pull_form.validate_on_submit():
            return _render_secrets(
                server,
                create_forms=(opaque_form, pull_form),
                open_modal="create-secret-modal",
                secret_kind="pull",
            )

        name, namespace = pull_form.name.data, pull_form.namespace.data
        return _apply_and_respond(
            server,
            "deployment_pods.secrets",
            lambda: provider_for_server(server).create_image_pull_secret(
                namespace,
                name,
                pull_form.registry_server.data,
                pull_form.username.data,
                pull_form.password.data,
                email=pull_form.email.data or None,
            ),
            "deployment_pods.create_secret",
            f"Could not create secret '{name}' in namespace '{namespace}' on server '{server.name}'",
            "CREATE_SECRET",
            str(server.id),
            (
                f"Created image pull secret '{name}' for registry '{pull_form.registry_server.data}' "
                f"in namespace '{namespace}' on server '{server.name}'"
            ),
            f"Secret '{name}' created.",
        )

    if not opaque_form.validate_on_submit():
        return _render_secrets(server, create_forms=(opaque_form, pull_form), open_modal="create-secret-modal")

    data = _collect_opaque_entries(opaque_form.entries)
    if not data:
        flash("At least one key is required.", "error")
        return _render_secrets(server, create_forms=(opaque_form, pull_form), open_modal="create-secret-modal")

    name, namespace = opaque_form.name.data, opaque_form.namespace.data
    return _apply_and_respond(
        server,
        "deployment_pods.secrets",
        lambda: provider_for_server(server).create_secret(namespace, name, data),
        "deployment_pods.create_secret",
        f"Could not create secret '{name}' in namespace '{namespace}' on server '{server.name}'",
        "CREATE_SECRET",
        str(server.id),
        (
            f"Created secret '{name}' (keys: {', '.join(sorted(data.keys()))}) "
            f"in namespace '{namespace}' on server '{server.name}'"
        ),
        f"Secret '{name}' created.",
    )


@deployment_pods_bp.route("/<uuid:server_id>/secrets/<namespace>/<name>/edit", methods=["POST"])
@permission_required("deployment_secret.manage")
def edit_secret(server_id, namespace, name):
    server = _kube_server_or_404(server_id)
    secret_kind = request.form.get("secret_kind") or "opaque"
    prefix = _secret_edit_prefix(namespace, name)

    if secret_kind == "pull":
        form = ImagePullSecretEditForm(prefix=prefix)
        if not form.validate_on_submit():
            return _render_secrets(
                server, open_modal=f"edit-secret-modal-{namespace}-{name}", invalid_edit=((namespace, name), form)
            )

        return _apply_and_respond(
            server,
            "deployment_pods.secrets",
            lambda: provider_for_server(server).create_image_pull_secret(
                namespace, name, form.registry_server.data, form.username.data, form.password.data,
                email=form.email.data or None,
            ),
            "deployment_pods.edit_secret",
            f"Could not update secret '{name}' in namespace '{namespace}' on server '{server.name}'",
            "UPDATE_SECRET",
            str(server.id),
            (
                f"Updated image pull secret '{name}' for registry '{form.registry_server.data}' "
                f"in namespace '{namespace}' on server '{server.name}'"
            ),
            f"Secret '{name}' updated.",
        )

    form = OpaqueSecretEditForm(prefix=prefix)
    if not form.validate_on_submit():
        return _render_secrets(
            server, open_modal=f"edit-secret-modal-{namespace}-{name}", invalid_edit=((namespace, name), form)
        )

    data_updates = {}
    removed_keys = []
    for entry in form.existing_entries.entries:
        key = entry.form.key.data
        if not key:
            continue
        if entry.form.remove.data:
            removed_keys.append(key)
        elif entry.form.value.data:
            data_updates[key] = entry.form.value.data

    for key, value in _collect_opaque_entries(form.new_entries).items():
        data_updates[key] = value

    touched_keys = sorted(set(data_updates) | set(removed_keys))

    return _apply_and_respond(
        server,
        "deployment_pods.secrets",
        lambda: provider_for_server(server).update_secret(namespace, name, data_updates, removed_keys=removed_keys),
        "deployment_pods.edit_secret",
        f"Could not update secret '{name}' in namespace '{namespace}' on server '{server.name}'",
        "UPDATE_SECRET",
        str(server.id),
        (
            f"Updated secret '{name}' (keys touched: {', '.join(touched_keys) or 'none'}) "
            f"in namespace '{namespace}' on server '{server.name}'"
        ),
        f"Secret '{name}' updated.",
    )


@deployment_pods_bp.route("/<uuid:server_id>/secrets/<namespace>/<name>/delete", methods=["POST"])
@permission_required("deployment_secret.manage")
def delete_secret(server_id, namespace, name):
    server = _kube_server_or_404(server_id)

    return _apply_and_respond(
        server,
        "deployment_pods.secrets",
        lambda: provider_for_server(server).delete_secret(namespace, name),
        "deployment_pods.delete_secret",
        f"Could not delete secret '{name}' in namespace '{namespace}' on server '{server.name}'",
        "DELETE_SECRET",
        str(server.id),
        f"Deleted secret '{name}' in namespace '{namespace}' on server '{server.name}'",
        f"Secret '{name}' deleted.",
    )


def _configmap_edit_prefix(namespace, name):
    return f"configmap-{namespace}-{name}-"


def _render_configmaps(server, namespace_filter="", create_form=None, open_modal=None, invalid_edit=None):
    choice_pairs = [(name, name) for name in _namespace_choices(server)]

    if create_form is None:
        create_form = ConfigMapCreateForm(prefix="create-configmap-")
    create_form.namespace.choices = choice_pairs

    items = []
    error = None
    try:
        items = provider_for_server(server).list_configmaps(namespace_filter or None)
    except Exception as exc:
        entry = log_error(
            source="deployment_pods.configmaps",
            exc=exc,
            description=(
                f"Could not list configmaps for server '{server.name}' "
                f"(namespace={namespace_filter or 'all'}): {exc}"
            ),
        )
        error = error_detail_link("Could not list configmaps.", entry)

    invalid_key, invalid_form = invalid_edit or (None, None)
    rows = []
    for item in items:
        name = item["name"]
        item_namespace = item["namespace"]
        prefix = _configmap_edit_prefix(item_namespace, name)

        if (item_namespace, name) == invalid_key:
            edit_form = invalid_form
        else:
            edit_form = ConfigMapEditForm(prefix=prefix)
            # ConfigMap data isn't sensitive — list_configmaps already
            # fetched the real values in one call, so the edit form can be
            # pre-filled with them directly (no separate "keep blank"
            # dance, no second kubectl call).
            for key, value in sorted(item["data"].items()):
                edit_form.entries.append_entry({"key": key, "value": value})

        rows.append(
            {
                "name": name,
                "namespace": item_namespace,
                # "configmap_keys", not "keys" — same Jinja dict.keys()
                # attribute-collision gotcha as the Secret rows above.
                "configmap_keys": sorted(item["data"].keys()),
                "created_at": item["created_at"],
                "edit_form": edit_form,
            }
        )

    return render_template(
        "deployment_pods/configmaps.html",
        server=server,
        rows=rows,
        namespace_filter=namespace_filter,
        create_form=create_form,
        error=error,
        open_modal=open_modal,
        resource_kinds=RESOURCE_KINDS,
    )


@deployment_pods_bp.route("/<uuid:server_id>/configmaps")
@permission_required("deployment_pod.view")
def configmaps(server_id):
    server = _kube_server_or_404(server_id)
    namespace_filter = request.args.get("namespace") or ""
    return _render_configmaps(server, namespace_filter=namespace_filter)


@deployment_pods_bp.route("/<uuid:server_id>/configmaps/create", methods=["POST"])
@permission_required("deployment_configmap.manage")
def create_configmap(server_id):
    server = _kube_server_or_404(server_id)
    form = ConfigMapCreateForm(prefix="create-configmap-")
    form.namespace.choices = [(name, name) for name in _namespace_choices(server)]

    if not form.validate_on_submit():
        return _render_configmaps(server, create_form=form, open_modal="create-configmap-modal")

    data = _collect_opaque_entries(form.entries)
    if not data:
        flash("At least one key is required.", "error")
        return _render_configmaps(server, create_form=form, open_modal="create-configmap-modal")

    name, namespace = form.name.data, form.namespace.data
    return _apply_and_respond(
        server,
        "deployment_pods.configmaps",
        lambda: provider_for_server(server).create_configmap(namespace, name, data),
        "deployment_pods.create_configmap",
        f"Could not create configmap '{name}' in namespace '{namespace}' on server '{server.name}'",
        "CREATE_CONFIGMAP",
        str(server.id),
        (
            f"Created configmap '{name}' (keys: {', '.join(sorted(data.keys()))}) "
            f"in namespace '{namespace}' on server '{server.name}'"
        ),
        f"ConfigMap '{name}' created.",
    )


@deployment_pods_bp.route("/<uuid:server_id>/configmaps/<namespace>/<name>/edit", methods=["POST"])
@permission_required("deployment_configmap.manage")
def edit_configmap(server_id, namespace, name):
    server = _kube_server_or_404(server_id)
    form = ConfigMapEditForm(prefix=_configmap_edit_prefix(namespace, name))

    if not form.validate_on_submit():
        return _render_configmaps(
            server, open_modal=f"edit-configmap-modal-{namespace}-{name}", invalid_edit=((namespace, name), form)
        )

    data = _collect_opaque_entries(form.entries)
    if not data:
        flash("At least one key is required.", "error")
        return _render_configmaps(
            server, open_modal=f"edit-configmap-modal-{namespace}-{name}", invalid_edit=((namespace, name), form)
        )

    return _apply_and_respond(
        server,
        "deployment_pods.configmaps",
        lambda: provider_for_server(server).create_configmap(namespace, name, data),
        "deployment_pods.edit_configmap",
        f"Could not update configmap '{name}' in namespace '{namespace}' on server '{server.name}'",
        "UPDATE_CONFIGMAP",
        str(server.id),
        (
            f"Updated configmap '{name}' (keys: {', '.join(sorted(data.keys()))}) "
            f"in namespace '{namespace}' on server '{server.name}'"
        ),
        f"ConfigMap '{name}' updated.",
    )


@deployment_pods_bp.route("/<uuid:server_id>/configmaps/<namespace>/<name>/delete", methods=["POST"])
@permission_required("deployment_configmap.manage")
def delete_configmap(server_id, namespace, name):
    server = _kube_server_or_404(server_id)

    return _apply_and_respond(
        server,
        "deployment_pods.configmaps",
        lambda: provider_for_server(server).delete_configmap(namespace, name),
        "deployment_pods.delete_configmap",
        f"Could not delete configmap '{name}' in namespace '{namespace}' on server '{server.name}'",
        "DELETE_CONFIGMAP",
        str(server.id),
        f"Deleted configmap '{name}' in namespace '{namespace}' on server '{server.name}'",
        f"ConfigMap '{name}' deleted.",
    )


def _render_workloads(server, namespace_filter=""):
    items = []
    error = None
    try:
        items = provider_for_server(server).list_resources(
            "deployment", namespace=namespace_filter or None, all_namespaces=not namespace_filter
        )
    except Exception as exc:
        entry = log_error(
            source="deployment_pods.workloads",
            exc=exc,
            description=(
                f"Could not list workloads for server '{server.name}' "
                f"(namespace={namespace_filter or 'all'}): {exc}"
            ),
        )
        error = error_detail_link("Could not list workloads.", entry)

    rows = []
    for item in items:
        metadata = item.get("metadata", {})
        status = item.get("status", {}) or {}
        spec = item.get("spec", {}) or {}
        containers = spec.get("template", {}).get("spec", {}).get("containers", []) or []
        rows.append(
            {
                "name": metadata.get("name"),
                "namespace": metadata.get("namespace"),
                "ready": f"{status.get('readyReplicas', 0)}/{spec.get('replicas', 0)}",
                "images": ", ".join(container.get("image", "?") for container in containers) or "—",
                "created_at": metadata.get("creationTimestamp"),
            }
        )

    return render_template(
        "deployment_pods/workloads.html",
        server=server,
        rows=rows,
        namespace_filter=namespace_filter,
        error=error,
        resource_kinds=RESOURCE_KINDS,
    )


@deployment_pods_bp.route("/<uuid:server_id>/workloads")
@permission_required("deployment_pod.view")
def workloads(server_id):
    server = _kube_server_or_404(server_id)
    namespace_filter = request.args.get("namespace") or ""
    return _render_workloads(server, namespace_filter=namespace_filter)


@deployment_pods_bp.route("/<uuid:server_id>/workloads/<namespace>/<name>/restart", methods=["POST"])
@permission_required("deployment_workload.restart")
def restart_workload(server_id, namespace, name):
    server = _kube_server_or_404(server_id)

    return _apply_and_respond(
        server,
        "deployment_pods.workloads",
        lambda: provider_for_server(server).restart_deployment(namespace, name),
        "deployment_pods.restart_workload",
        f"Could not restart deployment '{name}' in namespace '{namespace}' on server '{server.name}'",
        "RESTART_WORKLOAD",
        str(server.id),
        f"Restarted deployment '{name}' in namespace '{namespace}' on server '{server.name}'",
        f"Deployment '{name}' restart triggered.",
    )
