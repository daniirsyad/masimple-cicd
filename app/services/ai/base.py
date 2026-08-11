from abc import ABC, abstractmethod


class AIProvider(ABC):
    """Provider-agnostic interface for the version-documentation description assistant.

    Adding a new provider (Claude/Gemini/Custom API) is just a new subclass plus a
    registry entry in `app/services/ai/factory.py` — no calling-code changes.
    """

    def __init__(self, api_key=None, model_name=None, endpoint_url=None):
        self.api_key = api_key
        self.model_name = model_name
        # Only CustomAPIProvider actually uses this — every provider takes
        # the same superset of constructor kwargs (get_ai_provider always
        # passes it) so a not-yet-implemented-here provider doesn't need
        # special-casing at the call site, mirroring how every
        # RegistryProvider now also accepts registry_url even where unused.
        self.endpoint_url = endpoint_url

    @abstractmethod
    def generate_description(self, context) -> str:
        """Generate a description from `context` (the fully rendered prompt text)."""
