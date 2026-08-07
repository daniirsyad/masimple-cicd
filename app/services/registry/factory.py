from app.services.registry.dockerhub import DockerHubProvider

_PROVIDERS = {
    "dockerhub": DockerHubProvider,
}


def get_registry_provider(provider_type="dockerhub", **kwargs):
    try:
        provider_cls = _PROVIDERS[provider_type]
    except KeyError:
        raise ValueError(
            f"Registry provider '{provider_type}' is not implemented. "
            f"Supported providers: {', '.join(_PROVIDERS)}."
        ) from None
    return provider_cls(**kwargs)
