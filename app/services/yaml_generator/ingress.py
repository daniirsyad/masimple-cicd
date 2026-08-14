def build(fields: dict) -> dict:
    """Shared by both `/yaml-generator` (single path/backend per host, via
    the flat path/path_type/backend_service_name/backend_service_port keys)
    and the `/deployment-pods` Ingress CRUD (one host with one-or-more
    paths, via a `paths` list of the same four keys per entry) — one source
    of truth for what an Ingress manifest dict looks like. `fields["paths"]`
    takes priority when present; the flat keys are the fallback so the
    existing YAML Generator form (which only ever submits one path) is
    unaffected by the CRUD feature's multi-path support.
    """
    name = fields["name"]
    namespace = fields.get("namespace") or "default"
    host = fields["host"]
    tls_secret_name = fields.get("tls_secret_name")
    ingress_class_name = fields.get("ingress_class_name")

    paths = fields.get("paths") or [
        {
            "path": fields.get("path") or "/",
            "path_type": fields.get("path_type") or "Prefix",
            "backend_service_name": fields["backend_service_name"],
            "backend_service_port": fields["backend_service_port"],
        }
    ]

    spec = {
        "rules": [
            {
                "host": host,
                "http": {
                    "paths": [
                        {
                            "path": path.get("path") or "/",
                            "pathType": path.get("path_type") or "Prefix",
                            "backend": {
                                "service": {
                                    "name": path["backend_service_name"],
                                    "port": {"number": path["backend_service_port"]},
                                }
                            },
                        }
                        for path in paths
                    ]
                },
            }
        ]
    }
    if ingress_class_name:
        spec["ingressClassName"] = ingress_class_name
    if tls_secret_name:
        spec["tls"] = [{"hosts": [host], "secretName": tls_secret_name}]

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }
