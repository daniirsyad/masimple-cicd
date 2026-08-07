from abc import ABC, abstractmethod


class AIProvider(ABC):
    """Provider-agnostic interface for the version-documentation description assistant.

    Adding a new provider (Claude/Gemini/Custom API) is just a new subclass plus a
    registry entry in `app/services/ai/factory.py` — no calling-code changes.
    """

    def __init__(self, api_key=None, model_name=None):
        self.api_key = api_key
        self.model_name = model_name

    @abstractmethod
    def generate_description(self, context) -> str:
        """Generate a description from `context` (the fully rendered prompt text)."""
