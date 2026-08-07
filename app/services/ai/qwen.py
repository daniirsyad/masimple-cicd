import os

import requests

from app.services.ai.base import AIProvider

# DashScope's OpenAI-compatible chat completions endpoint. Alibaba Cloud publishes
# several regional/product variants (classic DashScope international/China-mainland,
# and newer per-workspace Model Studio URLs) that shift over time, so this is
# overridable via QWEN_API_BASE_URL rather than hardcoded to one region/account type.
DEFAULT_QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_QWEN_MODEL = "qwen-plus"


class QwenProvider(AIProvider):
    def generate_description(self, context) -> str:
        if not self.api_key:
            raise RuntimeError(
                "No Qwen API key configured (set QWEN_API_KEY, or configure one in "
                "AI Provider Settings)."
            )

        base_url = os.environ.get("QWEN_API_BASE_URL", DEFAULT_QWEN_BASE_URL)
        response = requests.post(
            base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_name or DEFAULT_QWEN_MODEL,
                "messages": [{"role": "user", "content": context}],
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
