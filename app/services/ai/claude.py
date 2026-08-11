import requests

from app.services.ai.base import AIProvider

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024


class ClaudeProvider(AIProvider):
    def generate_description(self, context) -> str:
        if not self.api_key:
            raise RuntimeError(
                "No Claude API key configured (set CLAUDE_API_KEY, or configure one in "
                "AI Provider Settings)."
            )

        response = requests.post(
            MESSAGES_URL,
            headers={
                # Anthropic's Messages API authenticates via x-api-key, not
                # a Bearer Authorization header like Qwen's OpenAI-compatible
                # endpoint.
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_name or DEFAULT_CLAUDE_MODEL,
                "max_tokens": MAX_TOKENS,
                "messages": [{"role": "user", "content": context}],
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"].strip()
