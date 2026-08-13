from app.services.yaml_generator.render import rows_to_dict


def build(fields: dict) -> dict:
    name = fields["name"]
    namespace = fields.get("namespace") or "default"
    data = rows_to_dict(fields.get("data"))

    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": namespace},
        "data": data,
    }
