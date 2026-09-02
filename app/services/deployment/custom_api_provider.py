import requests

from app.services.deployment.base import DeployResult, DeploymentProvider

REQUEST_TIMEOUT_SECONDS = 30


class CustomAPIProvider(DeploymentProvider):
    """POSTs the rendered manifest to a client-side agent endpoint — the "or
    API" flexibility case, for targets that aren't a Kubernetes cluster
    reachable via kubectl (e.g. a lightweight agent process on the target
    server that applies the manifest itself).

    Assumes the agent endpoint accepts GET for a lightweight reachability/
    auth check (test_connection) and POST with the raw YAML body to apply it
    — the only contract this app can assume without a concrete agent
    implementation to match against; adjust here if a real agent's contract
    differs.
    """

    def __init__(self, api_url, token=None):
        if not api_url:
            raise ValueError("CustomAPIProvider requires an api_url.")
        self.api_url = api_url
        self.token = token

    def _headers(self):
        headers = {"Content-Type": "application/x-yaml"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def test_connection(self):
        try:
            response = requests.get(self.api_url, headers=self._headers(), timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise RuntimeError(f"Could not reach {self.api_url}: {exc}") from None
        if response.status_code >= 400:
            raise RuntimeError(f"{self.api_url} responded with {response.status_code}: {response.text[:500]}")
        return True

    def apply(self, manifest_yaml, wait_timeout_seconds=None):
        # No rollout/readiness concept for an "api"-type target —
        # wait_timeout_seconds is accepted and silently ignored, same
        # pattern as get_live_status/restart's NotImplementedError being
        # caught gracefully by their own callers.
        try:
            response = requests.post(
                self.api_url,
                data=manifest_yaml.encode(),
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = f"POST {self.api_url} -> {response.status_code}\n{response.text}"
        if response.status_code >= 400:
            return DeployResult(success=False, log=log, error=f"Agent responded with {response.status_code}")
        return DeployResult(success=True, log=log)

    def delete(self, manifest_yaml):
        # Symmetric assumption to apply()'s POST — same "adjust here if a
        # real agent's contract differs" caveat as the class docstring.
        try:
            response = requests.delete(
                self.api_url,
                data=manifest_yaml.encode(),
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = f"DELETE {self.api_url} -> {response.status_code}\n{response.text}"
        if response.status_code >= 400:
            return DeployResult(success=False, log=log, error=f"Agent responded with {response.status_code}")
        return DeployResult(success=True, log=log)

    def get_live_status(self, manifest_yaml):
        # No agent contract exists for "is this specific manifest still
        # applied" — a bare GET can only prove the agent is reachable, not
        # what's currently deployed through it. The live-status poller
        # treats this as "unknown" rather than guessing.
        raise NotImplementedError("Live-status checks aren't supported for 'api'-type deployment servers.")

    def restart(self, manifest_yaml, wait_timeout_seconds=None):
        # No agent contract exists for "restart what you already applied,
        # unchanged" either — unlike delete (a plausible symmetric guess at
        # POST/DELETE), there's no obvious HTTP verb for "restart", so this
        # isn't guessed at. The Restart button/route surfaces this as an
        # explicit "not supported" rather than silently doing nothing.
        raise NotImplementedError("Restart isn't supported for 'api'-type deployment servers.")
