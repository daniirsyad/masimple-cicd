from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class DeployResult:
    success: bool
    log: str
    error: str | None = None


class DeploymentProvider(ABC):
    """Provider-agnostic interface for applying a rendered manifest to a
    target DeploymentServer.

    Implemented first as KubernetesProvider (kubectl apply against a stored
    kubeconfig) and CustomAPIProvider (POST to a client-side agent endpoint)
    — a third implementation is just a new class + factory entry, no
    calling-code changes needed (see app/services/deployment/factory.py).
    """

    @abstractmethod
    def test_connection(self):
        """Sanity-check this provider's configured credentials against the
        target, without applying anything — used when saving/testing a
        DeploymentServer, so a bad credential is caught immediately instead
        of failing on the next deploy. Returns True if reachable; raises
        RuntimeError with a clear message otherwise.
        """

    @abstractmethod
    def apply(self, manifest_yaml, wait_timeout_seconds=None):
        """Apply `manifest_yaml` (already placeholder-resolved, see
        app/services/deployment/resolver.py) to this target. Returns a
        DeployResult — never raises for an ordinary apply failure, only for
        a truly unexpected error the caller (the worker) should still catch.

        If `wait_timeout_seconds` is a positive number and this provider
        has a readiness concept (KubernetesProvider), apply() blocks after
        a successful apply, polling until every workload resource in
        `manifest_yaml` reports Ready or the timeout elapses — a timeout
        counts as a failed DeployResult, not a raised exception. None or
        <= 0 skips the wait entirely. A provider with no readiness concept
        (CustomAPIProvider) accepts and silently ignores this argument.
        """

    @abstractmethod
    def delete(self, manifest_yaml):
        """Tear down whatever `apply(manifest_yaml)` previously created —
        the "stop deployment" action (see
        app/services/deployment/worker.py's action="stop" executions).
        `manifest_yaml` is always the exact rendered YAML that was actually
        applied (looked up from the DeploymentExecution being stopped), not
        freshly re-resolved. Same DeployResult contract as apply().
        """

    @abstractmethod
    def get_live_status(self, manifest_yaml):
        """Whether the resources in `manifest_yaml` are still present on
        this target — used by the live-status poller (see
        app/services/deployment/worker.py._status_poll_loop) to drive the
        "currently deployed" badge and gate the Stop button. Returns "live"
        or "missing"; raises NotImplementedError if this provider type has
        no reliable way to check (caught by the poller, which just leaves
        the status as unknown rather than treating it as an error).
        """

    @abstractmethod
    def restart(self, manifest_yaml, wait_timeout_seconds=None):
        """Restart whatever's currently running from `manifest_yaml` — tears
        it down and reapplies it (delete then apply of the same rendered
        YAML) without changing the applied image or config (see
        app/services/deployment/worker.py's action="restart" executions).
        Not a live rolling recycle — there's a real gap with nothing running
        between the delete and the apply. `manifest_yaml` is the exact
        rendered YAML that was actually applied, same as delete(). Same
        DeployResult contract as apply()/delete(); raise NotImplementedError
        if this provider type has no equivalent operation. Same
        `wait_timeout_seconds` contract as apply() — restart's own re-apply
        half waits the same way.
        """
