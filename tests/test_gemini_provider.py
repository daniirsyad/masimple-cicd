import pytest

from app.services.ai import gemini as gemini_module
from app.services.ai.gemini import GeminiProvider


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


class TestGenerateDescription:
    def test_raises_when_no_api_key_is_configured(self):
        provider = GeminiProvider(api_key=None)
        with pytest.raises(RuntimeError, match="API key"):
            provider.generate_description("some prompt")

    def test_sends_the_api_key_as_a_query_param_not_a_header(self, monkeypatch):
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _FakeResponse(
                200, {"candidates": [{"content": {"parts": [{"text": "a generated description"}]}}]}
            )

        monkeypatch.setattr(gemini_module.requests, "post", fake_post)
        provider = GeminiProvider(api_key="AIzaTest", model_name="gemini-2.5-flash")

        result = provider.generate_description("Summarize these commits")

        assert result == "a generated description"
        assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        assert captured["kwargs"]["params"] == {"key": "AIzaTest"}
        assert "Authorization" not in captured["kwargs"]["headers"]
        assert "key" not in captured["kwargs"]["headers"]
        assert captured["kwargs"]["json"] == {"contents": [{"parts": [{"text": "Summarize these commits"}]}]}

    def test_falls_back_to_the_default_model_when_none_is_configured(self, monkeypatch):
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            return _FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "text"}]}}]})

        monkeypatch.setattr(gemini_module.requests, "post", fake_post)
        provider = GeminiProvider(api_key="AIzaTest", model_name=None)

        provider.generate_description("prompt")

        assert captured["url"].endswith(f"{gemini_module.DEFAULT_GEMINI_MODEL}:generateContent")

    def test_strips_whitespace_from_the_returned_text(self, monkeypatch):
        monkeypatch.setattr(
            gemini_module.requests,
            "post",
            lambda url, **kwargs: _FakeResponse(
                200, {"candidates": [{"content": {"parts": [{"text": "  padded text  \n"}]}}]}
            ),
        )
        provider = GeminiProvider(api_key="AIzaTest")

        assert provider.generate_description("prompt") == "padded text"

    def test_raises_on_an_http_error_status(self, monkeypatch):
        monkeypatch.setattr(gemini_module.requests, "post", lambda url, **kwargs: _FakeResponse(403))
        provider = GeminiProvider(api_key="bad-key")

        with pytest.raises(RuntimeError):
            provider.generate_description("prompt")
