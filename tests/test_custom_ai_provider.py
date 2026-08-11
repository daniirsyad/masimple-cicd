import pytest

from app.services.ai import custom_api as custom_api_module
from app.services.ai.custom_api import CustomAPIProvider

ENDPOINT_URL = "https://ai.example.com/generate"


_NOT_JSON = object()


class _FakeResponse:
    def __init__(self, status_code=200, json_data=_NOT_JSON, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._json_data is _NOT_JSON:
            raise ValueError("not JSON")
        return self._json_data


class TestConstruction:
    def test_raises_without_an_endpoint_url(self):
        with pytest.raises(ValueError, match="endpoint_url"):
            CustomAPIProvider(api_key="key")


class TestGenerateDescription:
    def test_posts_prompt_and_model_with_bearer_auth(self, monkeypatch):
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _FakeResponse(200, json_data={"description": "a generated description"})

        monkeypatch.setattr(custom_api_module.requests, "post", fake_post)
        provider = CustomAPIProvider(api_key="secret-key", model_name="my-model", endpoint_url=ENDPOINT_URL)

        result = provider.generate_description("Summarize these commits")

        assert result == "a generated description"
        assert captured["url"] == ENDPOINT_URL
        assert captured["kwargs"]["headers"]["Authorization"] == "Bearer secret-key"
        assert captured["kwargs"]["json"] == {"prompt": "Summarize these commits", "model": "my-model"}

    def test_omits_authorization_header_when_no_api_key_is_configured(self, monkeypatch):
        captured = {}

        def fake_post(url, **kwargs):
            captured["kwargs"] = kwargs
            return _FakeResponse(200, json_data={"description": "text"})

        monkeypatch.setattr(custom_api_module.requests, "post", fake_post)
        provider = CustomAPIProvider(api_key=None, endpoint_url=ENDPOINT_URL)

        provider.generate_description("prompt")

        assert "Authorization" not in captured["kwargs"]["headers"]

    def test_omits_model_from_the_payload_when_not_configured(self, monkeypatch):
        captured = {}

        def fake_post(url, **kwargs):
            captured["kwargs"] = kwargs
            return _FakeResponse(200, json_data={"description": "text"})

        monkeypatch.setattr(custom_api_module.requests, "post", fake_post)
        provider = CustomAPIProvider(endpoint_url=ENDPOINT_URL)

        provider.generate_description("prompt")

        assert captured["kwargs"]["json"] == {"prompt": "prompt"}

    def test_accepts_a_bare_json_string_response(self, monkeypatch):
        monkeypatch.setattr(
            custom_api_module.requests, "post", lambda url, **kwargs: _FakeResponse(200, json_data="plain result")
        )
        provider = CustomAPIProvider(endpoint_url=ENDPOINT_URL)

        assert provider.generate_description("prompt") == "plain result"

    def test_accepts_a_non_json_plain_text_response(self, monkeypatch):
        response = _FakeResponse(200, text="  raw text body  ")  # json_data defaults to _NOT_JSON
        monkeypatch.setattr(custom_api_module.requests, "post", lambda url, **kwargs: response)
        provider = CustomAPIProvider(endpoint_url=ENDPOINT_URL)

        assert provider.generate_description("prompt") == "raw text body"

    def test_raises_on_an_unexpected_json_shape(self, monkeypatch):
        monkeypatch.setattr(
            custom_api_module.requests,
            "post",
            lambda url, **kwargs: _FakeResponse(200, json_data={"unexpected": "shape"}),
        )
        provider = CustomAPIProvider(endpoint_url=ENDPOINT_URL)

        with pytest.raises(RuntimeError, match="Unexpected response shape"):
            provider.generate_description("prompt")

    def test_raises_on_an_http_error_status(self, monkeypatch):
        monkeypatch.setattr(custom_api_module.requests, "post", lambda url, **kwargs: _FakeResponse(500))
        provider = CustomAPIProvider(endpoint_url=ENDPOINT_URL)

        with pytest.raises(RuntimeError):
            provider.generate_description("prompt")
