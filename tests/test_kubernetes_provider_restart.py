"""Unit tests for the two restart mechanisms on KubernetesProvider:
- restart(manifest_yaml): now delete()+apply() instead of `kubectl rollout
  restart -f -` (see kubernetes_provider.py's docstring for why).
- restart_deployment(namespace, name): the new true rollout-restart, used
  only by the dedicated Workloads tab against one named Deployment object.

Mocks only `subprocess.run`, same approach as the other
test_kubernetes_provider_*.py files.
"""
import subprocess

import pytest

from app.services.deployment.kubernetes_provider import KubernetesProvider

MANIFEST_YAML = '{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "x"}}'


@pytest.fixture
def provider():
    return KubernetesProvider(kubeconfig="fake-kubeconfig-yaml")


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestRestartIsDeleteThenApply:
    def test_success_calls_delete_then_apply_with_the_same_manifest(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append({"args": args, "input": input})
            return _completed(returncode=0, stdout="ok\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.restart(MANIFEST_YAML)

        assert result.success is True
        assert len(calls) == 2
        assert calls[0]["args"] == ["kubectl", "delete", "-f", "-", "--ignore-not-found=true"]
        assert calls[0]["input"] == MANIFEST_YAML
        assert calls[1]["args"] == ["kubectl", "apply", "-f", "-"]
        assert calls[1]["input"] == MANIFEST_YAML

    def test_delete_failure_short_circuits_and_apply_is_never_called(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            if args[1] == "delete":
                return _completed(returncode=1, stderr="delete failed")
            raise AssertionError("apply should not be called if delete failed")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.restart(MANIFEST_YAML)

        assert result.success is False
        assert len(calls) == 1

    def test_apply_failure_after_successful_delete_is_reported(self, provider, monkeypatch):
        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            if args[1] == "delete":
                return _completed(returncode=0, stdout="deleted\n")
            return _completed(returncode=1, stderr="apply failed")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.restart(MANIFEST_YAML)

        assert result.success is False
        assert "deleted" in result.log
        assert result.error is not None


class TestRestartDeployment:
    def test_calls_kubectl_rollout_restart_deployment_with_namespace(self, provider, monkeypatch):
        captured = {}

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            captured["args"] = args
            return _completed(returncode=0, stdout="deployment.apps/app-abc restarted\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.restart_deployment("default", "app-abc")

        assert result.success is True
        assert captured["args"] == ["kubectl", "rollout", "restart", "deployment/app-abc", "-n", "default"]

    def test_failure_is_reported(self, provider, monkeypatch):
        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            return _completed(returncode=1, stderr='deployments.apps "app-abc" not found')

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.restart_deployment("default", "app-abc")

        assert result.success is False
        assert "not found" in result.log
