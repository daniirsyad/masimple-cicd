from app.services.yaml_generator.render import rows_to_dict


def build(fields: dict) -> dict:
    """Shared by both `/yaml-generator` and the `/deployment-pods` Network
    Policy CRUD — one source of truth for what a NetworkPolicy manifest dict
    looks like, same precedent as ingress.py. Both callers hand this raw
    WTForms `.data`-shaped values (a FieldList becomes a plain list of row
    dicts, e.g. {"peer_type": ..., "value": ...} per peer row); this
    function does all the interpretation, mirroring configmap.py/
    service.py's use of rows_to_dict() for their own FieldList fields.
    `pod_selector` accepts either shape directly: a plain {key: value} dict
    (deployment_pods' own Form uses a "key=value per line" textarea, already
    parsed into a dict before calling here) or raw FieldList rows (the YAML
    Generator page's key/value rows, matching Deployment/Service/ConfigMap's
    own `pod_selector`-equivalent fields on that same page).
    """
    name = fields["name"]
    namespace = fields.get("namespace") or "default"
    pod_selector = _normalize_labels(fields.get("pod_selector"))
    enable_ingress = bool(fields.get("enable_ingress_rules"))
    enable_egress = bool(fields.get("enable_egress_rules"))

    spec = {"podSelector": {"matchLabels": pod_selector} if pod_selector else {}}

    policy_types = []
    if enable_ingress:
        policy_types.append("Ingress")
    if enable_egress:
        policy_types.append("Egress")
    if policy_types:
        spec["policyTypes"] = policy_types

    if enable_ingress:
        spec["ingress"] = [_build_rule(fields.get("ingress_peers"), fields.get("ingress_ports"), "from")]
    if enable_egress:
        spec["egress"] = [_build_rule(fields.get("egress_peers"), fields.get("egress_ports"), "to")]

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }


def _normalize_labels(value):
    if isinstance(value, dict):
        return value
    return rows_to_dict(value)


def _build_rule(peer_rows, port_rows, direction_key):
    """One rule (`spec.ingress[0]`/`spec.egress[0]`) — an empty rule (no
    peers, no ports) is valid Kubernetes and means "allow all" for that
    direction, so an intentionally-empty rule is left in place rather than
    dropped.
    """
    rule = {}
    peers = [peer for peer in (_build_peer(row) for row in (peer_rows or [])) if peer]
    if peers:
        rule[direction_key] = peers

    ports = [
        {"protocol": row.get("protocol") or "TCP", "port": row["port"]}
        for row in (port_rows or [])
        if row.get("port")
    ]
    if ports:
        rule["ports"] = ports

    return rule


def _build_peer(row):
    peer_type = row.get("peer_type")
    value = (row.get("value") or "").strip()
    if not value:
        return None
    if peer_type == "pod":
        return {"podSelector": {"matchLabels": _parse_label_pairs(value)}}
    if peer_type == "namespace":
        return {"namespaceSelector": {"matchLabels": _parse_label_pairs(value)}}
    if peer_type == "ip_block":
        return {"ipBlock": {"cidr": value}}
    return None


def _parse_label_pairs(text):
    """"key=value,key2=value2" -> {key: value} — kept local rather than
    reusing app.blueprints.deployment_pods.forms' equivalent helper, since
    services in this app never import from blueprints.
    """
    labels = {}
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        key, _, val = piece.partition("=")
        if key.strip():
            labels[key.strip()] = val.strip()
    return labels
