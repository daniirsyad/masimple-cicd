import os

from app.models import AIProviderConfig
from app.utils.crypto import decrypt
from app.services.ai.qwen import QwenProvider

_PROVIDERS = {
    "qwen": QwenProvider,
}


def _resolve_api_key(provider_type, config):
    if config is not None and config.encrypted_api_key:
        return decrypt(config.encrypted_api_key)
    return os.environ.get(f"{provider_type.upper()}_API_KEY")


def _default_config():
    return (
        AIProviderConfig.query.filter_by(is_active=True, is_default=True).first()
        or AIProviderConfig.query.filter_by(is_active=True).first()
    )


def default_provider_type():
    """The provider_type `get_ai_provider(None)` would resolve to — for
    callers (the two AI-assist "generate description" endpoints) that need
    to echo back which provider was actually used, without duplicating this
    resolution logic themselves.
    """
    config = _default_config()
    return config.provider_type if config else "qwen"


def get_ai_provider(provider_type=None):
    """Build an AIProvider for `provider_type`, or the configured default if omitted.

    Looks up `AIProviderConfig` for model name and API key (DB value takes
    precedence; falls back to an env var by naming convention). Raises a clear
    error for provider types not yet implemented (Claude/Gemini/Custom API),
    so adding them later is just a new class + registry entry here.
    """
    config = None

    if provider_type is None:
        config = _default_config()
        provider_type = config.provider_type if config else "qwen"
    else:
        config = AIProviderConfig.query.filter_by(
            provider_type=provider_type, is_active=True
        ).first()

    try:
        provider_cls = _PROVIDERS[provider_type]
    except KeyError:
        raise ValueError(
            f"AI provider '{provider_type}' is not implemented yet. "
            f"Supported providers: {', '.join(_PROVIDERS)}."
        ) from None

    api_key = _resolve_api_key(provider_type, config)
    model_name = config.model_name if config else None
    return provider_cls(api_key=api_key, model_name=model_name)
