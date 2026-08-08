import json

from app.services.deployment.factory import get_deployment_provider
from app.utils.crypto import decrypt

API_CONNECTION_TYPE = "api"
KUBE_CONNECTION_TYPE = "kube"


def provider_for_server(server):
    """Builds the right DeploymentProvider for a DeploymentServer row,
    decrypting and unpacking its stored credentials. Shared by
    /deployment-servers (test-connection button) and the deploy worker (apply
    at execution time) — every call site that needs to act on a registered
    server's connection.

    "kube" servers store the raw kubeconfig blob directly; "api" servers
    store a small {"api_url": ..., "token": ...} JSON document — both as
    ciphertext in DeploymentServer.encrypted_credentials.
    """
    credentials = decrypt(server.encrypted_credentials)

    if server.connection_type == KUBE_CONNECTION_TYPE:
        return get_deployment_provider(server.connection_type, kubeconfig=credentials)

    if server.connection_type == API_CONNECTION_TYPE:
        parsed = json.loads(credentials) if credentials else {}
        return get_deployment_provider(
            server.connection_type, api_url=parsed.get("api_url"), token=parsed.get("token")
        )

    raise ValueError(f"Unknown deployment connection type: {server.connection_type!r}")


def encode_api_credentials(api_url, token):
    """Builds the JSON blob stored (encrypted) for an "api"-type server."""
    return json.dumps({"api_url": api_url, "token": token})
