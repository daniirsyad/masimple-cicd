from app.services.yaml_generator.render import rows_to_dict


def build(fields: dict) -> dict:
    name = fields["name"]
    namespace = fields.get("namespace") or "default"
    replicas = fields.get("replicas") or 1
    image = fields["image"]
    container_port = fields.get("container_port")
    env_vars = rows_to_dict(fields.get("env_vars"))

    # Explicit labels win; otherwise default to {"app": name} so the
    # Deployment's own selector always has something to match against —
    # spec.selector is immutable after creation, so it must never be empty.
    labels = rows_to_dict(fields.get("labels")) or {"app": name}

    container = {"name": name, "image": image}
    if container_port:
        container["ports"] = [{"containerPort": container_port}]
    if env_vars:
        container["env"] = [{"name": key, "value": value} for key, value in env_vars.items()]

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {"containers": [container]},
            },
        },
    }
