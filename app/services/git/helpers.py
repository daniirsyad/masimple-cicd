from app.services.git.factory import get_git_provider
from app.utils.crypto import decrypt


def provider_for_git_source(git_source):
    """Builds the right GitProvider for a GitSource row, decrypting its
    stored token. Shared by /github, /builders (branch/Dockerfile lookups),
    and the build worker (sync_repo at build time) — every call site that
    needs to act on a registered repo's connection.
    """
    return get_git_provider(git_source.provider_type, token=decrypt(git_source.encrypted_token))
