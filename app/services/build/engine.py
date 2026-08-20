import json
import os
import subprocess
import time
import uuid
from abc import ABC, abstractmethod
from base64 import b64encode
from dataclasses import dataclass

import docker


@dataclass
class BuildResult:
    success: bool
    image_id: str | None
    tags: list
    log: str
    error: str | None = None
    image_size: int | None = None
    # True if this engine already pushed the image as part of the build itself
    # (Kaniko doesn't separate the two steps) — tells the worker to skip its
    # own separate RegistryProvider.push_image() call for this result.
    pushed: bool = False


class BuildEngine(ABC):
    """Provider-agnostic interface for the actual image-build step.

    Implemented first with docker-py; Kaniko is a second implementation for
    daemonless/rootless builds, no calling-code changes needed beyond what
    the worker already does to support pushing engines.
    """

    @abstractmethod
    def build_image(
        self,
        context_dir,
        dockerfile_path,
        tags,
        build_args=None,
        on_log_line=None,
        registry_provider=None,
        push_repository=None,
    ):
        """Build an image from `context_dir` using `dockerfile_path`, applying `tags`.

        Calls `on_log_line(line)` for each line of output as it streams in, if
        given, so a caller (the worker) can append to ImageBuild.build_log
        incrementally rather than waiting for the whole build to finish.

        `registry_provider`/`push_repository` are only meaningful to engines
        that push as part of the build itself (see `BuildResult.pushed`) —
        `DockerBuildEngine` ignores them, since the worker pushes separately
        afterward via `RegistryProvider.push_image()`.
        """

    def cleanup_local_image(self, tag):
        """Remove a build's locally-loaded image after it's been pushed, so
        this app's own container/daemon storage doesn't grow by a full image
        on every single build forever — confirmed in practice reaching
        several GB after normal day-to-day use. Default no-op: an engine
        that pushes as part of build_image itself (Kaniko — see
        BuildResult.pushed) never loads anything into local daemon storage
        in the first place, so there's nothing here to remove.
        """


class DockerBuildEngine(BuildEngine):
    """Shells out to `docker buildx build` (BuildKit) rather than docker-py's
    low-level build API. docker-py's `client.api.build()` only ever talks to
    Docker's legacy (non-BuildKit) builder — no env var or client option
    switches it to BuildKit, since BuildKit support was simply never
    implemented in that call. That made any BuildKit-only Dockerfile syntax
    (`RUN --mount=...`, heredocs, ...) fail outright. `--load` puts the built
    image into the local daemon's image store, same as the old approach, so
    `client` (docker-py, still used below to inspect the built image, and by
    the worker afterward for its separate registry push step) keeps working
    unchanged.
    """

    # docker-py's own default (60s) is a per-read socket timeout, not a
    # total-request one — it applies to the streaming push/pull/remove calls
    # below just as much as to quick ones. 60s is comfortably enough over a
    # native dockerd, but confirmed in practice too tight pushing a few
    # hundred MB through Podman's Docker-API-compatible socket (slower than
    # a native push), which raised a bare ReadTimeout mid-push with no
    # retry. Generous rather than tuned to a specific image size, since
    # there's no way to know that up front.
    CLIENT_TIMEOUT_SECONDS = 600

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = docker.from_env(timeout=self.CLIENT_TIMEOUT_SECONDS)
        return self._client

    def build_image(
        self,
        context_dir,
        dockerfile_path,
        tags,
        build_args=None,
        on_log_line=None,
        registry_provider=None,
        push_repository=None,
    ):
        # registry_provider/push_repository: unused here — this engine only
        # builds; the worker pushes separately afterward via engine.client.
        if not tags:
            raise ValueError("build_image requires at least one tag.")

        log_lines = []

        def _emit(line):
            log_lines.append(line)
            if on_log_line:
                on_log_line(line)

        # --file resolves relative to the process's cwd, not the build
        # context (unlike docker-py's old client.api.build(), which always
        # resolved `dockerfile` relative to `path`) — join it against
        # context_dir explicitly so a relative dockerfile_path still finds
        # the right file regardless of where this subprocess actually runs.
        full_dockerfile_path = os.path.join(context_dir, dockerfile_path)
        argv = ["docker", "buildx", "build", "--load", "--progress=plain", f"--file={full_dockerfile_path}"]
        argv += [f"--tag={tag}" for tag in tags]
        for key, value in (build_args or {}).items():
            argv.append(f"--build-arg={key}={value}")
        argv.append(context_dir)

        try:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError as exc:
            error = f"Failed to start docker buildx: {exc}"
            _emit(f"ERROR: {error}\n")
            return BuildResult(success=False, image_id=None, tags=tags, log="".join(log_lines), error=error)

        for line in process.stdout:
            _emit(line)
        process.wait()

        if process.returncode != 0:
            error = f"docker buildx build exited with code {process.returncode}"
            return BuildResult(success=False, image_id=None, tags=tags, log="".join(log_lines), error=error)

        image_id, image_size = self._inspect_image(tags[0])
        return BuildResult(
            success=True, image_id=image_id, tags=tags, log="".join(log_lines), image_size=image_size
        )

    def cleanup_local_image(self, tag):
        try:
            self.client.images.remove(image=tag, force=True)
        except docker.errors.ImageNotFound:
            # Already gone (e.g. a concurrent prune) — nothing left to do.
            pass

    def _inspect_image(self, image_ref):
        try:
            image = self.client.images.get(image_ref)
            return image.id, image.attrs.get("Size")
        except docker.errors.ImageNotFound:
            return None, None


KUBECTL_PATH = os.environ.get("KUBECTL_PATH", "kubectl")
KANIKO_EXECUTOR_IMAGE = os.environ.get("KANIKO_EXECUTOR_IMAGE", "gcr.io/kaniko-project/executor:v1.23.2")
# Must match the mountPath the app's own Deployment already uses for its
# REPO_CLONE_ROOT-backed volume (see k8s/deployment.yaml) — context_dir
# (built from REPO_CLONE_ROOT) is only valid inside the build Job's
# container if it's mounted at this exact same path there too.
KANIKO_WORKSPACE_MOUNT_PATH = os.environ.get("KANIKO_WORKSPACE_MOUNT_PATH", "/app/data")
# How long `kubectl logs -f` itself will wait for the Job's pod to actually
# reach Running before giving up — its own default (20s) is too tight for a
# cold pull of the kaniko-executor image (or a slow scheduler), and without
# this flag it doesn't retry at all: it fails immediately with "container
# ... is waiting to start: ContainerCreating" the moment it's invoked before
# the container has started, rather than waiting for it.
KANIKO_POD_RUNNING_TIMEOUT_SECONDS = 300
# Fallback safety net for _wait_for_job_completion, only actually exercised
# when log streaming above returns early without a real answer (e.g. it hit
# its own pod-running-timeout above). Deliberately generous — an actual
# image build (pull base layers, run every step, push) can take many
# minutes, and DockerBuildEngine imposes no build-duration timeout of its
# own either; this is just a last-resort "something is very wrong" cutoff.
KANIKO_JOB_STATUS_TIMEOUT_SECONDS = 3600
KANIKO_JOB_STATUS_POLL_INTERVAL_SECONDS = 2
SERVICE_ACCOUNT_NAMESPACE_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


class KanikoBuildEngine(BuildEngine):
    """Builds (and pushes) by launching `kaniko-executor` as its own
    Kubernetes Job, in the same cluster/namespace this app's own pod runs
    in — not as a subprocess of this app. An earlier version of this class
    ran kaniko-executor as a bare subprocess, which shared this app's own
    root filesystem with it (kaniko has no daemon/chroot of its own and
    extracts each FROM image's layers directly onto whatever filesystem the
    executor process is running in) and corrupted a live app container
    mid-build. Running it as its own pod instead gives it real filesystem
    isolation, with no Docker/CRI daemon or socket needed at all — this is
    the build engine for clusters (e.g. CRI-O) that don't expose one.

    Needs the app pod's own ServiceAccount to be able to create/get/delete
    Jobs, Pods, pods/log, and Secrets in its own namespace (see
    k8s/deployment.yaml). Shares the exact same hostPath-backed volume the
    app's own Deployment already mounts (KANIKO_WORKSPACE_HOST_PATH must be
    set to that same host path) at KANIKO_WORKSPACE_MOUNT_PATH, so the
    build Job's pod sees the identical repo clone (including any
    managed-Dockerfile file already written into it — see
    worker._resolve_dockerfile_path) that `context_dir` already points at,
    with no separate context-transfer step. Pinned to the exact node the
    app pod is currently scheduled on via the Downward-API-sourced
    NODE_NAME env var, since a hostPath is node-local.

    `BuildResult.pushed` comes back True (like the old subprocess version)
    since kaniko builds and pushes in one step — the worker knows not to
    push again.
    """

    def build_image(
        self,
        context_dir,
        dockerfile_path,
        tags,
        build_args=None,
        on_log_line=None,
        registry_provider=None,
        push_repository=None,
    ):
        if not tags:
            raise ValueError("build_image requires at least one tag.")
        if registry_provider is None or push_repository is None:
            raise ValueError(
                "KanikoBuildEngine pushes as part of the build and needs "
                "registry_provider and push_repository to do so."
            )

        log_lines = []

        def _emit(line):
            log_lines.append(line)
            if on_log_line:
                on_log_line(line)

        def _fail(error):
            _emit(f"ERROR: {error}\n")
            return BuildResult(success=False, image_id=None, tags=tags, log="".join(log_lines), error=error)

        host_path = os.environ.get("KANIKO_WORKSPACE_HOST_PATH")
        if not host_path:
            return _fail("KANIKO_WORKSPACE_HOST_PATH is not set — required to mount the build context into the Job.")

        try:
            namespace = self._namespace()
        except RuntimeError as exc:
            return _fail(str(exc))

        tag_name = tags[0].rpartition(":")[2]
        destination = f"{push_repository}:{tag_name}"
        job_name = f"kaniko-build-{uuid.uuid4().hex[:16]}"
        secret_name = f"{job_name}-regcred"

        try:
            self._apply_secret(secret_name, namespace, registry_provider)
            self._apply_job(
                job_name, namespace, host_path, context_dir, dockerfile_path, destination,
                build_args or {}, secret_name,
            )
        except RuntimeError as exc:
            self._cleanup(job_name, secret_name, namespace, log_lines)
            return _fail(str(exc))

        try:
            self._stream_job_logs(job_name, namespace, _emit)
            success = self._wait_for_job_completion(job_name, namespace)
        except RuntimeError as exc:
            self._cleanup(job_name, secret_name, namespace, log_lines)
            return _fail(str(exc))

        self._cleanup(job_name, secret_name, namespace, log_lines)

        if not success:
            return _fail(f"Kaniko build Job {job_name} did not complete successfully.")

        return BuildResult(success=True, image_id=None, tags=tags, log="".join(log_lines), pushed=True)

    def _namespace(self):
        if os.path.exists(SERVICE_ACCOUNT_NAMESPACE_FILE):
            with open(SERVICE_ACCOUNT_NAMESPACE_FILE) as namespace_file:
                return namespace_file.read().strip()
        namespace = os.environ.get("KUBERNETES_NAMESPACE")
        if not namespace:
            raise RuntimeError(
                "Could not determine the current Kubernetes namespace (no in-cluster "
                "service account token found, and KUBERNETES_NAMESPACE isn't set)."
            )
        return namespace

    def _apply_secret(self, secret_name, namespace, registry_provider):
        auth = b64encode(f"{registry_provider.username}:{registry_provider.password}".encode()).decode()
        docker_config = {"auths": {registry_provider.docker_config_auth_key: {"auth": auth}}}
        self._apply(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "type": "Opaque",
                "metadata": {"name": secret_name, "namespace": namespace},
                "stringData": {"config.json": json.dumps(docker_config)},
            }
        )

    def _apply_job(
        self, job_name, namespace, host_path, context_dir, dockerfile_path, destination, build_args, secret_name
    ):
        args = [
            f"--context=dir://{context_dir}",
            f"--dockerfile={dockerfile_path}",
            f"--destination={destination}",
        ]
        for key, value in build_args.items():
            args.append(f"--build-arg={key}={value}")

        pod_spec = {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "kaniko",
                    "image": KANIKO_EXECUTOR_IMAGE,
                    "args": args,
                    "volumeMounts": [
                        {"name": "workspace", "mountPath": KANIKO_WORKSPACE_MOUNT_PATH},
                        {"name": "docker-config", "mountPath": "/kaniko/.docker"},
                    ],
                }
            ],
            "volumes": [
                {"name": "workspace", "hostPath": {"path": host_path, "type": "Directory"}},
                {"name": "docker-config", "secret": {"secretName": secret_name}},
            ],
        }
        # A hostPath volume is node-local — this Job's pod must land on the
        # exact same node the app pod is on right now, not wherever the
        # scheduler would otherwise pick.
        node_name = os.environ.get("NODE_NAME")
        if node_name:
            pod_spec["nodeName"] = node_name

        self._apply(
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {"name": job_name, "namespace": namespace},
                "spec": {
                    "backoffLimit": 0,
                    "ttlSecondsAfterFinished": 300,
                    "template": {"spec": pod_spec},
                },
            }
        )

    def _apply(self, manifest):
        try:
            # JSON is valid YAML — `kubectl apply -f -` accepts it directly,
            # same rationale as KubernetesProvider.create_namespace/create_secret
            # for not hand-building YAML text or adding a PyYAML dependency.
            process = subprocess.run(
                [KUBECTL_PATH, "apply", "-f", "-"], input=json.dumps(manifest), capture_output=True, text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"kubectl apply failed to start: {exc}") from None
        if process.returncode != 0:
            raise RuntimeError((process.stderr or "").strip() or "kubectl apply exited non-zero.")

    def _stream_job_logs(self, job_name, namespace, emit):
        argv = [
            KUBECTL_PATH, "logs", "-f", f"job/{job_name}", "-n", namespace,
            f"--pod-running-timeout={KANIKO_POD_RUNNING_TIMEOUT_SECONDS}s",
        ]
        try:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError as exc:
            raise RuntimeError(f"Failed to start kubectl logs: {exc}") from None
        for line in process.stdout:
            emit(line)
        process.wait()
        # kubectl logs' own exit code only reflects whether it could attach
        # and stream at all, not the built container's exit code — the
        # Job's actual outcome is checked separately, in
        # _wait_for_job_completion, right after this returns.

    def _wait_for_job_completion(self, job_name, namespace):
        deadline = time.monotonic() + KANIKO_JOB_STATUS_TIMEOUT_SECONDS
        while True:
            try:
                process = subprocess.run(
                    [KUBECTL_PATH, "get", "job", job_name, "-n", namespace, "-o", "json"],
                    capture_output=True, text=True, timeout=15,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(f"Could not check build Job status: {exc}") from None
            if process.returncode != 0:
                raise RuntimeError((process.stderr or "").strip() or "kubectl get job failed.")

            status = json.loads(process.stdout).get("status", {})
            if status.get("succeeded", 0) >= 1:
                return True
            if status.get("failed", 0) >= 1:
                return False
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Timed out waiting for build Job {job_name} to report a final status.")
            time.sleep(KANIKO_JOB_STATUS_POLL_INTERVAL_SECONDS)

    def _cleanup(self, job_name, secret_name, namespace, log_lines):
        """Best-effort: a failed cleanup is a stray Job/Secret left behind
        in the cluster, not a reason to also fail an otherwise-already-
        decided build result.
        """
        for kind, name in (("job", job_name), ("secret", secret_name)):
            try:
                subprocess.run(
                    [KUBECTL_PATH, "delete", kind, name, "-n", namespace, "--ignore-not-found=true"],
                    capture_output=True, text=True, timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                log_lines.append(f"\nWARNING: failed to clean up {kind}/{name}: {exc}\n")
