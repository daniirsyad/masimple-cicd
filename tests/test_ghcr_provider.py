import pytest

from app.services.registry import ghcr as ghcr_module
from app.services.registry.ghcr import GHCRProvider


class TestRegistryHost:
    def test_returns_ghcr_host(self):
        provider = GHCRProvider(username="someone", password="token")
        assert provider.registry_host == "ghcr.io"


class TestFullRepositoryName:
    def test_prepends_host_and_username_for_a_bare_image_name(self):
        provider = GHCRProvider(username="myorg", password="token")
        assert provider.full_repository_name("myapp") == "ghcr.io/myorg/myapp"

    def test_prepends_host_only_when_owner_already_present(self):
        provider = GHCRProvider(username="myorg", password="token")
        assert provider.full_repository_name("otherorg/myapp") == "ghcr.io/otherorg/myapp"

    def test_leaves_an_already_fully_qualified_name_unchanged(self):
        provider = GHCRProvider(username="myorg", password="token")
        assert provider.full_repository_name("ghcr.io/otherorg/myapp") == "ghcr.io/otherorg/myapp"


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
    def test_raises_when_credentials_are_not_configured(self):
        provider = GHCRProvider()
        with pytest.raises(RuntimeError):
            provider.authenticate(FakeDockerClient([]))

    def test_logs_in_against_the_ghcr_host(self):
        provider = GHCRProvider(username="myorg", password="token")
        client = FakeDockerClient([])

        provider.authenticate(client)

        assert client.login_calls == [("myorg", "token", "ghcr.io")]


class TestPushImage:
    def test_raises_when_push_stream_contains_an_error_event(self):
        events = [{"status": "Pushing"}, {"error": "denied: requested access to the resource is denied"}]
        client = FakeDockerClient(events)
        provider = GHCRProvider(username="myorg", password="token")

        with pytest.raises(RuntimeError, match="denied"):
            provider.push_image(client, "myapp", "1.0.0")

    def test_pushes_under_the_fully_qualified_ghcr_name(self):
        events = [{"status": "Pushed"}]
        client = FakeDockerClient(events)
        provider = GHCRProvider(username="myorg", password="token")

        result = provider.push_image(client, "myapp", "1.0.0")

        assert result == events
        assert client.images.push_calls == [("ghcr.io/myorg/myapp", "1.0.0")]


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


class TestListTags:
    def test_returns_tags_directly_when_no_challenge_is_issued(self, monkeypatch):
        monkeypatch.setattr(
            ghcr_module.requests, "get", lambda url, **kwargs: _FakeResponse(200, {"tags": ["1.0.0", "1.0.1"]})
        )
        provider = GHCRProvider(username="myorg", password="token")

        assert provider.list_tags("myapp") == ["1.0.0", "1.0.1"]

    def test_fetches_a_bearer_token_on_401_then_retries(self, monkeypatch):
        calls = []

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                return _FakeResponse(
                    401,
                    headers={
                        "Www-Authenticate": 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:myorg/myapp:pull"'
                    },
                )
            if len(calls) == 2:
                assert url == "https://ghcr.io/token"
                return _FakeResponse(200, {"token": "abc123"})
            assert kwargs["headers"]["Authorization"] == "Bearer abc123"
            return _FakeResponse(200, {"tags": ["2.0.0"]})

        monkeypatch.setattr(ghcr_module.requests, "get", fake_get)
        provider = GHCRProvider(username="myorg", password="token")

        assert provider.list_tags("myapp") == ["2.0.0"]
        assert len(calls) == 3


class TestValidateCredentials:
    def test_raises_when_no_credentials_are_configured(self):
        provider = GHCRProvider()
        with pytest.raises(RuntimeError):
            provider.validate_credentials()

    def test_raises_when_the_token_endpoint_rejects_the_credentials(self, monkeypatch):
        def fake_get(url, **kwargs):
            if url == "https://ghcr.io/v2/":
                return _FakeResponse(401, headers={"Www-Authenticate": 'Bearer realm="https://ghcr.io/token"'})
            return _FakeResponse(401)

        monkeypatch.setattr(ghcr_module.requests, "get", fake_get)
        provider = GHCRProvider(username="myorg", password="bad-token")

        with pytest.raises(RuntimeError, match="authentication failed"):
            provider.validate_credentials()

    def test_returns_true_when_a_token_is_successfully_issued(self, monkeypatch):
        def fake_get(url, **kwargs):
            if url == "https://ghcr.io/v2/":
                return _FakeResponse(401, headers={"Www-Authenticate": 'Bearer realm="https://ghcr.io/token"'})
            return _FakeResponse(200, {"token": "abc123"})

        monkeypatch.setattr(ghcr_module.requests, "get", fake_get)
        provider = GHCRProvider(username="myorg", password="good-token")

        assert provider.validate_credentials() is True
