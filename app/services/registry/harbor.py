import requests

from app.services.registry.base import RegistryProvider


class HarborProvider(RegistryProvider):
    """A self-hosted Harbor instance. Same shape as GHCRProvider (itself
    mirroring DockerHubProvider's bearer-token challenge flow — see that
    class's docstring) except the registry host isn't a hardcoded constant:
    Harbor is self-hosted, so it comes from RegistryTarget.registry_url.
    """

    def __init__(self, username=None, password=None, registry_url=None):
        if not registry_url:
            raise ValueError("HarborProvider requires a registry_url (this Harbor instance's host).")
        # Accept a full URL ("https://harbor.example.com/") or a bare host
        # ("harbor.example.com") — registry API calls below always build
        # their own "https://" URLs, so only the bare host is kept.
        self._registry_host = registry_url.replace("https://", "").replace("http://", "").rstrip("/")
        self.username = username
        self.password = password

    @property
    def registry_host(self):
        return self._registry_host

    def full_repository_name(self, repository):
        # Harbor image references need the instance's own host prefix
        # explicitly, same "no '/' means prepend the account (here: Harbor
        # project) name" convention as GHCRProvider/DockerHubProvider.
        if repository.startswith(f"{self.registry_host}/"):
            return repository
        if "/" in repository:
            return f"{self.registry_host}/{repository}"
        return f"{self.registry_host}/{self.username}/{repository}"

    def authenticate(self, docker_client):
        if not self.username or not self.password:
            raise RuntimeError("Harbor credentials are not configured (username/token).")
        return docker_client.login(username=self.username, password=self.password, registry=self.registry_host)

    def push_image(self, docker_client, repository, tag):
        self.authenticate(docker_client)
        full_repository = self.full_repository_name(repository)
        events = list(docker_client.images.push(full_repository, tag=tag, stream=True, decode=True))

        # Same "docker-py's push stream never raises on its own" caveat as
        # DockerHubProvider.push_image — see there for why this check exists.
        for event in events:
            if "error" in event:
                raise RuntimeError(f"Harbor push failed for {full_repository}:{tag}: {event['error']}")

        return events

    def list_tags(self, repository):
        full_repository = self.full_repository_name(repository)
        repo_path = full_repository[len(f"{self.registry_host}/") :]
        url = f"https://{self.registry_host}/v2/{repo_path}/tags/list"

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
        # Same "probe the v2 root for a challenge, then actually obtain a
        # token" approach as GHCRProvider.validate_credentials — see there
        # for the reasoning (no lightweight login endpoint, no specific
        # repository to scope a check against at save-time).
        if not self.username or not self.password:
            raise RuntimeError("Harbor credentials are not configured (username/token).")

        response = requests.get(f"https://{self.registry_host}/v2/", timeout=10)
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
            raise RuntimeError("Harbor authentication failed — check the username/token.")
        token_response.raise_for_status()

        data = token_response.json()
        if not (data.get("token") or data.get("access_token")):
            raise RuntimeError("Harbor did not return a token — check the username/token.")
        return True

    @staticmethod
    def _parse_www_authenticate(header_value):
        # e.g. Bearer realm="https://harbor.example.com/service/token",service="harbor-registry",scope="repository:proj/img:pull"
        _, _, params_str = header_value.partition(" ")
        parsed = {}
        for part in params_str.split(","):
            if "=" not in part:
                continue
            key, _, value = part.strip().partition("=")
            parsed[key] = value.strip('"')
        return parsed
