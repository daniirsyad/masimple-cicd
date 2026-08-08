from app.services.deployment.custom_api_provider import CustomAPIProvider
from app.services.deployment.kubernetes_provider import KubernetesProvider

_PROVIDERS = {
    "kube": KubernetesProvider,
    "api": CustomAPIProvider,
}


def get_deployment_provider(connection_type, **kwargs):
    try:
        provider_cls = _PROVIDERS[connection_type]
    except KeyError:
        raise ValueError(
            f"Deployment connection type '{connection_type}' is not implemented. "
            f"Supported types: {', '.join(_PROVIDERS)}."
        ) from None
    return provider_cls(**kwargs)
