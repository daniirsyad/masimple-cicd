import base64
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

    def restart(self, manifest_yaml):
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
        """
        delete_result = self.delete(manifest_yaml)
        if not delete_result.success:
            return delete_result

        apply_result = self.apply(manifest_yaml)
        combined_log = delete_result.log + "\n" + apply_result.log
        return DeployResult(success=apply_result.success, log=combined_log, error=apply_result.error)

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
