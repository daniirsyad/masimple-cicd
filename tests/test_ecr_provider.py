import base64

import botocore.exceptions
import pytest

from app.services.registry import ecr as ecr_module
from app.services.registry.ecr import ECRProvider

ECR_HOST = "123456789012.dkr.ecr.us-east-1.amazonaws.com"


class FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return iter(self._pages)


class FakeECRClient:
    def __init__(self, auth_response=None, pages=None, raise_on_auth=False, raise_on_list=False):
        self.auth_response = auth_response
        self.pages = pages or []
        self.raise_on_auth = raise_on_auth
        self.raise_on_list = raise_on_list
        self.calls = []

    def get_authorization_token(self):
        self.calls.append("get_authorization_token")
        if self.raise_on_auth:
            raise botocore.exceptions.ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetAuthorizationToken")
        return self.auth_response

    def get_paginator(self, name):
        self.calls.append(("get_paginator", name))
        if self.raise_on_list:
            raise botocore.exceptions.ClientError({"Error": {"Code": "RepositoryNotFoundException"}}, "ListImages")
        return FakePaginator(self.pages)


def _patch_boto3_client(monkeypatch, fake_client):
    captured = {}

    def fake_boto3_client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        return fake_client

    monkeypatch.setattr(ecr_module.boto3, "client", fake_boto3_client)
    return captured


def _token(username="AWS", password="temp-password"):
    return base64.b64encode(f"{username}:{password}".encode()).decode()


class TestConstruction:
    def test_raises_without_a_registry_url(self, monkeypatch):
        _patch_boto3_client(monkeypatch, FakeECRClient())
        with pytest.raises(ValueError):
            ECRProvider(username="AKIA...", password="secret")

    def test_raises_when_registry_url_does_not_look_like_an_ecr_host(self, monkeypatch):
        _patch_boto3_client(monkeypatch, FakeECRClient())
        with pytest.raises(ValueError, match="ECR host"):
            ECRProvider(username="AKIA...", password="secret", registry_url="harbor.example.com")

    def test_parses_region_and_constructs_a_client_scoped_to_it(self, monkeypatch):
        fake_client = FakeECRClient()
        captured = _patch_boto3_client(monkeypatch, fake_client)

        ECRProvider(username="AKIA...", password="secret", registry_url=ECR_HOST)

        assert captured["service_name"] == "ecr"
        assert captured["kwargs"]["region_name"] == "us-east-1"
        assert captured["kwargs"]["aws_access_key_id"] == "AKIA..."
        assert captured["kwargs"]["aws_secret_access_key"] == "secret"

    def test_strips_scheme_from_registry_url(self, monkeypatch):
        _patch_boto3_client(monkeypatch, FakeECRClient())
        provider = ECRProvider(username="AKIA...", password="secret", registry_url=f"https://{ECR_HOST}/")
        assert provider.registry_host == ECR_HOST


class TestFullRepositoryName:
    def test_prepends_host_for_a_bare_repository_name(self, monkeypatch):
        _patch_boto3_client(monkeypatch, FakeECRClient())
        provider = ECRProvider(username="AKIA...", password="secret", registry_url=ECR_HOST)
        assert provider.full_repository_name("myapp") == f"{ECR_HOST}/myapp"

    def test_leaves_an_already_fully_qualified_name_unchanged(self, monkeypatch):
        _patch_boto3_client(monkeypatch, FakeECRClient())
        provider = ECRProvider(username="AKIA...", password="secret", registry_url=ECR_HOST)
        assert provider.full_repository_name(f"{ECR_HOST}/myapp") == f"{ECR_HOST}/myapp"


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
    def test_raises_when_credentials_are_not_configured(self, monkeypatch):
        _patch_boto3_client(monkeypatch, FakeECRClient())
        provider = ECRProvider(registry_url=ECR_HOST)

        with pytest.raises(RuntimeError, match="not configured"):
            provider.authenticate(FakeDockerClient([]))

    def test_raises_when_the_token_request_is_denied(self, monkeypatch):
        _patch_boto3_client(monkeypatch, FakeECRClient(raise_on_auth=True))
        provider = ECRProvider(username="AKIA...", password="secret", registry_url=ECR_HOST)

        with pytest.raises(RuntimeError, match="authorization token"):
            provider.authenticate(FakeDockerClient([]))

    def test_decodes_the_authorization_token_and_logs_in(self, monkeypatch):
        auth_response = {
            "authorizationData": [
                {"authorizationToken": _token("AWS", "temp-pass-123"), "proxyEndpoint": f"https://{ECR_HOST}"}
            ]
        }
        _patch_boto3_client(monkeypatch, FakeECRClient(auth_response=auth_response))
        provider = ECRProvider(username="AKIA...", password="secret", registry_url=ECR_HOST)
        client = FakeDockerClient([])

        provider.authenticate(client)

        assert client.login_calls == [("AWS", "temp-pass-123", ECR_HOST)]


