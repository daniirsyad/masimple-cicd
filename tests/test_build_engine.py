import base64
import json

import docker
import pytest

from app.services.build.engine import BuildResult, DockerBuildEngine, KanikoBuildEngine


class FakeImage:
    def __init__(self, id_, size):
        self.id = id_
        self.attrs = {"Size": size}


class FakeImagesCollection:
    def __init__(self, image_id="sha256:abc123", size=12345, raise_not_found=False):
        self._image_id = image_id
        self._size = size
        self._raise_not_found = raise_not_found
        self.get_calls = []
        self.remove_calls = []

    def get(self, ref):
        self.get_calls.append(ref)
        if self._raise_not_found:
            raise docker.errors.ImageNotFound("not found")
        return FakeImage(self._image_id, self._size)

    def remove(self, image, force=False):
        self.remove_calls.append((image, force))
        if self._raise_not_found:
            raise docker.errors.ImageNotFound("not found")


class FakeDockerClient:
    def __init__(self, image_id="sha256:abc123", image_size=12345, raise_not_found=False):
        self.images = FakeImagesCollection(image_id, image_size, raise_not_found)


class FakeBuildProcess:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self._final_returncode = returncode
        self.returncode = None

    def wait(self):
        self.returncode = self._final_returncode
        return self.returncode


def test_successful_build_streams_log_lines_and_returns_result(monkeypatch):
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return FakeBuildProcess(["Step 1/2 : FROM python:3.12-slim\n", "Step 2/2 : RUN echo hi\n"])

    monkeypatch.setattr("app.services.build.engine.subprocess.Popen", fake_popen)

    client = FakeDockerClient(image_id="sha256:abc123", image_size=999)
    engine = DockerBuildEngine(client=client)

    seen_lines = []
    result = engine.build_image(
        context_dir="/tmp/whatever",
        dockerfile_path="Dockerfile",
        tags=["myrepo/myimage:1.0.0"],
        on_log_line=seen_lines.append,
    )

    assert isinstance(result, BuildResult)
    assert result.success is True
    assert result.image_id == "sha256:abc123"
    assert result.tags == ["myrepo/myimage:1.0.0"]
    assert "Step 1/2" in result.log
    assert "Step 2/2" in result.log
    assert seen_lines == [
        "Step 1/2 : FROM python:3.12-slim\n",
        "Step 2/2 : RUN echo hi\n",
    ]
    assert result.image_size == 999

    assert captured["argv"][:3] == ["docker", "buildx", "build"]
    assert "--load" in captured["argv"]
    assert "--file=/tmp/whatever/Dockerfile" in captured["argv"]
    assert "--tag=myrepo/myimage:1.0.0" in captured["argv"]
    assert captured["argv"][-1] == "/tmp/whatever"
    assert client.images.get_calls == ["myrepo/myimage:1.0.0"]


def test_dockerfile_path_is_resolved_against_the_build_context(monkeypatch):
    """--file resolves relative to the subprocess's cwd, not the build
    context, so a plain dockerfile_path like "docker/Dockerfile.prod" must be
    joined against context_dir before being passed to buildx — otherwise it
    fails with "no such file or directory" whenever this process's cwd isn't
    also the build context.
    """
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return FakeBuildProcess([])

    monkeypatch.setattr("app.services.build.engine.subprocess.Popen", fake_popen)

    engine = DockerBuildEngine(client=FakeDockerClient())
    engine.build_image(
        context_dir="/repos/myapp",
        dockerfile_path="docker/Dockerfile.prod",
        tags=["x:1"],
    )

    assert "--file=/repos/myapp/docker/Dockerfile.prod" in captured["argv"]


def test_build_error_returns_failure_result_without_raising(monkeypatch):
    monkeypatch.setattr(
        "app.services.build.engine.subprocess.Popen",
        lambda argv, **kwargs: FakeBuildProcess(
            ["Step 1/1 : FROM does-not-exist\n", "ERROR: pull access denied for does-not-exist\n"],
            returncode=1,
        ),
    )

    engine = DockerBuildEngine(client=FakeDockerClient())
    result = engine.build_image("/ctx", "Dockerfile", ["x:1"])

    assert result.success is False
    assert "exited with code 1" in result.error
    assert "pull access denied" in result.log


