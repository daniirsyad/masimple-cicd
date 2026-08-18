import pytest

from app.services.registry.dockerhub import DockerHubProvider


class TestValidateCredentials:
    def test_raises_when_no_credentials_are_configured(self, monkeypatch):
        monkeypatch.delenv("DOCKERHUB_USERNAME", raising=False)
        monkeypatch.delenv("DOCKERHUB_TOKEN", raising=False)
        provider = DockerHubProvider()

        with pytest.raises(RuntimeError):
            provider.validate_credentials()

    def test_raises_when_only_username_is_configured(self):
        provider = DockerHubProvider(username="someone", password=None)

        with pytest.raises(RuntimeError):
            provider.validate_credentials()


class TestRegistryHost:
    def test_returns_docker_hub_api_host(self):
        provider = DockerHubProvider(username="someone", password="token")
        assert provider.registry_host == "registry-1.docker.io"


class TestDockerConfigAuthKey:
    def test_returns_legacy_index_docker_io_key_not_the_api_host(self):
        """Kaniko (via go-containerregistry) resolves an unqualified
        reference (e.g. "myuser/myapp", no host prefix — see
        full_repository_name) to the default registry "index.docker.io",
        and looks up its ~/.docker/config.json auth entry under the legacy
        key "https://index.docker.io/v1/" specifically — not registry_host
        (registry-1.docker.io, the real pull/push API host). Writing the
        auth entry under registry_host left Kaniko unable to find any
        matching credentials, so it fell back to an anonymous push and got
        a 401 — see app.services.build.engine.KanikoBuildEngine.
        """
        provider = DockerHubProvider(username="someone", password="token")
        assert provider.docker_config_auth_key == "https://index.docker.io/v1/"
        assert provider.docker_config_auth_key != provider.registry_host


class FakeImages:
    def __init__(self, events):
        self._events = events
        self.push_calls = []

    def push(self, repository, tag=None, stream=True, decode=True):
        self.push_calls.append((repository, tag))
        return iter(self._events)


class FakeDockerClient:
    def __init__(self, events):
        self.images = FakeImages(events)
        self.login_calls = []

    def login(self, username, password, registry=None):
        self.login_calls.append((username, password, registry))
        return {"Status": "Login Succeeded"}


class TestAuthenticate:
    def test_logs_in_with_the_legacy_index_docker_io_registry_key(self):
        """A real dockerd defaults an omitted `registry` to Docker Hub's
        legacy identity internally, but Podman's Docker-API-compatible
        /auth endpoint has no such fallback — it 500s trying to ping
        "https:///v2/" with no host at all when `registry` is left out.
        docker_client.login() must be given docker_config_auth_key (the
        legacy "https://index.docker.io/v1/" key, not registry_host) so
        this also works against a Podman-backed docker.sock.
        """
        events = []
        client = FakeDockerClient(events)
        provider = DockerHubProvider(username="myuser", password="token")

        provider.authenticate(client)

        assert client.login_calls == [("myuser", "token", "https://index.docker.io/v1/")]


class TestPushImage:
    def test_raises_when_push_stream_contains_an_error_event(self):
        """docker-py's push stream never raises on a failed push by itself —
        errors only show up as {"error": ...} entries — so without this check
        a failed push (e.g. no matching local tag, denied access) looks
        identical to a successful one to every caller.
        """
        events = [{"status": "Pushing"}, {"error": "denied: requested access to the resource is denied"}]
        client = FakeDockerClient(events)
        provider = DockerHubProvider(username="myuser", password="token")

        with pytest.raises(RuntimeError, match="denied"):
            provider.push_image(client, "myuser/myapp", "1.0.0")

    def test_returns_events_on_success(self):
        events = [{"status": "Pushing"}, {"status": "Pushed"}]
        client = FakeDockerClient(events)
        provider = DockerHubProvider(username="myuser", password="token")

        result = provider.push_image(client, "myuser/myapp", "1.0.0")

        assert result == events
        assert client.images.push_calls == [("myuser/myapp", "1.0.0")]
