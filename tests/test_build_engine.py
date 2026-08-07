import base64
import json
import os

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

    def get(self, ref):
        self.get_calls.append(ref)
        if self._raise_not_found:
            raise docker.errors.ImageNotFound("not found")
        return FakeImage(self._image_id, self._size)


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
    monkeypatch.setattr("docker.from_env", lambda: sentinel)
    engine = DockerBuildEngine()
    assert engine.client is sentinel


class _FakeRegistryProvider:
    def __init__(self, username="myuser", password="mypass", registry_host="registry-1.docker.io"):
        self.username = username
        self.password = password
        self.registry_host = registry_host


class FakeKanikoProcess:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self._final_returncode = returncode
        self.returncode = None

    def wait(self):
        self.returncode = self._final_returncode
        return self.returncode


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

    def test_successful_build_returns_pushed_result_and_writes_docker_config(self, monkeypatch):
        captured = {}

        def fake_popen(argv, stdout=None, stderr=None, text=None, env=None):
            with open(os.path.join(env["DOCKER_CONFIG"], "config.json")) as config_file:
                captured["config"] = json.load(config_file)
            captured["argv"] = argv
            return FakeKanikoProcess(["Pushed image\n"], returncode=0)

        monkeypatch.setattr("app.services.build.engine.subprocess.Popen", fake_popen)

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
        assert "--destination=myuser/myapp:1.0.0" in captured["argv"]
        assert "--context=dir:///ctx" in captured["argv"]
        assert "--dockerfile=Dockerfile" in captured["argv"]

        auth_entry = captured["config"]["auths"]["registry-1.docker.io"]
        decoded = base64.b64decode(auth_entry["auth"]).decode()
        assert decoded == "myuser:mypass"

    def test_build_args_are_forwarded_as_flags(self, monkeypatch):
        captured = {}

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            return FakeKanikoProcess([], returncode=0)

        monkeypatch.setattr("app.services.build.engine.subprocess.Popen", fake_popen)

        engine = KanikoBuildEngine()
        engine.build_image(
            "/ctx",
            "Dockerfile",
            ["x:1"],
            build_args={"VERSION": "1.2.3"},
            registry_provider=_FakeRegistryProvider(),
            push_repository="repo/x",
        )

        assert "--build-arg=VERSION=1.2.3" in captured["argv"]

    def test_streams_log_lines(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.build.engine.subprocess.Popen",
            lambda argv, **kwargs: FakeKanikoProcess(["line1\n", "line2\n"], returncode=0),
        )

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

    def test_nonzero_exit_code_returns_failure(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.build.engine.subprocess.Popen",
            lambda argv, **kwargs: FakeKanikoProcess(["error building\n"], returncode=1),
        )

        engine = KanikoBuildEngine()
        result = engine.build_image(
            "/ctx", "Dockerfile", ["x:1"], registry_provider=_FakeRegistryProvider(), push_repository="repo/x"
        )

        assert result.success is False
        assert result.pushed is False
        assert "exited with code 1" in result.error

    def test_missing_executor_binary_returns_failure_without_raising(self, monkeypatch):
        def fake_popen(argv, **kwargs):
            raise OSError("No such file or directory")

        monkeypatch.setattr("app.services.build.engine.subprocess.Popen", fake_popen)

        engine = KanikoBuildEngine()
        result = engine.build_image(
            "/ctx", "Dockerfile", ["x:1"], registry_provider=_FakeRegistryProvider(), push_repository="repo/x"
        )

        assert result.success is False
        assert "Failed to start kaniko-executor" in result.error