def test_multiple_tags_are_all_passed_in_one_build(monkeypatch):
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return FakeBuildProcess([])

    monkeypatch.setattr("app.services.build.engine.subprocess.Popen", fake_popen)

    engine = DockerBuildEngine(client=FakeDockerClient())
    result = engine.build_image("/ctx", "Dockerfile", ["repo/img:1.0.0", "repo/img:latest"])

    assert result.success is True
    assert "--tag=repo/img:1.0.0" in captured["argv"]
    assert "--tag=repo/img:latest" in captured["argv"]


def test_build_args_are_forwarded_as_flags(monkeypatch):
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return FakeBuildProcess([])

    monkeypatch.setattr("app.services.build.engine.subprocess.Popen", fake_popen)

    engine = DockerBuildEngine(client=FakeDockerClient())
    engine.build_image("/ctx", "Dockerfile", ["x:1"], build_args={"VERSION": "1.2.3"})

    assert "--build-arg=VERSION=1.2.3" in captured["argv"]


def test_requires_at_least_one_tag():
    engine = DockerBuildEngine(client=FakeDockerClient())
    with pytest.raises(ValueError):
        engine.build_image("/ctx", "Dockerfile", [])


def test_image_not_found_when_inspecting_built_image_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        "app.services.build.engine.subprocess.Popen", lambda argv, **kwargs: FakeBuildProcess([])
    )

    engine = DockerBuildEngine(client=FakeDockerClient(raise_not_found=True))
    result = engine.build_image("/ctx", "Dockerfile", ["x:1"])

    assert result.success is True
    assert result.image_size is None
    assert result.image_id is None


def test_missing_docker_binary_returns_failure_without_raising(monkeypatch):
    def fake_popen(argv, **kwargs):
        raise OSError("No such file or directory")

    monkeypatch.setattr("app.services.build.engine.subprocess.Popen", fake_popen)

    engine = DockerBuildEngine(client=FakeDockerClient())
    result = engine.build_image("/ctx", "Dockerfile", ["x:1"])

    assert result.success is False
    assert "Failed to start docker buildx" in result.error


