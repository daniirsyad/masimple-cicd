"""Unit tests for KubernetesProvider.stream_pod_logs — the `kubectl logs -f`
generator behind the SSE pod-logs-stream endpoint. Mocks only
`subprocess.Popen`, same approach as the `subprocess.run` mocks in the other
test_kubernetes_provider_*.py files.
"""
import subprocess

import pytest

from app.services.deployment.kubernetes_provider import KubernetesProvider


@pytest.fixture
def provider():
    return KubernetesProvider(kubeconfig="fake-kubeconfig-yaml")


class _FakeProcess:
    def __init__(self, lines, returncode=0, stderr=""):
        self.stdout = iter(lines)
        self._stderr_text = stderr
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self._waited = False

    @property
    def stderr(self):
        return self

    def read(self):
        return self._stderr_text

    def poll(self):
        return self.returncode if self._waited else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self._waited = True
        return self.returncode


class TestStreamPodLogsHappyPath:
    def test_yields_each_line_stripped_of_its_trailing_newline(self, provider, monkeypatch):
        fake = _FakeProcess(["hello\n", "world\n"], returncode=0)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)

        lines = list(provider.stream_pod_logs("default", "app-abc123"))

        assert lines == ["hello", "world"]

    def test_passes_follow_and_tail_flags_and_container(self, provider, monkeypatch):
        captured = {}

        def _fake_popen(args, **kwargs):
            captured["args"] = args
            return _FakeProcess([], returncode=0)

        monkeypatch.setattr(subprocess, "Popen", _fake_popen)
        list(provider.stream_pod_logs("default", "app-abc123", container="sidecar"))

        assert "-f" in captured["args"]
        assert "-c" in captured["args"]
        assert "sidecar" in captured["args"]


class TestStreamPodLogsFailure:
    def test_nonzero_exit_after_stdout_closes_raises_with_stderr(self, provider, monkeypatch):
        fake = _FakeProcess(["partial line\n"], returncode=1, stderr="pod not found\n")
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)

        gen = provider.stream_pod_logs("default", "missing-pod")
        assert next(gen) == "partial line"
        with pytest.raises(RuntimeError, match="pod not found"):
            next(gen)


class TestStreamPodLogsCleanup:
    def test_closing_the_generator_early_terminates_the_process(self, provider, monkeypatch):
        fake = _FakeProcess(["line one\n", "line two\n", "line three\n"], returncode=0)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)

        gen = provider.stream_pod_logs("default", "app-abc123")
        assert next(gen) == "line one"
        gen.close()

        assert fake.terminated is True
