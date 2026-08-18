import os

import requests

from app.services.registry.base import RegistryProvider

REGISTRY_HOST = "registry-1.docker.io"
HUB_LOGIN_URL = "https://hub.docker.com/v2/users/login/"


class DockerHubProvider(RegistryProvider):
    """Docker Hub, but `list_tags` speaks the standard Registry HTTP API v2 bearer-token
    challenge flow (not Docker Hub's proprietary Hub API) — the same shape GHCR/Harbor/ECR
    use, so adding those later only needs a different realm/host, not different calling code.
    """

    def __init__(self, username=None, password=None, registry_url=None):
        # registry_url is accepted (not just DockerHubProvider — every
        # RegistryProvider now takes the same superset of constructor
        # kwargs, see get_registry_provider's call sites) but unused here:
        # Docker Hub's host is fixed (REGISTRY_HOST below), unlike a
        # self-hosted registry (Harbor) or one needing a derived host (ECR).
        self.username = username or os.environ.get("DOCKERHUB_USERNAME")
        self.password = password or os.environ.get("DOCKERHUB_TOKEN")

    @property
    def registry_host(self):
        return REGISTRY_HOST

    @property
    def docker_config_auth_key(self):
        # Docker Hub is special-cased in the Docker/OCI credential-resolution
        # convention: an unqualified reference (no host prefix, e.g.
        # "hamiltondev/hamilton-ai" — see full_repository_name below) resolves
        # to the default registry "index.docker.io", not registry_host above
        # (that's the real pull/push API host, registry-1.docker.io). Both
        # Docker CLI's own `docker login` and go-containerregistry (what
        # Kaniko uses for its credential-file lookup) key that default
        # registry's auth entry under this exact legacy string instead —
        # writing the entry under registry_host here left Kaniko unable to
        # find any matching credentials for an unqualified push, so it fell
        # back to an anonymous, unauthenticated push and got a 401.
        return "https://index.docker.io/v1/"

    def full_repository_name(self, repository):
        return repository if "/" in repository else f"{self.username}/{repository}"

    def authenticate(self, docker_client):
        if not self.username or not self.password:
            raise RuntimeError(
                "Docker Hub credentials are not configured (DOCKERHUB_USERNAME/DOCKERHUB_TOKEN)."
            )
        # registry=... matters here in a way it doesn't for GHCR/Harbor/ECR's
        # own authenticate(): a real dockerd defaults an omitted registry to
        # Docker Hub's legacy identity internally, but Podman's Docker-API-
        # compatible /auth endpoint doesn't have that fallback — it 500s
        # trying to ping "https:///v2/" with no host at all. docker_config_
        # auth_key (not registry_host, the real API host — see its docstring)
        # is the same legacy identity Kaniko's credential file needed too.
        return docker_client.login(
            username=self.username, password=self.password, registry=self.docker_config_auth_key
        )

    def push_image(self, docker_client, repository, tag):
        self.authenticate(docker_client)
        full_repository = self.full_repository_name(repository)
        events = list(docker_client.images.push(full_repository, tag=tag, stream=True, decode=True))

        # docker-py's push stream never raises on its own for a failed push
        # (bad/missing local tag, auth rejected mid-push, ...) — errors only
        # show up as {"error": ...} entries in the event stream, so without
        # this check a failed push looks identical to a successful one to
        # every caller.
        for event in events:
            if "error" in event:
                raise RuntimeError(f"Docker push failed for {full_repository}:{tag}: {event['error']}")

        return events

    def list_tags(self, repository):
        full_repository = self.full_repository_name(repository)
        url = f"https://{REGISTRY_HOST}/v2/{full_repository}/tags/list"

        response = requests.get(url, timeout=10)
        if response.status_code == 401:
            token = self._get_bearer_token(response.headers.get("Www-Authenticate", ""), full_repository)
            response = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)

        response.raise_for_status()
        return response.json().get("tags", []) or []

    def _get_bearer_token(self, www_authenticate, full_repository):
        challenge = self._parse_www_authenticate(www_authenticate)
        realm = challenge.get("realm")
        if not realm:
            raise RuntimeError(f"Unexpected registry auth challenge: {www_authenticate!r}")

        params = {key: value for key, value in challenge.items() if key != "realm"}
        params.setdefault("scope", f"repository:{full_repository}:pull")

        auth = (self.username, self.password) if self.username and self.password else None
        token_response = requests.get(realm, params=params, auth=auth, timeout=10)
        token_response.raise_for_status()
        data = token_response.json()
        return data.get("token") or data.get("access_token")

    def validate_credentials(self):
        # Docker Hub's own login endpoint is a lighter check than the Registry
        # API v2 bearer-token flow used by list_tags() — it doesn't require a
        # specific repository scope to test against, just username/password.
        if not self.username or not self.password:
            raise RuntimeError("Docker Hub credentials are not configured (username/token).")
        response = requests.post(
            HUB_LOGIN_URL,
            json={"username": self.username, "password": self.password},
            timeout=10,
        )
        if response.status_code == 401:
            raise RuntimeError("Docker Hub authentication failed — check the username/token.")
        response.raise_for_status()
        return True

    @staticmethod
    def _parse_www_authenticate(header_value):
        # e.g. Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:x/y:pull"
        _, _, params_str = header_value.partition(" ")
        parsed = {}
        for part in params_str.split(","):
            if "=" not in part:
                continue
            key, _, value = part.strip().partition("=")
            parsed[key] = value.strip('"')
        return parsed