def test_lazy_client_uses_docker_from_env_when_not_injected(monkeypatch):
    sentinel = object()
    captured = {}

    def fake_from_env(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr("docker.from_env", fake_from_env)
    engine = DockerBuildEngine()
    assert engine.client is sentinel

    # docker-py's own default (60s) is a per-read timeout too short for
    # pushing a real image through Podman's Docker-API-compatible socket —
    # confirmed in practice raising a bare ReadTimeout mid-push. Must be
    # passed explicitly, not left at the client library's own default.
    assert captured["timeout"] == DockerBuildEngine.CLIENT_TIMEOUT_SECONDS


class TestCleanupLocalImage:
    def test_removes_the_local_image_by_tag(self):
        client = FakeDockerClient()
        engine = DockerBuildEngine(client=client)

        engine.cleanup_local_image("myapp:1.0.0")

        assert client.images.remove_calls == [("myapp:1.0.0", True)]

    def test_already_gone_image_does_not_raise(self):
        client = FakeDockerClient(raise_not_found=True)
        engine = DockerBuildEngine(client=client)

        engine.cleanup_local_image("myapp:1.0.0")  # must not raise

    def test_kaniko_engine_cleanup_is_a_no_op(self):
        """Kaniko pushes as part of build_image itself (BuildResult.pushed)
        and never loads anything into local daemon storage — nothing to
        clean up, and no docker client to even do it with.
        """
        engine = KanikoBuildEngine()
        engine.cleanup_local_image("myapp:1.0.0")  # must not raise


class _FakeRegistryProvider:
    def __init__(
        self,
        username="myuser",
        password="mypass",
        registry_host="registry-1.docker.io",
        docker_config_auth_key=None,
    ):
        self.username = username
        self.password = password
        self.registry_host = registry_host
        # Mirrors RegistryProvider.docker_config_auth_key's real default
        # (falls back to registry_host) — Kaniko's config.json is keyed by
        # this, not registry_host directly; see DockerHubProvider's override
        # for why those two differ for Docker Hub specifically.
        self.docker_config_auth_key = docker_config_auth_key or registry_host


class FakeKanikoLogProcess:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self._final_returncode = returncode
        self.returncode = None

    def wait(self):
        self.returncode = self._final_returncode
        return self.returncode


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_fake_run(job_status="succeeded"):
    """A kubectl-shaped fake for subprocess.run, dispatching on the
    subcommand (argv[1]: apply/get/delete) the way the real
    KanikoBuildEngine actually calls it. `calls` records every invocation
    for assertions; `job_status` controls what a "get job" call reports
    (succeeded/failed/pending, the last leaving status empty).
    """
    calls = []

    def fake_run(argv, input=None, capture_output=True, text=True, timeout=None):
        calls.append({"argv": argv, "input": input})
        if argv[1] == "apply":
            return FakeCompletedProcess(returncode=0)
        if argv[1] == "get":
            status = {}
            if job_status == "succeeded":
                status["succeeded"] = 1
            elif job_status == "failed":
                status["failed"] = 1
            return FakeCompletedProcess(returncode=0, stdout=json.dumps({"status": status}))
        if argv[1] == "delete":
            return FakeCompletedProcess(returncode=0)
        raise AssertionError(f"unexpected kubectl call: {argv}")

    return calls, fake_run


class TestKanikoBuildEngine:
    def test_requires_at_least_one_tag(self):
        engine = KanikoBuildEngine()
        with pytest.raises(ValueError):
            engine.build_image(
                "/ctx", "Dockerfile", [], registry_provider=_FakeRegistryProvider(), push_repository="repo/x"
            )

    def test_requires_registry_provider_and_push_repository(self):
        engine = KanikoBuildEngine()
        with pytest.raises(ValueError):
            engine.build_image("/ctx", "Dockerfile", ["x:1"])

    def test_missing_workspace_host_path_returns_failure_without_any_kubectl_call(self, monkeypatch):
        monkeypatch.delenv("KANIKO_WORKSPACE_HOST_PATH", raising=False)
        calls, fake_run = _make_fake_run()
        monkeypatch.setattr("app.services.build.engine.subprocess.run", fake_run)

        engine = KanikoBuildEngine()
        result = engine.build_image(
            "/ctx", "Dockerfile", ["x:1"], registry_provider=_FakeRegistryProvider(), push_repository="repo/x"
        )

        assert result.success is False
        assert "KANIKO_WORKSPACE_HOST_PATH" in result.error
        assert calls == []

    def test_undeterminable_namespace_returns_failure(self, monkeypatch):
        monkeypatch.setenv("KANIKO_WORKSPACE_HOST_PATH", "/mnt/data")
        monkeypatch.setattr(
            "app.services.build.engine.KanikoBuildEngine._namespace",
            lambda self: (_ for _ in ()).throw(RuntimeError("Could not determine the current Kubernetes namespace")),
        )
        calls, fake_run = _make_fake_run()
        monkeypatch.setattr("app.services.build.engine.subprocess.run", fake_run)

        engine = KanikoBuildEngine()
        result = engine.build_image(
            "/ctx", "Dockerfile", ["x:1"], registry_provider=_FakeRegistryProvider(), push_repository="repo/x"
        )

        assert result.success is False
        assert "namespace" in result.error
        assert calls == []

    def _prepare(self, monkeypatch, job_status="succeeded", log_lines=("Pushed image\n",), node_name=None):
        monkeypatch.setenv("KANIKO_WORKSPACE_HOST_PATH", "/mnt/data")
        if node_name is None:
            monkeypatch.delenv("NODE_NAME", raising=False)
        else:
            monkeypatch.setenv("NODE_NAME", node_name)
        monkeypatch.setattr("app.services.build.engine.KanikoBuildEngine._namespace", lambda self: "test-ns")

        calls, fake_run = _make_fake_run(job_status=job_status)
        monkeypatch.setattr("app.services.build.engine.subprocess.run", fake_run)

        log_calls = []

        def fake_popen(argv, stdout=None, stderr=None, text=None):
            log_calls.append(argv)
            return FakeKanikoLogProcess(list(log_lines), returncode=0)

        monkeypatch.setattr("app.services.build.engine.subprocess.Popen", fake_popen)
        return calls, log_calls

    def test_successful_build_returns_pushed_result_and_applies_docker_config_secret(self, monkeypatch):
        calls, log_calls = self._prepare(monkeypatch)

        engine = KanikoBuildEngine()
        result = engine.build_image(
            context_dir="/ctx",
            dockerfile_path="Dockerfile",
            tags=["myapp:1.0.0"],
            registry_provider=_FakeRegistryProvider(),
            push_repository="myuser/myapp",
        )

        assert result.success is True
        assert result.pushed is True

        applies = [json.loads(c["input"]) for c in calls if c["argv"][1] == "apply"]
        secret_manifest = next(m for m in applies if m["kind"] == "Secret")
        job_manifest = next(m for m in applies if m["kind"] == "Job")

        auth_entry = json.loads(secret_manifest["stringData"]["config.json"])["auths"]["registry-1.docker.io"]
        decoded = base64.b64decode(auth_entry["auth"]).decode()
        assert decoded == "myuser:mypass"

        container = job_manifest["spec"]["template"]["spec"]["containers"][0]
        assert "--destination=myuser/myapp:1.0.0" in container["args"]
        assert "--context=dir:///ctx" in container["args"]
        assert "--dockerfile=Dockerfile" in container["args"]
        assert job_manifest["spec"]["template"]["spec"]["volumes"][0]["hostPath"]["path"] == "/mnt/data"
        assert "nodeName" not in job_manifest["spec"]["template"]["spec"]

        assert log_calls == [["kubectl", "logs", "-f", f"job/{job_manifest['metadata']['name']}", "-n", "test-ns"]]

        deletes = {(c["argv"][2], c["argv"][3]) for c in calls if c["argv"][1] == "delete"}
        assert deletes == {("job", job_manifest["metadata"]["name"]), ("secret", secret_manifest["metadata"]["name"])}

    def test_writes_docker_config_keyed_by_docker_config_auth_key_not_registry_host(self, monkeypatch):
        """A real DockerHubProvider's docker_config_auth_key differs from its
        registry_host — this is the actual bug that left Kaniko unable to
        find credentials for an unqualified Docker Hub push (see
        test_registry_provider.py::TestDockerConfigAuthKey).
        """
        calls, _ = self._prepare(monkeypatch)
        provider = _FakeRegistryProvider(
            registry_host="registry-1.docker.io",
            docker_config_auth_key="https://index.docker.io/v1/",
        )

        engine = KanikoBuildEngine()
        engine.build_image(
            context_dir="/ctx",
            dockerfile_path="Dockerfile",
            tags=["myapp:1.0.0"],
            registry_provider=provider,
            push_repository="myuser/myapp",
        )

        secret_manifest = next(json.loads(c["input"]) for c in calls if c["argv"][1] == "apply" and "regcred" in json.loads(c["input"])["metadata"]["name"])
        auths = json.loads(secret_manifest["stringData"]["config.json"])["auths"]
        assert "https://index.docker.io/v1/" in auths
        assert "registry-1.docker.io" not in auths

    def test_build_args_are_forwarded_as_flags(self, monkeypatch):
        calls, _ = self._prepare(monkeypatch)

        engine = KanikoBuildEngine()
        engine.build_image(
            "/ctx",
            "Dockerfile",
            ["x:1"],
            build_args={"VERSION": "1.2.3"},
            registry_provider=_FakeRegistryProvider(),
            push_repository="repo/x",
        )

        job_manifest = next(json.loads(c["input"]) for c in calls if c["argv"][1] == "apply" and json.loads(c["input"])["kind"] == "Job")
        assert "--build-arg=VERSION=1.2.3" in job_manifest["spec"]["template"]["spec"]["containers"][0]["args"]

    def test_node_name_env_var_pins_the_job_to_the_current_node(self, monkeypatch):
        calls, _ = self._prepare(monkeypatch, node_name="worker-1")

        engine = KanikoBuildEngine()
        engine.build_image(
            "/ctx", "Dockerfile", ["x:1"], registry_provider=_FakeRegistryProvider(), push_repository="repo/x"
        )

        job_manifest = next(json.loads(c["input"]) for c in calls if c["argv"][1] == "apply" and json.loads(c["input"])["kind"] == "Job")
        assert job_manifest["spec"]["template"]["spec"]["nodeName"] == "worker-1"

    def test_streams_log_lines(self, monkeypatch):
        self._prepare(monkeypatch, log_lines=["line1\n", "line2\n"])

        engine = KanikoBuildEngine()
        seen_lines = []
        engine.build_image(
            "/ctx",
            "Dockerfile",
            ["x:1"],
            on_log_line=seen_lines.append,
            registry_provider=_FakeRegistryProvider(),
            push_repository="repo/x",
        )

        assert seen_lines == ["line1\n", "line2\n"]

    def test_job_reporting_failed_returns_failure_and_still_cleans_up(self, monkeypatch):
        calls, _ = self._prepare(monkeypatch, job_status="failed")

        engine = KanikoBuildEngine()
        result = engine.build_image(
            "/ctx", "Dockerfile", ["x:1"], registry_provider=_FakeRegistryProvider(), push_repository="repo/x"
        )

        assert result.success is False
        assert result.pushed is False
        assert "did not complete successfully" in result.error
        assert any(c["argv"][1] == "delete" and c["argv"][2] == "job" for c in calls)
        assert any(c["argv"][1] == "delete" and c["argv"][2] == "secret" for c in calls)

    def test_job_status_timeout_returns_failure(self, monkeypatch):
        # "pending" leaves status {} forever — never succeeded, never failed
        # — so _wait_for_job_completion must eventually give up rather than
        # loop forever. Shrunk to near-zero so the test itself stays fast.
        monkeypatch.setattr("app.services.build.engine.KANIKO_JOB_STATUS_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr("app.services.build.engine.KANIKO_JOB_STATUS_POLL_INTERVAL_SECONDS", 0.01)
        self._prepare(monkeypatch, job_status="pending")

        engine = KanikoBuildEngine()
        result = engine.build_image(
            "/ctx", "Dockerfile", ["x:1"], registry_provider=_FakeRegistryProvider(), push_repository="repo/x"
        )

        assert result.success is False
        assert "Timed out" in result.error

    def test_kubectl_apply_nonzero_exit_returns_failure_without_raising(self, monkeypatch):
        monkeypatch.setenv("KANIKO_WORKSPACE_HOST_PATH", "/mnt/data")
        monkeypatch.setattr("app.services.build.engine.KanikoBuildEngine._namespace", lambda self: "test-ns")
        monkeypatch.setattr(
            "app.services.build.engine.subprocess.run",
            lambda argv, **kwargs: FakeCompletedProcess(returncode=1, stderr="secrets is forbidden"),
        )

        engine = KanikoBuildEngine()
        result = engine.build_image(
            "/ctx", "Dockerfile", ["x:1"], registry_provider=_FakeRegistryProvider(), push_repository="repo/x"
        )

        assert result.success is False
        assert "forbidden" in result.error

    def test_missing_kubectl_binary_returns_failure_without_raising(self, monkeypatch):
        monkeypatch.setenv("KANIKO_WORKSPACE_HOST_PATH", "/mnt/data")
        monkeypatch.setattr("app.services.build.engine.KanikoBuildEngine._namespace", lambda self: "test-ns")

        def fake_run(argv, **kwargs):
            raise OSError("No such file or directory")

        monkeypatch.setattr("app.services.build.engine.subprocess.run", fake_run)

        engine = KanikoBuildEngine()
        result = engine.build_image(
            "/ctx", "Dockerfile", ["x:1"], registry_provider=_FakeRegistryProvider(), push_repository="repo/x"
        )

        assert result.success is False
        assert "kubectl apply failed to start" in result.error

    def test_missing_kubectl_for_log_streaming_returns_failure_without_raising(self, monkeypatch):
        calls, _ = self._prepare(monkeypatch)

        def fake_popen(argv, **kwargs):
            raise OSError("No such file or directory")

        monkeypatch.setattr("app.services.build.engine.subprocess.Popen", fake_popen)

        engine = KanikoBuildEngine()
        result = engine.build_image(
            "/ctx", "Dockerfile", ["x:1"], registry_provider=_FakeRegistryProvider(), push_repository="repo/x"
        )

        assert result.success is False
        assert "Failed to start kubectl logs" in result.error
        # Cleanup still runs even though the build itself never got a
        # verdict — the Job/Secret it already created must not be
        # abandoned in the cluster.
        assert any(c["argv"][1] == "delete" and c["argv"][2] == "job" for c in calls)
