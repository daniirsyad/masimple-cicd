import requests

from app.services.registry.base import RegistryProvider

REGISTRY_HOST = "ghcr.io"


class GHCRProvider(RegistryProvider):
    """GitHub Container Registry. `list_tags` speaks the same standard
    Registry HTTP API v2 bearer-token challenge flow DockerHubProvider
    already uses — see that class's docstring — just against ghcr.io's own
    realm/host instead of Docker Hub's.
    """

    def __init__(self, username=None, password=None, registry_url=None):
        # registry_url unused — ghcr.io's host is fixed, same as Docker
        # Hub's (unlike Harbor, which is self-hosted and needs it).
        self.username = username
        self.password = password

    @property
    def registry_host(self):
        return REGISTRY_HOST

    def full_repository_name(self, repository):
        # GHCR image references need the ghcr.io host prefix explicitly
        # (unlike Docker Hub, which is the implicit default registry) — a
        # bare image name gets this account's own owner/org prepended too,
        # the same "no '/' means prepend the account" convention
        # DockerHubProvider uses for full_repository_name.
        if repository.startswith(f"{REGISTRY_HOST}/"):
            return repository
        if "/" in repository:
            return f"{REGISTRY_HOST}/{repository}"
        return f"{REGISTRY_HOST}/{self.username}/{repository}"

    def authenticate(self, docker_client):
        if not self.username or not self.password:
            raise RuntimeError("GHCR credentials are not configured (username/token).")
        return docker_client.login(username=self.username, password=self.password, registry=REGISTRY_HOST)

    def push_image(self, docker_client, repository, tag):
        self.authenticate(docker_client)
        full_repository = self.full_repository_name(repository)
        events = list(docker_client.images.push(full_repository, tag=tag, stream=True, decode=True))

        # Same "docker-py's push stream never raises on its own" caveat as
        # DockerHubProvider.push_image — see there for why this check exists.
        for event in events:
            if "error" in event:
                raise RuntimeError(f"GHCR push failed for {full_repository}:{tag}: {event['error']}")

        return events

    def list_tags(self, repository):
        full_repository = self.full_repository_name(repository)
        # full_repository already carries the "ghcr.io/" host prefix (see
        # full_repository_name) — the v2 API path itself only wants the
        # owner/image part after the host, so strip it back off here.
        repo_path = full_repository[len(f"{REGISTRY_HOST}/") :]
        url = f"https://{REGISTRY_HOST}/v2/{repo_path}/tags/list"

        response = requests.get(url, timeout=10)
        if response.status_code == 401:
            token = self._get_bearer_token(response.headers.get("Www-Authenticate", ""), repo_path)
            response = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)

        response.raise_for_status()
        return response.json().get("tags", []) or []

    def _get_bearer_token(self, www_authenticate, repo_path):
        challenge = self._parse_www_authenticate(www_authenticate)
        realm = challenge.get("realm")
        if not realm:
            raise RuntimeError(f"Unexpected registry auth challenge: {www_authenticate!r}")

        params = {key: value for key, value in challenge.items() if key != "realm"}
        params.setdefault("scope", f"repository:{repo_path}:pull")

        auth = (self.username, self.password) if self.username and self.password else None
        token_response = requests.get(realm, params=params, auth=auth, timeout=10)
        token_response.raise_for_status()
        data = token_response.json()
        return data.get("token") or data.get("access_token")

    def validate_credentials(self):
        # GHCR has no Docker-Hub-style lightweight login endpoint separate
        # from the real registry, and there's no specific repository to
        # scope a check against at save-time — so this probes the registry
        # root (a 401 is expected/normal there) and, if challenged, tries to
        # actually obtain a bearer token via the same realm the real
        # list_tags flow uses. A rejected/empty token means the credentials
        # themselves are bad, independent of any one repository.
        if not self.username or not self.password:
            raise RuntimeError("GHCR credentials are not configured (username/token).")

        response = requests.get(f"https://{REGISTRY_HOST}/v2/", timeout=10)
        if response.status_code != 401:
            response.raise_for_status()
            return True

        challenge = self._parse_www_authenticate(response.headers.get("Www-Authenticate", ""))
        realm = challenge.get("realm")
        if not realm:
            raise RuntimeError(f"Unexpected registry auth challenge: {response.headers.get('Www-Authenticate', '')!r}")

        params = {key: value for key, value in challenge.items() if key != "realm"}
        token_response = requests.get(realm, params=params, auth=(self.username, self.password), timeout=10)
        if token_response.status_code == 401:
            raise RuntimeError("GHCR authentication failed — check the username/token.")
        token_response.raise_for_status()

        data = token_response.json()
        if not (data.get("token") or data.get("access_token")):
            raise RuntimeError("GHCR did not return a token — check the username/token.")
        return True

    @staticmethod
    def _parse_www_authenticate(header_value):
        # e.g. Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:x/y:pull"
        _, _, params_str = header_value.partition(" ")
        parsed = {}
        for part in params_str.split(","):
            if "=" not in part:
                continue
            key, _, value = part.strip().partition("=")
            parsed[key] = value.strip('"')
        return parsed
