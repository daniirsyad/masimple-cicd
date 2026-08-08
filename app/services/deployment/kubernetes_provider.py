import json
import os
import subprocess
import tempfile

from app.services.deployment.base import DeployResult, DeploymentProvider

POD_LIST_TIMEOUT_SECONDS = 15
POD_LOGS_TIMEOUT_SECONDS = 30
POD_LOG_TAIL_LINES = 500

KUBECTL_PATH = os.environ.get("KUBECTL_PATH", "kubectl")
TEST_CONNECTION_TIMEOUT_SECONDS = 10
APPLY_TIMEOUT_SECONDS = 120


class KubernetesProvider(DeploymentProvider):
    """Shells out to `kubectl` against a stored kubeconfig, rather than a
    hand-rolled Kubernetes REST client — same precedent as
    app/services/build/engine.py shelling out to `docker buildx build` /
    `kaniko-executor` instead of reimplementing their protocols. `kubectl
    apply` already handles API discovery, namespacing, and merge semantics
    correctly for arbitrary manifest YAML (including multi-document YAML),
    which a raw `requests`-based client would otherwise have to reimplement
    from scratch for every resource kind.
    """

    def __init__(self, kubeconfig):
        if not kubeconfig:
            raise ValueError("KubernetesProvider requires a kubeconfig.")
        self.kubeconfig = kubeconfig

    def _run_kubectl(self, args, input_text=None, timeout=APPLY_TIMEOUT_SECONDS):
        # Written fresh per call (not cached) — same one-shot-tempfile
        # approach as KanikoBuildEngine._write_docker_config, so a stale
        # config never lingers on disk between calls.
        with tempfile.TemporaryDirectory() as config_dir:
            config_path = os.path.join(config_dir, "kubeconfig.yaml")
            with open(config_path, "w") as config_file:
                config_file.write(self.kubeconfig)

            env = {**os.environ, "KUBECONFIG": config_path}
            return subprocess.run(
                [KUBECTL_PATH, *args],
                input=input_text,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )

    def test_connection(self):
        try:
            process = self._run_kubectl(
                ["cluster-info", "--request-timeout=10s"], timeout=TEST_CONNECTION_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not reach cluster: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or "kubectl cluster-info failed.")
        return True

    def apply(self, manifest_yaml):
        try:
            process = self._run_kubectl(["apply", "-f", "-"], input_text=manifest_yaml)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = process.stdout + process.stderr
        if process.returncode != 0:
            return DeployResult(
                success=False, log=log, error=f"kubectl apply exited with code {process.returncode}"
            )
        return DeployResult(success=True, log=log)

    def delete(self, manifest_yaml):
        try:
            # --ignore-not-found: a resource already gone (e.g. removed by
            # hand outside Hamilton) shouldn't fail the stop — the end state
            # ("not present") is what we actually wanted.
            process = self._run_kubectl(["delete", "-f", "-", "--ignore-not-found=true"], input_text=manifest_yaml)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = process.stdout + process.stderr
        if process.returncode != 0:
            return DeployResult(
                success=False, log=log, error=f"kubectl delete exited with code {process.returncode}"
            )
        return DeployResult(success=True, log=log)

    def restart(self, manifest_yaml):
        try:
            # kubectl rollout restart only supports Deployment/DaemonSet/
            # StatefulSet — a manifest that also declares Services/
            # ConfigMaps/etc. alongside a restartable workload will report a
            # per-resource error for those non-restartable kinds even
            # though the actual workload restart still succeeds. Not
            # filtered by kind here (this module never parses YAML — see
            # resolver.py's regex-only placeholder substitution for the
            # same rationale); surfaced as-is in the log for a human to read.
            process = self._run_kubectl(["rollout", "restart", "-f", "-"], input_text=manifest_yaml)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = process.stdout + process.stderr
        if process.returncode != 0:
            return DeployResult(
                success=False, log=log, error=f"kubectl rollout restart exited with code {process.returncode}"
            )
        return DeployResult(success=True, log=log)

    def get_live_status(self, manifest_yaml):
        try:
            process = self._run_kubectl(
                ["get", "-f", "-", "-o", "name"], input_text=manifest_yaml, timeout=TEST_CONNECTION_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not check live status: {exc}") from None

        if process.returncode == 0:
            return "live"
        if "NotFound" in process.stderr:
            return "missing"
        raise RuntimeError(process.stderr.strip() or "kubectl get failed.")

    def list_pods(self, namespace=None):
        """[{name, namespace, phase, ready, restarts, node, created_at}, ...]
        — namespace=None lists across every namespace (--all-namespaces).
        """
        args = ["get", "pods", "-o", "json"]
        args += ["-n", namespace] if namespace else ["--all-namespaces"]

        try:
            process = self._run_kubectl(args, timeout=POD_LIST_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not list pods: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or "kubectl get pods failed.")

        pods = []
        for item in json.loads(process.stdout).get("items", []):
            metadata = item.get("metadata", {})
            status = item.get("status", {})
            containers = status.get("containerStatuses", [])
            pods.append(
                {
                    "name": metadata.get("name"),
                    "namespace": metadata.get("namespace"),
                    "phase": status.get("phase"),
                    "ready": f"{sum(1 for c in containers if c.get('ready'))}/{len(containers)}",
                    "restarts": sum(c.get("restartCount", 0) for c in containers),
                    "node": item.get("spec", {}).get("nodeName"),
                    "created_at": metadata.get("creationTimestamp"),
                }
            )
        return pods

    def pod_logs(self, namespace, pod_name, container=None):
        args = ["logs", pod_name, "-n", namespace, f"--tail={POD_LOG_TAIL_LINES}"]
        if container:
            args += ["-c", container]

        try:
            process = self._run_kubectl(args, timeout=POD_LOGS_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not fetch logs: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or "kubectl logs failed.")
        return process.stdout

    def describe_pod(self, namespace, pod_name):
        try:
            process = self._run_kubectl(
                ["describe", "pod", pod_name, "-n", namespace], timeout=POD_LOGS_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not describe pod: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or "kubectl describe failed.")
        return process.stdout

    def list_resources(self, kubectl_kind, namespace=None, all_namespaces=False):
        """Raw `kubectl get <kind> -o json` items — generic across any
        resource kind (namespaces, nodes, services, ingresses, pv, pvc, ...).
        Summarizing each item into display columns is left to the caller
        (see deployment_pods.routes.RESOURCE_KINDS) since that's inherently
        per-kind, not something the provider should know about.
        """
        args = ["get", kubectl_kind, "-o", "json"]
        if all_namespaces:
            args.append("--all-namespaces")
        elif namespace:
            args += ["-n", namespace]

        try:
            process = self._run_kubectl(args, timeout=POD_LIST_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not list {kubectl_kind}: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or f"kubectl get {kubectl_kind} failed.")
        return json.loads(process.stdout).get("items", [])

    def describe_resource(self, kubectl_kind, name, namespace=None):
        args = ["describe", kubectl_kind, name]
        if namespace:
            args += ["-n", namespace]

        try:
            process = self._run_kubectl(args, timeout=POD_LOGS_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not describe {kubectl_kind}/{name}: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or f"kubectl describe {kubectl_kind} failed.")
        return process.stdout
