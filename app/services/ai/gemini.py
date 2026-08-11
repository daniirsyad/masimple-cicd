import requests

from app.services.ai.base import AIProvider

GENERATE_CONTENT_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class GeminiProvider(AIProvider):
    def generate_description(self, context) -> str:
        if not self.api_key:
            raise RuntimeError(
                "No Gemini API key configured (set GEMINI_API_KEY, or configure one in "
                "AI Provider Settings)."
            )

        model = self.model_name or DEFAULT_GEMINI_MODEL
        url = GENERATE_CONTENT_URL_TEMPLATE.format(model=model)
        response = requests.post(
            url,
            # Gemini's REST auth convention is an API key query param, not
            # a header — unlike Qwen's Bearer token or Claude's x-api-key.
            params={"key": self.api_key},
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": context}]}]},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
