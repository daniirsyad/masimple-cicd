from abc import ABC, abstractmethod


class RegistryProvider(ABC):
    """Provider-agnostic interface for authenticating with and pushing to a registry.

    Methods take an already-constructed docker-py client (owned by `BuildEngine`)
    rather than each provider managing its own client, so the same authenticated
    connection used to build an image is reused to push it.
    """

    @property
    @abstractmethod
    def registry_host(self):
        """The registry's API hostname (e.g. `registry-1.docker.io`) — the key a
        `~/.docker/config.json`-style auth entry needs, for build engines
        (Kaniko) that authenticate via that file instead of a docker-py client.
        """

    @abstractmethod
    def full_repository_name(self, repository):
        """Return the fully-qualified repository name (including this registry's
        namespace/username) that `repository` will actually be pushed/pulled under —
        e.g. Docker Hub prepends the account name if `repository` has no "/" in it.
        """

    @abstractmethod
    def authenticate(self, docker_client):
        """Log `docker_client` in to this registry. Returns the docker-py login response."""

    @abstractmethod
    def push_image(self, docker_client, repository, tag):
        """Push `repository:tag` to this registry. Returns the stream of push events."""

    @abstractmethod
    def list_tags(self, repository):
        """Return a list of tag names that already exist for `repository` on this registry."""

    @abstractmethod
    def validate_credentials(self):
        """Sanity-check this provider's configured credentials against the
        registry, without needing a specific repository — used when saving a
        RegistryTarget, so a bad token is caught immediately instead of
        failing on the next build. Returns True if valid; raises
        RuntimeError with a clear message otherwise.
        """
