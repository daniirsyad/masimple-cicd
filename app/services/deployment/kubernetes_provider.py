import base64
import json
import math
import os
import subprocess
import tempfile
import time

import yaml

from app.services.deployment.base import DeployResult, DeploymentProvider

POD_LIST_TIMEOUT_SECONDS = 15
POD_LOGS_TIMEOUT_SECONDS = 30
POD_LOG_TAIL_LINES = 500

KUBECTL_PATH = os.environ.get("KUBECTL_PATH", "kubectl")
TEST_CONNECTION_TIMEOUT_SECONDS = 10
APPLY_TIMEOUT_SECONDS = 120

# The only three kinds `kubectl rollout status`/`rollout restart` actually
# understand — same set restart()'s own docstring already calls out.
ROLLOUT_WAIT_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}
# Buffer over a rollout wait's own requested timeout so `kubectl rollout
# status --timeout=Ns`'s graceful timeout always fires (and returns a clean
# nonzero exit) before this module's own hard-deadline kill would.
ROLLOUT_STATUS_TIMEOUT_BUFFER_SECONDS = 15

# A container `state.waiting.reason` in this set means the workload is never
# going to recover on its own — the kubelet only sets these after at least
# one real failed attempt (a failed pull, or a crash-and-restart), so there's
# no flakiness from reacting on first sight. Seeing one of these means the
# rollout wait should stop immediately rather than waiting out the full
# configured timeout.
FAIL_FAST_WAITING_REASONS = {
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
    "CreateContainerConfigError",
    "CreateContainerError",
}
# How often the rollout wait re-checks pod statuses for the reasons above
# while `kubectl rollout status` is still running.
ROLLOUT_POLL_INTERVAL_SECONDS = 3


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

    def apply(self, manifest_yaml, wait_timeout_seconds=None):
        try:
            process = self._run_kubectl(["apply", "-f", "-"], input_text=manifest_yaml)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = process.stdout + process.stderr
        if process.returncode != 0:
            return DeployResult(
                success=False, log=log, error=f"kubectl apply exited with code {process.returncode}"
            )

        if not wait_timeout_seconds or wait_timeout_seconds <= 0:
            return DeployResult(success=True, log=log)

        rollout_result = self._wait_for_rollout(manifest_yaml, wait_timeout_seconds)
        combined_log = log + "\n" + rollout_result.log
        return DeployResult(success=rollout_result.success, log=combined_log, error=rollout_result.error)

    def delete(self, manifest_yaml):
        try:
            # --ignore-not-found: a resource already gone (e.g. removed by
            # hand outside MASIMPLE CICD) shouldn't fail the stop — the end state
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

    def restart(self, manifest_yaml, wait_timeout_seconds=None):
        """Tears down and reapplies exactly what's already applied — a
        delete() of `manifest_yaml` followed by an apply() of the same
        text, rather than `kubectl rollout restart -f -`. Deliberately not
        that: rollout restart only understands Deployment/DaemonSet/
        StatefulSet, so any manifest that also declares Services/ConfigMaps/
        etc. alongside a restartable workload used to report per-resource
        errors for those other kinds even though the actual workload
        restart succeeded. delete()+apply() work uniformly across whatever
        resource kinds the manifest actually contains. Trade-off: this is a
        real teardown-then-recreate, not a live rolling recycle — there's a
        genuine gap with nothing running in between, unlike a rollout
        restart's zero-downtime pod-by-pod replacement.

        `wait_timeout_seconds` is threaded straight into the apply() half —
        no separate rollout-wait logic here, restart's readiness wait is
        just apply's.
        """
        delete_result = self.delete(manifest_yaml)
        if not delete_result.success:
            return delete_result

        apply_result = self.apply(manifest_yaml, wait_timeout_seconds=wait_timeout_seconds)
        combined_log = delete_result.log + "\n" + apply_result.log
        return DeployResult(success=apply_result.success, log=combined_log, error=apply_result.error)

    def _rollout_targets(self, manifest_yaml):
        """[(kind, name, namespace_or_None, match_labels), ...] for every
        Deployment/StatefulSet/DaemonSet document in `manifest_yaml`, in
        document order — the three kinds `kubectl rollout status`
        understands (see restart()'s own docstring for why `rollout
        restart -f -` isn't used elsewhere either). A manifest with none of
        these (ConfigMap/Secret/Service/... only) yields an empty list,
        meaning "nothing to wait on". This only ever runs after a
        *successful* `kubectl apply` of this exact text (see apply()),
        which would itself have already rejected unparsable YAML — but a
        YAMLError is still swallowed defensively rather than raised, since a
        wait-target scan failing must never turn an already-successful
        apply into a hard error.

        `match_labels` is `spec.selector.matchLabels` (a dict), used by
        `_pod_fail_fast_reason()` to find this target's own pods. An empty
        dict (selector absent, or expressed only via `matchExpressions`
        rather than `matchLabels` — out of scope here) means "skip fail-fast
        detection for this target", never an error — it just falls back to
        plain timeout-only waiting.
        """
        targets = []
        try:
            documents = yaml.safe_load_all(manifest_yaml)
            for doc in documents:
                if not isinstance(doc, dict):
                    continue
                if doc.get("kind") not in ROLLOUT_WAIT_KINDS:
                    continue
                metadata = doc.get("metadata") or {}
                name = metadata.get("name")
                if name:
                    spec = doc.get("spec") or {}
                    match_labels = (spec.get("selector") or {}).get("matchLabels") or {}
                    targets.append((doc["kind"], name, metadata.get("namespace"), match_labels))
        except yaml.YAMLError:
            return []
        return targets

    def _pod_fail_fast_reason(self, namespace, match_labels):
        """(reason, pod_name) for the first pod matching `match_labels`
        whose container (or init container) is waiting on one of
        FAIL_FAST_WAITING_REASONS — (None, None) if nothing bad is found.

        This is a best-effort supplementary probe: any error running or
        parsing `kubectl get pods` (including a nonzero exit) is treated the
        same as "nothing found" rather than raised — a flaky check must
        never itself fail an otherwise-healthy deploy. It's also poll-based
        sampling, not a watch, so a container that flaps in and out of a bad
        state between polls can in principle be missed; acceptable, not
        something this needs to solve.
        """
        selector = ",".join(f"{key}={value}" for key, value in sorted(match_labels.items()))
        args = ["get", "pods", "-l", selector, "-o", "json"]
        if namespace:
            args += ["-n", namespace]

        try:
            process = self._run_kubectl(args, timeout=POD_LIST_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            return None, None
        if process.returncode != 0:
            return None, None

        try:
            items = json.loads(process.stdout).get("items", [])
        except json.JSONDecodeError:
            return None, None

        for pod in items:
            status = pod.get("status") or {}
            statuses = status.get("containerStatuses", []) + status.get("initContainerStatuses", [])
            for container_status in statuses:
                reason = ((container_status.get("state") or {}).get("waiting") or {}).get("reason")
                if reason in FAIL_FAST_WAITING_REASONS:
                    return reason, (pod.get("metadata") or {}).get("name")
        return None, None

    @staticmethod
    def _terminate_process(process):
        """Same terminate/wait-with-timeout/kill fallback stream_pod_logs()
        already uses for its own long-lived Popen.
        """
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def _wait_for_rollout(self, manifest_yaml, wait_timeout_seconds):
        """Runs `kubectl rollout status <kind>/<name> [-n <namespace>]
        --timeout=<remaining>s` for every workload _rollout_targets() finds,
        sequentially, against ONE SHARED DEADLINE
        (time.monotonic() + wait_timeout_seconds, set once up front) rather
        than a fresh wait_timeout_seconds per resource — a manifest bundling
        several workloads must not let one slow resource multiply the time
        the global single-flight deploy slot is held for; the configured
        value is a total budget. Any one resource not becoming ready
        (including running out of the shared deadline before its own turn)
        fails the whole result — a workload that never comes up is a real
        deploy failure, not a partial success.

        While a resource's rollout status is in progress, its pods are also
        polled (every ROLLOUT_POLL_INTERVAL_SECONDS) for a
        FAIL_FAST_WAITING_REASONS container state — see
        _run_rollout_status_poll()/_pod_fail_fast_reason(). Hitting one of
        those aborts the wait immediately, for this resource *and* every
        remaining one in this manifest, rather than waiting out the rest of
        the shared deadline: one workload already in CrashLoopBackOff/
        ImagePullBackOff/etc. means the deploy has already failed. An
        ordinary rollout failure/timeout (no fail-fast reason seen) keeps
        the existing behavior of still attempting every remaining resource.
        """
        targets = self._rollout_targets(manifest_yaml)
        if not targets:
            return DeployResult(success=True, log="")

        deadline = time.monotonic() + wait_timeout_seconds
        log_parts = []
        failed_labels = []

        for kind, name, namespace, match_labels in targets:
            resource_ref = f"{kind.lower()}/{name}"
            label = f"{resource_ref} -n {namespace}" if namespace else resource_ref

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log_parts.append(f"Skipped rollout wait for {label}: timeout budget already exhausted.\n")
                failed_labels.append(label)
                continue

            log_text, ordinary_failure, fail_fast_detail = self._run_rollout_status_poll(
                resource_ref, namespace, match_labels, remaining, label
            )
            log_parts.append(log_text)

            if fail_fast_detail:
                failed_labels.append(f"{label} ({fail_fast_detail})")
                break
            if ordinary_failure:
                failed_labels.append(label)

        combined_log = "".join(log_parts)
        if failed_labels:
            return DeployResult(
                success=False,
                log=combined_log,
                error=f"Rollout did not become ready within {wait_timeout_seconds}s: {', '.join(failed_labels)}",
            )
        return DeployResult(success=True, log=combined_log)

    def _run_rollout_status_poll(self, resource_ref, namespace, match_labels, remaining_seconds, label):
        """Runs `kubectl rollout status <resource_ref> [-n namespace]
        --timeout=<remaining_seconds>s` as its own Popen (not the blocking
        `_run_kubectl`), polling every ROLLOUT_POLL_INTERVAL_SECONDS for
        either natural completion or a FAIL_FAST_WAITING_REASONS pod state
        (when `match_labels` is non-empty), and enforcing its own
        `hard_deadline` kill since Popen has no built-in timeout= like
        subprocess.run does.

        Returns (log_text, ordinary_failure: bool, fail_fast_detail: str | None).
        `fail_fast_detail` set means a bad pod state was found; otherwise
        `ordinary_failure` reflects a nonzero rollout-status exit or a
        hard-deadline kill (both "waited, never became ready" cases).
        """
        args = ["rollout", "status", resource_ref, f"--timeout={math.ceil(remaining_seconds)}s"]
        if namespace:
            args += ["-n", namespace]

        # Kept open for the whole poll loop (unlike _run_kubectl, which
        # closes its tempdir right after a blocking call returns) — closing
        # it early would delete the kubeconfig out from under a still-running
        # kubectl process.
        with tempfile.TemporaryDirectory() as config_dir:
            config_path = os.path.join(config_dir, "kubeconfig.yaml")
            with open(config_path, "w") as config_file:
                config_file.write(self.kubeconfig)
            env = {**os.environ, "KUBECONFIG": config_path}

            try:
                process = subprocess.Popen(
                    [KUBECTL_PATH, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
                )
            except OSError as exc:
                return f"{label}: {exc}\n", True, None

            hard_deadline = time.monotonic() + remaining_seconds + ROLLOUT_STATUS_TIMEOUT_BUFFER_SECONDS

            while True:
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    return stdout + stderr, process.returncode != 0, None

                if time.monotonic() >= hard_deadline:
                    self._terminate_process(process)
                    return (
                        f"{label}: rollout status did not exit within {math.ceil(remaining_seconds)}s (killed)\n",
                        True,
                        None,
                    )

                if match_labels:
                    reason, pod_name = self._pod_fail_fast_reason(namespace, match_labels)
                    if reason:
                        self._terminate_process(process)
                        return f"{label}: pod {pod_name} is {reason}\n", False, f"pod {pod_name}: {reason}"

                time.sleep(ROLLOUT_POLL_INTERVAL_SECONDS)

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

    def stream_pod_logs(self, namespace, pod_name, container=None):
        """Yields decoded stdout lines from `kubectl logs -f` as they're
        written — used by the SSE log-tail endpoint
        (deployment_pods.routes.pod_logs_stream) in place of pod_logs()'s
        one-shot tail, so watching a pod's logs live no longer means
        re-spawning kubectl (and a fresh kubeconfig auth handshake) every few
        seconds. Unlike `_run_kubectl`, this uses `Popen` directly since the
        process must stay alive for the generator's lifetime rather than
        being waited on immediately; the `finally` block tears it down
        whenever the generator is closed (client disconnect, or the SSE
        route's own request ending), same lifetime pattern as the
        `tempfile.TemporaryDirectory` it runs inside.
        """
        args = ["logs", pod_name, "-n", namespace, "-f", f"--tail={POD_LOG_TAIL_LINES}"]
        if container:
            args += ["-c", container]

        with tempfile.TemporaryDirectory() as config_dir:
            config_path = os.path.join(config_dir, "kubeconfig.yaml")
            with open(config_path, "w") as config_file:
                config_file.write(self.kubeconfig)

            env = {**os.environ, "KUBECONFIG": config_path}
            process = subprocess.Popen(
                [KUBECTL_PATH, *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                bufsize=1,
            )
            try:
                for line in process.stdout:
                    yield line.rstrip("\n")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

            if process.returncode not in (0, None):
                stderr = (process.stderr.read() or "").strip()
                raise RuntimeError(stderr or f"kubectl logs -f exited with code {process.returncode}")

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

    def create_namespace(self, name, labels=None):
        """Create (or update, via kubectl apply's upsert semantics) a
        Namespace. Reused as-is for the "edit" action too — a Namespace's
        only meaningfully-editable surface is its labels.
        """
        manifest = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name}}
        if labels:
            manifest["metadata"]["labels"] = labels
        # JSON is valid YAML — kubectl apply -f - accepts it directly, so
        # there's no need to hand-build YAML text (risky for arbitrary
        # secret values below) or add a PyYAML dependency (same rationale as
        # resolver.py's regex-only approach — this module never parses or
        # generates real YAML anywhere).
        return self.apply(json.dumps(manifest))

    def delete_namespace(self, name):
        try:
            process = self._run_kubectl(["delete", "namespace", name, "--ignore-not-found=true"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = process.stdout + process.stderr
        if process.returncode != 0:
            return DeployResult(
                success=False, log=log, error=f"kubectl delete namespace exited with code {process.returncode}"
            )
        return DeployResult(success=True, log=log)

    def list_secrets(self, namespace=None):
        """[{name, namespace, type, keys, created_at}, ...] — `keys` is the
        sorted list of data key *names* only, values are never decoded or
        returned (same "write-only" boundary as DeploymentServer.kubeconfig/
        RegistryTarget.token elsewhere in this app).
        """
        args = ["get", "secrets", "-o", "json"]
        args += ["-n", namespace] if namespace else ["--all-namespaces"]

        try:
            process = self._run_kubectl(args, timeout=POD_LIST_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not list secrets: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or "kubectl get secrets failed.")

        secrets = []
        for item in json.loads(process.stdout).get("items", []):
            metadata = item.get("metadata", {})
            secrets.append(
                {
                    "name": metadata.get("name"),
                    "namespace": metadata.get("namespace"),
                    "type": item.get("type"),
                    "keys": sorted((item.get("data") or {}).keys()),
                    "created_at": metadata.get("creationTimestamp"),
                }
            )
        return secrets

    def get_secret(self, namespace, name):
        """{"type": ..., "keys": [...]} for edit-form pre-fill — key names
        only, values are never decoded or returned.
        """
        try:
            process = self._run_kubectl(
                ["get", "secret", name, "-n", namespace, "-o", "json"], timeout=POD_LIST_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not fetch secret: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or "kubectl get secret failed.")

        item = json.loads(process.stdout)
        return {"type": item.get("type"), "keys": sorted((item.get("data") or {}).keys())}

    def create_secret(self, namespace, name, data):
        """`data`: {key: plaintext_value} — sent as `stringData`, which the
        API server base64-encodes into `data` itself; this process never
        base64-encodes or otherwise touches the values.
        """
        manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {"name": name, "namespace": namespace},
            "stringData": data,
        }
        return self.apply(json.dumps(manifest))

    def update_secret(self, namespace, name, data_updates, removed_keys=None):
        """Merge-style update for an Opaque secret: keys in `data_updates`
        are set/overwritten (plaintext, via `stringData`), keys in
        `removed_keys` are dropped, and every other existing key is carried
        forward untouched — copied through as its still-base64-encoded value
        from the live object, never decoded. Relies on `kubectl apply`'s
        three-way merge (via its last-applied-configuration annotation) to
        actually remove a key that's absent from this new payload; a secret
        that was never previously `apply`'d by this app has no such
        annotation yet, so a stale key might not be pruned on its very first
        edit through here — standard `kubectl apply` behavior, not
        special-cased.
        """
        try:
            process = self._run_kubectl(
                ["get", "secret", name, "-n", namespace, "-o", "json"], timeout=POD_LIST_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not fetch secret: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or "kubectl get secret failed.")

        current = json.loads(process.stdout)
        existing_data = current.get("data") or {}
        removed = set(removed_keys or [])
        kept_data = {
            key: value for key, value in existing_data.items() if key not in removed and key not in data_updates
        }

        manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": current.get("type") or "Opaque",
            "metadata": {"name": name, "namespace": namespace},
            "data": kept_data,
            "stringData": data_updates,
        }
        return self.apply(json.dumps(manifest))

    def create_image_pull_secret(self, namespace, name, registry_server, username, password, email=None):
        """A `kubernetes.io/dockerconfigjson` secret for `imagePullSecrets` —
        reused as-is for edit too (always resubmitted whole, no per-field
        merge — unlike an Opaque secret's independent keys, this is really
        one blob).
        """
        auth_entry = {
            "username": username,
            "password": password,
            "auth": base64.b64encode(f"{username}:{password}".encode()).decode(),
        }
        if email:
            auth_entry["email"] = email

        dockerconfigjson = json.dumps({"auths": {registry_server: auth_entry}})
        manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "kubernetes.io/dockerconfigjson",
            "metadata": {"name": name, "namespace": namespace},
            "stringData": {".dockerconfigjson": dockerconfigjson},
        }
        return self.apply(json.dumps(manifest))

    def delete_secret(self, namespace, name):
        try:
            process = self._run_kubectl(["delete", "secret", name, "-n", namespace, "--ignore-not-found=true"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = process.stdout + process.stderr
        if process.returncode != 0:
            return DeployResult(
                success=False, log=log, error=f"kubectl delete secret exited with code {process.returncode}"
            )
        return DeployResult(success=True, log=log)

    def list_configmaps(self, namespace=None):
        """[{name, namespace, data, created_at}, ...] — unlike list_secrets,
        `data` here is the real (plaintext) key/value map, not just key
        names: ConfigMap data isn't sensitive, so there's no "write-only"
        boundary to maintain and no reason to force a second kubectl call
        later just to pre-fill an edit form with real values.
        """
        args = ["get", "configmaps", "-o", "json"]
        args += ["-n", namespace] if namespace else ["--all-namespaces"]

        try:
            process = self._run_kubectl(args, timeout=POD_LIST_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not list configmaps: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or "kubectl get configmaps failed.")

        configmaps = []
        for item in json.loads(process.stdout).get("items", []):
            metadata = item.get("metadata", {})
            configmaps.append(
                {
                    "name": metadata.get("name"),
                    "namespace": metadata.get("namespace"),
                    "data": item.get("data") or {},
                    "created_at": metadata.get("creationTimestamp"),
                }
            )
        return configmaps

    def create_configmap(self, namespace, name, data):
        """`data`: {key: plaintext_value}. Reused as-is for edit too (apply
        is upsert, and — unlike an Opaque Secret's blank-to-keep dance —
        there's nothing to hide here, so the edit form just resubmits the
        full current key/value set with the user's changes applied).
        """
        manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": namespace},
            "data": data,
        }
        return self.apply(json.dumps(manifest))

    def delete_configmap(self, namespace, name):
        try:
            process = self._run_kubectl(["delete", "configmap", name, "-n", namespace, "--ignore-not-found=true"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = process.stdout + process.stderr
        if process.returncode != 0:
            return DeployResult(
                success=False, log=log, error=f"kubectl delete configmap exited with code {process.returncode}"
            )
        return DeployResult(success=True, log=log)

    def list_ingresses(self, namespace=None):
        """[{name, namespace, hosts, rule_count, tls_secret_names,
        ingress_class_name, spec, created_at}, ...] — like list_configmaps,
        `spec` here is the real, full rule/backend/TLS structure (Ingress
        data isn't sensitive), used both for the summary table and to
        pre-fill the edit form (Form or raw-YAML mode) in one call, no
        separate per-row kubectl round-trip needed.
        """
        args = ["get", "ingress", "-o", "json"]
        args += ["-n", namespace] if namespace else ["--all-namespaces"]

        try:
            process = self._run_kubectl(args, timeout=POD_LIST_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not list ingresses: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or "kubectl get ingress failed.")

        ingresses = []
        for item in json.loads(process.stdout).get("items", []):
            metadata = item.get("metadata", {})
            spec = item.get("spec", {}) or {}
            rules = spec.get("rules", []) or []
            ingresses.append(
                {
                    "name": metadata.get("name"),
                    "namespace": metadata.get("namespace"),
                    "created_at": metadata.get("creationTimestamp"),
                    "hosts": [rule.get("host") or "*" for rule in rules],
                    "rule_count": len(rules),
                    "tls_secret_names": [
                        tls.get("secretName") for tls in (spec.get("tls") or []) if tls.get("secretName")
                    ],
                    "ingress_class_name": spec.get("ingressClassName"),
                    "spec": spec,
                }
            )
        return ingresses

    def delete_ingress(self, namespace, name):
        try:
            process = self._run_kubectl(["delete", "ingress", name, "-n", namespace, "--ignore-not-found=true"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = process.stdout + process.stderr
        if process.returncode != 0:
            return DeployResult(
                success=False, log=log, error=f"kubectl delete ingress exited with code {process.returncode}"
            )
        return DeployResult(success=True, log=log)

    def list_network_policies(self, namespace=None):
        """[{name, namespace, pod_selector, policy_types, ingress_rule_count,
        egress_rule_count, spec, annotations, created_at}, ...] — same
        one-call-powers-list-and-edit-prefill shape as list_ingresses.
        """
        args = ["get", "networkpolicies", "-o", "json"]
        args += ["-n", namespace] if namespace else ["--all-namespaces"]

        try:
            process = self._run_kubectl(args, timeout=POD_LIST_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not list network policies: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or "kubectl get networkpolicies failed.")

        policies = []
        for item in json.loads(process.stdout).get("items", []):
            metadata = item.get("metadata", {})
            spec = item.get("spec", {}) or {}
            policies.append(
                {
                    "name": metadata.get("name"),
                    "namespace": metadata.get("namespace"),
                    "created_at": metadata.get("creationTimestamp"),
                    "pod_selector": (spec.get("podSelector") or {}).get("matchLabels") or {},
                    "policy_types": spec.get("policyTypes") or [],
                    "ingress_rule_count": len(spec.get("ingress") or []),
                    "egress_rule_count": len(spec.get("egress") or []),
                    "spec": spec,
                    "annotations": metadata.get("annotations") or {},
                }
            )
        return policies

    def delete_network_policy(self, namespace, name):
        try:
            process = self._run_kubectl(
                ["delete", "networkpolicy", name, "-n", namespace, "--ignore-not-found=true"]
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = process.stdout + process.stderr
        if process.returncode != 0:
            return DeployResult(
                success=False, log=log, error=f"kubectl delete networkpolicy exited with code {process.returncode}"
            )
        return DeployResult(success=True, log=log)

    def restart_deployment(self, namespace, name):
        """A true `kubectl rollout restart` against one specific, already-
        named Deployment object — the zero-downtime rolling recycle that
        `restart()` above deliberately does NOT do anymore (see that
        method's docstring). Only meaningful for a resource kind rollout
        restart actually understands, which is exactly why this takes a
        concrete Deployment name/namespace instead of arbitrary manifest
        YAML — no kind-guessing involved.
        """
        try:
            process = self._run_kubectl(["rollout", "restart", f"deployment/{name}", "-n", namespace])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(success=False, log="", error=str(exc))

        log = process.stdout + process.stderr
        if process.returncode != 0:
            return DeployResult(
                success=False, log=log, error=f"kubectl rollout restart exited with code {process.returncode}"
            )
        return DeployResult(success=True, log=log)

    def find_deployments_using_secret(self, namespace, secret_name):
        """Names of Deployments in `namespace` whose pod template references
        Secret `secret_name` — via a container/initContainer's `env[].
        valueFrom.secretKeyRef`, `envFrom[].secretRef`, or a `volumes[].
        secret.secretName`. An already-running Pod never picks up an
        updated Secret's value on its own (env vars and imagePullSecrets are
        injected once, at container start), so the caller uses this to know
        which Deployments actually need a rollout restart after a Secret
        edit — restarting every Deployment in the namespace regardless of
        whether it even references the secret would be needless churn.
        """
        deployments = self.list_resources("deployment", namespace=namespace, all_namespaces=False)
        names = []
        for item in deployments:
            pod_spec = (item.get("spec") or {}).get("template", {}).get("spec", {}) or {}
            if self._pod_spec_references_secret(pod_spec, secret_name):
                name = item.get("metadata", {}).get("name")
                if name:
                    names.append(name)
        return sorted(names)

    @staticmethod
    def _pod_spec_references_secret(pod_spec, secret_name):
        containers = (pod_spec.get("containers") or []) + (pod_spec.get("initContainers") or [])
        for container in containers:
            for env_var in container.get("env") or []:
                secret_ref = (env_var.get("valueFrom") or {}).get("secretKeyRef") or {}
                if secret_ref.get("name") == secret_name:
                    return True
            for env_from in container.get("envFrom") or []:
                secret_ref = env_from.get("secretRef") or {}
                if secret_ref.get("name") == secret_name:
                    return True
        for volume in pod_spec.get("volumes") or []:
            secret_volume = volume.get("secret") or {}
            if secret_volume.get("secretName") == secret_name:
                return True
        return False
