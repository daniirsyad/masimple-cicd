from flask import abort, jsonify, render_template, request
from flask_login import current_user

from app.blueprints.deployment_pods import deployment_pods_bp
from app.models import DeploymentServer
from app.services.deployment.helpers import KUBE_CONNECTION_TYPE, provider_for_server
from app.utils.decorators import permission_required
from app.utils.error_logger import log_error


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


def _summarize_namespace(item):
    return item.get("status", {}).get("phase") or "—"


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
# design (list + describe only, no delete/edit) — see
# [[project-hamilton-deployment-module]] for why. Namespaced kinds default
# to --all-namespaces when no ?namespace= filter is given; cluster-scoped
# kinds (nodes, PVs, namespaces themselves) never pass -n/-A at all.
RESOURCE_KINDS = {
    "namespaces": {
        "kubectl_kind": "namespace", "namespaced": False, "label": "Namespaces", "summarize": _summarize_namespace,
    },
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
    servers = [
        server
        for server in DeploymentServer.query.filter_by(connection_type=KUBE_CONNECTION_TYPE)
        .order_by(DeploymentServer.name)
        .all()
        if server.is_accessible_to(current_user)
    ]
    return render_template("deployment_pods/index.html", servers=servers)


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
        log_error(
            source="deployment_pods.list_pods",
            exc=exc,
            description=f"Could not list pods for server '{server.name}' (namespace={namespace or 'all'}): {exc}",
        )
        error = "Could not list pods. See Error Logs for details."

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
        return jsonify({"logs": logs, "error": None})
    except Exception as exc:
        log_error(
            source="deployment_pods.pod_logs",
            exc=exc,
            description=f"Could not fetch logs for pod '{pod_name}' ({namespace}) on '{server.name}': {exc}",
        )
        return jsonify({"logs": None, "error": "Could not fetch logs. See Error Logs for details."})


@deployment_pods_bp.route("/<uuid:server_id>/pods/<namespace>/<pod_name>/describe")
@permission_required("deployment_pod.view")
def describe_pod(server_id, namespace, pod_name):
    """JSON, not a page — fetched by the Describe modal on deployment_pods/list.html."""
    server = _kube_server_or_404(server_id)

    try:
        description = provider_for_server(server).describe_pod(namespace, pod_name)
        return jsonify({"description": description, "error": None})
    except Exception as exc:
        log_error(
            source="deployment_pods.describe_pod",
            exc=exc,
            description=f"Could not describe pod '{pod_name}' ({namespace}) on '{server.name}': {exc}",
        )
        return jsonify({"description": None, "error": "Could not describe pod. See Error Logs for details."})


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
        log_error(
            source="deployment_pods.list_resources",
            exc=exc,
            description=f"Could not list {kind} for server '{server.name}': {exc}",
        )
        error = f"Could not list {config['label'].lower()}. See Error Logs for details."

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
        return jsonify({"description": description, "error": None})
    except Exception as exc:
        log_error(
            source="deployment_pods.describe_resource",
            exc=exc,
            description=f"Could not describe {kind}/{name} on '{server.name}': {exc}",
        )
        return jsonify({"description": None, "error": "Could not describe resource. See Error Logs for details."})
