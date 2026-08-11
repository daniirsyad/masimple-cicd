import requests

from app.services.ai.base import AIProvider


class CustomAPIProvider(AIProvider):
    """POSTs the fully-rendered prompt to a generic external AI endpoint —
    the "or Custom API" flexibility case, for a model server that isn't
    Qwen/Claude/Gemini specifically. Assumes the endpoint accepts a JSON
    body `{"prompt": ..., "model": ...}`, optional Bearer auth, and returns
    either a plain-text body or `{"description": "..."}` JSON — the only
    contract this app can assume without a concrete target to match
    against; adjust here if a real endpoint's contract differs. Mirrors
    app/services/deployment/custom_api_provider.py's own "document the
    assumed contract rather than guess silently" approach for the same
    reason (no real target exists yet).
    """

    def __init__(self, api_key=None, model_name=None, endpoint_url=None):
        super().__init__(api_key=api_key, model_name=model_name, endpoint_url=endpoint_url)
        if not endpoint_url:
            raise ValueError(
                "CustomAPIProvider requires an endpoint_url — set it on this provider's "
                "row in AI Provider Settings."
            )

    def generate_description(self, context) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {"prompt": context}
        if self.model_name:
            payload["model"] = self.model_name

        response = requests.post(self.endpoint_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError:
            # Not JSON — treat the raw body itself as the description, the
            # simplest possible contract for a bare-bones custom endpoint.
            return response.text.strip()

        if isinstance(data, str):
            return data.strip()
        if isinstance(data, dict) and "description" in data:
            return str(data["description"]).strip()

        raise RuntimeError(f"Unexpected response shape from custom AI endpoint: {data!r}")
