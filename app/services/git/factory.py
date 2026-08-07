from app.services.git.github import GitHubProvider

_PROVIDERS = {
    "github": GitHubProvider,
}


def get_git_provider(provider_type="github", **kwargs):
    try:
        provider_cls = _PROVIDERS[provider_type]
    except KeyError:
        raise ValueError(
            f"Git provider '{provider_type}' is not implemented. "
            f"Supported providers: {', '.join(_PROVIDERS)}."
        ) from None
    return provider_cls(**kwargs)
