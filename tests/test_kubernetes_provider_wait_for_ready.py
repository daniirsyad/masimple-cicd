"""Unit tests for KubernetesProvider's wait_timeout_seconds handling on
apply()/restart() — after a successful `kubectl apply`, polling `kubectl
rollout status` for every Deployment/StatefulSet/DaemonSet in the manifest
before the result counts as a success (see kubernetes_provider.py's
apply()/_wait_for_rollout()/_rollout_targets()).

Mocks only `subprocess.run`, same approach as test_kubernetes_provider_restart.py.
"""
import subprocess

import pytest

from app.services.deployment.custom_api_provider import CustomAPIProvider
from app.services.deployment.kubernetes_provider import KubernetesProvider

CONFIGMAP_ONLY_YAML = '{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "x"}}'

DEPLOYMENT_WITH_NAMESPACE_YAML = (
    '{"apiVersion": "apps/v1", "kind": "Deployment", '
    '"metadata": {"name": "app-abc", "namespace": "prod"}}'
)

DEPLOYMENT_NO_NAMESPACE_YAML = '{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "app-abc"}}'

MULTI_KIND_YAML = "\n".join(
    [
        '{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "cfg"}}',
        "---",
        '{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "dep-a", "namespace": "ns1"}}',
        "---",
        '{"apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": {"name": "sts-a", "namespace": "ns1"}}',
        "---",
        '{"apiVersion": "apps/v1", "kind": "DaemonSet", "metadata": {"name": "ds-a", "namespace": "ns1"}}',
    ]
)

TWO_DEPLOYMENTS_YAML = "\n".join(
    [
        '{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "dep-a", "namespace": "ns1"}}',
        "---",
        '{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "dep-b", "namespace": "ns1"}}',
    ]
)


@pytest.fixture
def provider():
    return KubernetesProvider(kubeconfig="fake-kubeconfig-yaml")


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestApplySkipsWait:
    def test_no_timeout_skips_rollout_wait_entirely(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            return _completed(returncode=0, stdout="applied\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=None)

        assert result.success is True
        assert len(calls) == 1
        assert calls[0] == ["kubectl", "apply", "-f", "-"]

    def test_zero_timeout_skips_rollout_wait(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            return _completed(returncode=0, stdout="applied\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=0)

        assert result.success is True
        assert len(calls) == 1

    def test_configmap_only_manifest_skips_wait_even_with_timeout_set(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            return _completed(returncode=0, stdout="applied\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(CONFIGMAP_ONLY_YAML, wait_timeout_seconds=300)

        assert result.success is True
        assert len(calls) == 1

    def test_apply_failure_never_calls_rollout_status(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            return _completed(returncode=1, stderr="apply failed")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=300)

        assert result.success is False
        assert len(calls) == 1


class TestApplyWaitsForRollout:
    def test_runs_rollout_status_for_deployment_with_namespace(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            if args[1] == "apply":
                return _completed(returncode=0, stdout="applied\n")
            return _completed(returncode=0, stdout="deployment \"app-abc\" successfully rolled out\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=300)

        assert result.success is True
        assert len(calls) == 2
        assert calls[1] == ["kubectl", "rollout", "status", "deployment/app-abc", "--timeout=300s", "-n", "prod"]
        assert "applied" in result.log
        assert "successfully rolled out" in result.log

    def test_omits_namespace_flag_when_manifest_has_none(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            return _completed(returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(DEPLOYMENT_NO_NAMESPACE_YAML, wait_timeout_seconds=300)

        assert result.success is True
        assert calls[1] == ["kubectl", "rollout", "status", "deployment/app-abc", "--timeout=300s"]

    def test_rollout_status_failure_marks_result_failed(self, provider, monkeypatch):
        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            if args[1] == "apply":
                return _completed(returncode=0)
            return _completed(returncode=1, stderr="error: timed out waiting for the condition\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=300)

        assert result.success is False
        assert "deployment/app-abc" in result.error
        assert "300s" in result.error

    def test_waits_for_every_workload_kind_and_ignores_configmap(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            return _completed(returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(MULTI_KIND_YAML, wait_timeout_seconds=120)

        assert result.success is True
        rollout_calls = [c for c in calls if c[1] == "rollout"]
        assert len(rollout_calls) == 3
        resource_refs = {c[3] for c in rollout_calls}
        assert resource_refs == {"deployment/dep-a", "statefulset/sts-a", "daemonset/ds-a"}

    def test_one_failing_resource_among_several_fails_the_whole_result(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            if args[1] == "apply":
                return _completed(returncode=0)
            if "dep-a" in args[3]:
                return _completed(returncode=0)
            return _completed(returncode=1, stderr="failed")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(TWO_DEPLOYMENTS_YAML, wait_timeout_seconds=120)

        assert result.success is False
        rollout_calls = [c for c in calls if c[1] == "rollout"]
        assert len(rollout_calls) == 2  # both attempted, no short-circuit
        assert "dep-b" in result.error

    def test_shared_deadline_is_not_reissued_per_resource(self, provider, monkeypatch):
        import app.services.deployment.kubernetes_provider as kp_module

        calls = []
        # First call to time.monotonic() establishes the deadline; simulate
        # a lot of time passing before the second resource's turn so its
        # own --timeout= reflects the shrunk remaining budget, not the
        # full wait_timeout_seconds again.
        monotonic_values = iter([1000.0, 1000.0, 1080.0, 1080.0])

        def _fake_monotonic():
            return next(monotonic_values, 1080.0)

        monkeypatch.setattr(kp_module.time, "monotonic", _fake_monotonic)

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            if args[1] == "apply":
                return _completed(returncode=0)
            return _completed(returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(TWO_DEPLOYMENTS_YAML, wait_timeout_seconds=100)

        assert result.success is True
        rollout_calls = [c for c in calls if c[1] == "rollout"]
        first_timeout = next(a for a in rollout_calls[0] if a.startswith("--timeout="))
        second_timeout = next(a for a in rollout_calls[1] if a.startswith("--timeout="))
        assert first_timeout == "--timeout=100s"
        # Deadline = 1000 + 100 = 1100; second call starts at monotonic()=1080 -> remaining ~20s.
        assert second_timeout == "--timeout=20s"


class TestRestartThreadsWaitTimeoutIntoApply:
    def test_restart_passes_wait_timeout_seconds_through_to_apply(self, provider, monkeypatch):
        captured = {}

        def _fake_apply(self, manifest_yaml, wait_timeout_seconds=None):
            captured["wait_timeout_seconds"] = wait_timeout_seconds
            from app.services.deployment.base import DeployResult

            return DeployResult(success=True, log="applied\n")

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            return _completed(returncode=0, stdout="deleted\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        monkeypatch.setattr(KubernetesProvider, "apply", _fake_apply)

        result = provider.restart(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=42)

        assert result.success is True
        assert captured["wait_timeout_seconds"] == 42


class TestCustomAPIProviderIgnoresWaitTimeout:
    def test_apply_accepts_and_ignores_wait_timeout_seconds(self, monkeypatch):
        import requests

        provider = CustomAPIProvider(api_url="https://agent.example.com/apply")

        class _FakeResponse:
            status_code = 200
            text = "ok"

        def _fake_post(url, data=None, headers=None, timeout=None):
            return _FakeResponse()

        monkeypatch.setattr(requests, "post", _fake_post)
        result = provider.apply(CONFIGMAP_ONLY_YAML, wait_timeout_seconds=300)

        assert result.success is True
