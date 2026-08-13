def build(fields: dict) -> dict:
    name = fields["name"]
    namespace = fields.get("namespace") or "default"
    host = fields["host"]
    path = fields.get("path") or "/"
    path_type = fields.get("path_type") or "Prefix"
    backend_name = fields["backend_service_name"]
    backend_port = fields["backend_service_port"]
    tls_secret_name = fields.get("tls_secret_name")

    spec = {
        "rules": [
            {
                "host": host,
                "http": {
                    "paths": [
                        {
                            "path": path,
                            "pathType": path_type,
                            "backend": {
                                "service": {"name": backend_name, "port": {"number": backend_port}}
                            },
                        }
                    ]
                },
            }
        ]
    }
    if tls_secret_name:
        spec["tls"] = [{"hosts": [host], "secretName": tls_secret_name}]

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }
