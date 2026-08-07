import json
import os
import subprocess
import tempfile
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

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = docker.from_env()
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

    def _inspect_image(self, image_ref):
        try:
            image = self.client.images.get(image_ref)
            return image.id, image.attrs.get("Size")
        except docker.errors.ImageNotFound:
            return None, None


class KanikoBuildEngine(BuildEngine):
    """Builds (and pushes) via the `kaniko-executor` binary — no Docker
    daemon, no docker.sock, no privileged/overlay filesystem requirements.
    Kaniko builds and pushes in one step, so unlike DockerBuildEngine this
    needs registry credentials up front rather than after a successful build;
    `BuildResult.pushed` comes back True so the worker knows not to push again.
    """

    EXECUTOR_PATH = os.environ.get("KANIKO_EXECUTOR_PATH", "/kaniko/executor")

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

        tag_name = tags[0].rpartition(":")[2]
        destination = f"{push_repository}:{tag_name}"

        log_lines = []

        def _emit(line):
            log_lines.append(line)
            if on_log_line:
                on_log_line(line)

        with tempfile.TemporaryDirectory() as config_dir:
            self._write_docker_config(config_dir, registry_provider)

            argv = [
                self.EXECUTOR_PATH,
                f"--context=dir://{context_dir}",
                f"--dockerfile={dockerfile_path}",
                f"--destination={destination}",
            ]
            for key, value in (build_args or {}).items():
                argv.append(f"--build-arg={key}={value}")

            env = {**os.environ, "DOCKER_CONFIG": config_dir}

            try:
                process = subprocess.Popen(
                    argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
                )
            except OSError as exc:
                error = f"Failed to start kaniko-executor: {exc}"
                _emit(f"ERROR: {error}\n")
                return BuildResult(success=False, image_id=None, tags=tags, log="".join(log_lines), error=error)

            for line in process.stdout:
                _emit(line)
            process.wait()

        if process.returncode != 0:
            error = f"kaniko-executor exited with code {process.returncode}"
            return BuildResult(success=False, image_id=None, tags=tags, log="".join(log_lines), error=error)

        return BuildResult(success=True, image_id=None, tags=tags, log="".join(log_lines), pushed=True)

    def _write_docker_config(self, config_dir, registry_provider):
        auth = b64encode(
            f"{registry_provider.username}:{registry_provider.password}".encode()
        ).decode()
        config = {"auths": {registry_provider.registry_host: {"auth": auth}}}
        with open(os.path.join(config_dir, "config.json"), "w") as config_file:
            json.dump(config, config_file)
