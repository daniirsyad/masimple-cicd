from app.services.yaml_generator.render import rows_to_dict


def build(fields: dict) -> dict:
    name = fields["name"]
    namespace = fields.get("namespace") or "default"
    service_type = fields.get("service_type") or "ClusterIP"
    # Defaults to {"app": name} so this Service still targets something
    # sensible when built standalone (not paired with a Deployment
    # generated in the same session using matching labels).
    selector = rows_to_dict(fields.get("selector")) or {"app": name}

    ports = []
    for row in fields.get("ports") or []:
        port = row.get("port")
        if not port:
            continue
        port_spec = {"port": port, "protocol": row.get("protocol") or "TCP"}
        if row.get("target_port"):
            port_spec["targetPort"] = row["target_port"]
        ports.append(port_spec)

    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"type": service_type, "selector": selector, "ports": ports},
    }
