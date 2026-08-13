import base64

from app.services.yaml_generator.render import rows_to_dict


def build(fields: dict) -> dict:
    name = fields["name"]
    namespace = fields.get("namespace") or "default"
    secret_type = fields.get("secret_type") or "Opaque"
    raw_data = rows_to_dict(fields.get("data"))
    # Kubernetes Secret.data values must be base64 — the form collects
    # plain text (what a human actually types), encoded only here at
    # build time, mirroring kubernetes_provider.py's own Secret handling
    # (values are never persisted/logged in plain text past this point).
    data = {key: base64.b64encode(value.encode()).decode() for key, value in raw_data.items()}

    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": secret_type,
        "metadata": {"name": name, "namespace": namespace},
        "data": data,
    }
