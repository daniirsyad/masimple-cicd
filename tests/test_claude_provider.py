import pytest

from app.services.ai import claude as claude_module
from app.services.ai.claude import ClaudeProvider


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
        provider = ClaudeProvider(api_key=None)
        with pytest.raises(RuntimeError, match="API key"):
            provider.generate_description("some prompt")

    def test_posts_with_x_api_key_header_and_anthropic_version(self, monkeypatch):
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _FakeResponse(200, {"content": [{"text": "a generated description"}]})

        monkeypatch.setattr(claude_module.requests, "post", fake_post)
        provider = ClaudeProvider(api_key="sk-ant-test", model_name="claude-sonnet-5")

        result = provider.generate_description("Summarize these commits")

        assert result == "a generated description"
        assert captured["url"] == claude_module.MESSAGES_URL
        assert captured["kwargs"]["headers"]["x-api-key"] == "sk-ant-test"
        assert captured["kwargs"]["headers"]["anthropic-version"] == claude_module.ANTHROPIC_VERSION
        assert "Authorization" not in captured["kwargs"]["headers"]
        assert captured["kwargs"]["json"]["model"] == "claude-sonnet-5"
        assert captured["kwargs"]["json"]["messages"] == [
            {"role": "user", "content": "Summarize these commits"}
        ]

    def test_falls_back_to_the_default_model_when_none_is_configured(self, monkeypatch):
        captured = {}

        def fake_post(url, **kwargs):
            captured["kwargs"] = kwargs
            return _FakeResponse(200, {"content": [{"text": "text"}]})

        monkeypatch.setattr(claude_module.requests, "post", fake_post)
        provider = ClaudeProvider(api_key="sk-ant-test", model_name=None)

        provider.generate_description("prompt")

        assert captured["kwargs"]["json"]["model"] == claude_module.DEFAULT_CLAUDE_MODEL

    def test_strips_whitespace_from_the_returned_text(self, monkeypatch):
        monkeypatch.setattr(
            claude_module.requests,
            "post",
            lambda url, **kwargs: _FakeResponse(200, {"content": [{"text": "  padded text  \n"}]}),
        )
        provider = ClaudeProvider(api_key="sk-ant-test")

        assert provider.generate_description("prompt") == "padded text"

    def test_raises_on_an_http_error_status(self, monkeypatch):
        monkeypatch.setattr(claude_module.requests, "post", lambda url, **kwargs: _FakeResponse(401))
        provider = ClaudeProvider(api_key="bad-key")

        with pytest.raises(RuntimeError):
            provider.generate_description("prompt")
