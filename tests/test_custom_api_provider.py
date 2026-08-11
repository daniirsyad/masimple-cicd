import pytest
import requests

from app.services.deployment import custom_api_provider as custom_api_provider_module
from app.services.deployment.base import DeployResult
from app.services.deployment.custom_api_provider import CustomAPIProvider

API_URL = "https://agent.example.com/apply"
MANIFEST_YAML = "apiVersion: v1\nkind: Pod\nmetadata:\n  name: demo\n"


class _FakeResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class _RecordingRequests:
    """Records exactly the (method, url, kwargs) each call was made with, so
    tests can assert on the exact verb/headers/body this provider builds —
    no HTTP mocking library is installed in this repo, so this stands in for
    `requests.get`/`requests.post`/`requests.delete` directly, matching the
    monkeypatch-at-the-call-boundary style already used elsewhere (e.g.
    tests/test_deployment_servers.py's KubernetesProvider monkeypatching).
    """

    def __init__(self, response=None, exc=None):
        self.response = response or _FakeResponse()
        self.exc = exc
        self.calls = []

    def _record(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.response

    def get(self, url, **kwargs):
        return self._record("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._record("POST", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._record("DELETE", url, **kwargs)


def _patch_requests(monkeypatch, response=None, exc=None):
    fake = _RecordingRequests(response=response, exc=exc)
    monkeypatch.setattr(custom_api_provider_module.requests, "get", fake.get)
    monkeypatch.setattr(custom_api_provider_module.requests, "post", fake.post)
    monkeypatch.setattr(custom_api_provider_module.requests, "delete", fake.delete)
    return fake


class TestTestConnection:
    def test_success_on_status_below_400(self, monkeypatch):
        fake = _patch_requests(monkeypatch, response=_FakeResponse(status_code=200))
        provider = CustomAPIProvider(api_url=API_URL)

        assert provider.test_connection() is True
        assert fake.calls == [("GET", API_URL, {"headers": {"Content-Type": "application/x-yaml"}, "timeout": 30})]

    def test_raises_on_status_400_or_above(self, monkeypatch):
        _patch_requests(monkeypatch, response=_FakeResponse(status_code=503, text="agent unavailable"))
        provider = CustomAPIProvider(api_url=API_URL)

        with pytest.raises(RuntimeError, match="503"):
            provider.test_connection()

    def test_raises_on_network_exception(self, monkeypatch):
        _patch_requests(monkeypatch, exc=requests.RequestException("connection refused"))
        provider = CustomAPIProvider(api_url=API_URL)

        with pytest.raises(RuntimeError, match="connection refused"):
            provider.test_connection()

    def test_authorization_header_present_only_when_token_is_configured(self, monkeypatch):
        fake = _patch_requests(monkeypatch)
        CustomAPIProvider(api_url=API_URL, token="secret-token").test_connection()
        assert fake.calls[0][2]["headers"]["Authorization"] == "Bearer secret-token"

        fake_no_token = _patch_requests(monkeypatch)
        CustomAPIProvider(api_url=API_URL).test_connection()
        assert "Authorization" not in fake_no_token.calls[0][2]["headers"]


class TestApply:
    def test_posts_raw_yaml_body_with_correct_content_type(self, monkeypatch):
        fake = _patch_requests(monkeypatch)
        provider = CustomAPIProvider(api_url=API_URL, token="tok")

        provider.apply(MANIFEST_YAML)

        method, url, kwargs = fake.calls[0]
        assert method == "POST"
        assert url == API_URL
        assert kwargs["data"] == MANIFEST_YAML.encode()
        assert kwargs["headers"]["Content-Type"] == "application/x-yaml"
        assert kwargs["headers"]["Authorization"] == "Bearer tok"

    def test_success_result_on_status_below_400(self, monkeypatch):
        _patch_requests(monkeypatch, response=_FakeResponse(status_code=201, text="applied"))
        provider = CustomAPIProvider(api_url=API_URL)

        result = provider.apply(MANIFEST_YAML)

        assert isinstance(result, DeployResult)
        assert result.success is True
        assert "201" in result.log

    def test_failure_result_on_status_400_or_above(self, monkeypatch):
        _patch_requests(monkeypatch, response=_FakeResponse(status_code=422, text="bad manifest"))
        provider = CustomAPIProvider(api_url=API_URL)

        result = provider.apply(MANIFEST_YAML)

        assert result.success is False
        assert "422" in result.error

    def test_failure_result_not_a_raised_exception_on_network_error(self, monkeypatch):
        """Unlike test_connection, apply must never raise on a network
        failure — it reports the failure back through DeployResult so the
        deploy worker's own try/except (which expects a result object, not
        an exception, from a "normal" failed apply) can record it per
        execution without treating it as an unexpected crash.
        """
        _patch_requests(monkeypatch, exc=requests.RequestException("timed out"))
        provider = CustomAPIProvider(api_url=API_URL)

        result = provider.apply(MANIFEST_YAML)

        assert result.success is False
        assert "timed out" in result.error


class TestDelete:
    def test_sends_manifest_yaml_as_the_delete_request_body(self, monkeypatch):
        """The unusual part of this contract: DELETE carries a body (the
        same rendered YAML apply() would have POSTed), a deliberate
        "symmetric assumption to apply()'s POST" per the provider's own
        docstring — not a body-less DELETE, which would otherwise look like
        the more RFC-conventional choice to someone "fixing" this later.
        """
        fake = _patch_requests(monkeypatch)
        provider = CustomAPIProvider(api_url=API_URL)

        provider.delete(MANIFEST_YAML)

        method, url, kwargs = fake.calls[0]
        assert method == "DELETE"
        assert url == API_URL
        assert kwargs["data"] == MANIFEST_YAML.encode()
        assert kwargs["headers"]["Content-Type"] == "application/x-yaml"

    def test_success_result_on_status_below_400(self, monkeypatch):
        _patch_requests(monkeypatch, response=_FakeResponse(status_code=204, text=""))
        provider = CustomAPIProvider(api_url=API_URL)

        result = provider.delete(MANIFEST_YAML)
        assert result.success is True

    def test_failure_result_on_status_400_or_above(self, monkeypatch):
        _patch_requests(monkeypatch, response=_FakeResponse(status_code=500, text="agent error"))
        provider = CustomAPIProvider(api_url=API_URL)

        result = provider.delete(MANIFEST_YAML)
        assert result.success is False
        assert "500" in result.error

    def test_failure_result_not_a_raised_exception_on_network_error(self, monkeypatch):
        _patch_requests(monkeypatch, exc=requests.RequestException("connection reset"))
        provider = CustomAPIProvider(api_url=API_URL)

        result = provider.delete(MANIFEST_YAML)
        assert result.success is False
        assert "connection reset" in result.error


class TestUnsupportedOperations:
    def test_get_live_status_raises_without_making_a_request(self, monkeypatch):
        fake = _patch_requests(monkeypatch)
        provider = CustomAPIProvider(api_url=API_URL)

        with pytest.raises(NotImplementedError):
            provider.get_live_status(MANIFEST_YAML)
        assert fake.calls == []

    def test_restart_raises_without_making_a_request(self, monkeypatch):
        fake = _patch_requests(monkeypatch)
        provider = CustomAPIProvider(api_url=API_URL)

        with pytest.raises(NotImplementedError):
            provider.restart(MANIFEST_YAML)
        assert fake.calls == []