class TestPushImage:
    def test_raises_when_push_stream_contains_an_error_event(self, monkeypatch):
        auth_response = {
            "authorizationData": [{"authorizationToken": _token(), "proxyEndpoint": f"https://{ECR_HOST}"}]
        }
        _patch_boto3_client(monkeypatch, FakeECRClient(auth_response=auth_response))
        provider = ECRProvider(username="AKIA...", password="secret", registry_url=ECR_HOST)
        client = FakeDockerClient([{"error": "denied"}])

        with pytest.raises(RuntimeError, match="denied"):
            provider.push_image(client, "myapp", "1.0.0")

    def test_pushes_under_the_fully_qualified_ecr_name(self, monkeypatch):
        auth_response = {
            "authorizationData": [{"authorizationToken": _token(), "proxyEndpoint": f"https://{ECR_HOST}"}]
        }
        _patch_boto3_client(monkeypatch, FakeECRClient(auth_response=auth_response))
        provider = ECRProvider(username="AKIA...", password="secret", registry_url=ECR_HOST)
        client = FakeDockerClient([{"status": "Pushed"}])

        result = provider.push_image(client, "myapp", "1.0.0")

        assert result == [{"status": "Pushed"}]
        assert client.images.push_calls == [(f"{ECR_HOST}/myapp", "1.0.0")]


class TestListTags:
    def test_collects_tags_across_pages(self, monkeypatch):
        pages = [
            {"imageIds": [{"imageTag": "1.0.0"}, {"imageDigest": "sha256:abc"}]},
            {"imageIds": [{"imageTag": "1.0.1"}]},
        ]
        _patch_boto3_client(monkeypatch, FakeECRClient(pages=pages))
        provider = ECRProvider(username="AKIA...", password="secret", registry_url=ECR_HOST)

        assert provider.list_tags("myapp") == ["1.0.0", "1.0.1"]

    def test_raises_a_runtime_error_on_a_client_error(self, monkeypatch):
        _patch_boto3_client(monkeypatch, FakeECRClient(raise_on_list=True))
        provider = ECRProvider(username="AKIA...", password="secret", registry_url=ECR_HOST)

        with pytest.raises(RuntimeError, match="Could not list ECR tags"):
            provider.list_tags("myapp")


class TestValidateCredentials:
    def test_raises_when_no_credentials_are_configured(self, monkeypatch):
        _patch_boto3_client(monkeypatch, FakeECRClient())
        provider = ECRProvider(registry_url=ECR_HOST)

        with pytest.raises(RuntimeError, match="not configured"):
            provider.validate_credentials()

    def test_raises_when_the_token_request_is_denied(self, monkeypatch):
        _patch_boto3_client(monkeypatch, FakeECRClient(raise_on_auth=True))
        provider = ECRProvider(username="AKIA...", password="secret", registry_url=ECR_HOST)

        with pytest.raises(RuntimeError, match="authentication failed"):
            provider.validate_credentials()

    def test_returns_true_on_success(self, monkeypatch):
        auth_response = {
            "authorizationData": [{"authorizationToken": _token(), "proxyEndpoint": f"https://{ECR_HOST}"}]
        }
        _patch_boto3_client(monkeypatch, FakeECRClient(auth_response=auth_response))
        provider = ECRProvider(username="AKIA...", password="secret", registry_url=ECR_HOST)

        assert provider.validate_credentials() is True
