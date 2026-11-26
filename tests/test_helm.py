"""Helm chart manifests: the helm commands KubernetesProvider builds, and the
helpers in app/services/deployment/helm.py. Mocks only `subprocess.run`, same
approach as the test_kubernetes_provider_*.py files."""
import json
import os
import subprocess

import pytest

from app.services.deployment import kubernetes_provider
from app.services.deployment.helm import HelmChart, HelmError, live_status_from_json, prepare_chart, show_values
from app.services.deployment.kubernetes_provider import KubernetesProvider

RELEASE_MANIFEST = """---
apiVersion: v1
kind: Service
metadata:
  name: web
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  selector:
    matchLabels: {app: web}
"""


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class _Calls(list):
    """A list of recorded calls that can also carry .responses."""


@pytest.fixture
def calls(monkeypatch):
    """Records every subprocess.run; `responses` maps a command word (args[1])
    to the CompletedProcess to return."""
    recorded = _Calls()
    responses = {}

    def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
        call = {"args": args, "input": input, "env": env or {}, "timeout": timeout}
        values_flag = args.index("-f") if "-f" in args else None
        if values_flag is not None:
            with open(args[values_flag + 1]) as values_file:
                call["values"] = values_file.read()
        recorded.append(call)
        return responses.get(args[1], _completed(stdout="ok\n"))

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr(kubernetes_provider, "rollback_on_failure_flag", lambda: "--rollback-on-failure")
    recorded.responses = responses
    return recorded


@pytest.fixture
def provider():
    return KubernetesProvider(kubeconfig="fake-kubeconfig-yaml")


HTTPS_CHART = HelmChart(repo="https://charts.example.com", name="web", version="1.2.3")


class TestPrepareChart:
    def test_https_repo_is_added_under_an_alias(self, calls):
        def run(args, input_text=None, timeout=None):
            return subprocess.run(["helm", *args], input=input_text)

        ref = prepare_chart(run, HTTPS_CHART, "/unused")
        assert ref == "masimple/web"
        assert calls[0]["args"] == ["helm", "repo", "add", "masimple", "https://charts.example.com"]
        assert calls[0]["input"] is None

    def test_private_https_repo_password_goes_over_stdin_not_args(self, calls):
        def run(args, input_text=None, timeout=None):
            return subprocess.run(["helm", *args], input=input_text)

        chart = HelmChart(repo="https://charts.example.com", name="web", version="1", username="bot", password="s3cret")
        prepare_chart(run, chart, "/unused")
        assert "s3cret" not in calls[0]["args"]
        assert calls[0]["args"][-3:] == ["--username", "bot", "--password-stdin"]
        assert calls[0]["input"] == "s3cret"

    def test_private_oci_registry_logs_in_to_the_host(self, calls):
        def run(args, input_text=None, timeout=None):
            return subprocess.run(["helm", *args], input=input_text)

        chart = HelmChart(repo="oci://ghcr.io/org/charts/", name="web", version="1", username="bot", password="tok")
        ref = prepare_chart(run, chart, "/unused")
        assert ref == "oci://ghcr.io/org/charts/web"
        assert calls[0]["args"] == ["helm", "registry", "login", "ghcr.io", "--username", "bot", "--password-stdin"]
        assert calls[0]["input"] == "tok"

    def test_public_oci_chart_needs_no_setup_call(self, calls):
        def run(args, input_text=None, timeout=None):
            return subprocess.run(["helm", *args], input=input_text)

        assert prepare_chart(run, HelmChart(repo="oci://ghcr.io/org", name="web", version="1"), "/unused") == "oci://ghcr.io/org/web"
        assert calls == []

    def test_repo_add_failure_raises_with_helms_message(self, calls):
        calls.responses["repo"] = _completed(returncode=1, stderr="looks like it is not a valid chart repository")
        with pytest.raises(HelmError, match="not a valid chart repository"):
            show_values(HTTPS_CHART)


class TestHelmUpgrade:
    def test_installs_with_values_file_wait_and_rollback(self, provider, calls):
        result = provider.helm_upgrade(HTTPS_CHART, "web", "apps", "image:\n  tag: v9\n", wait_timeout_seconds=120)

        assert result.success is True
        upgrade = calls[-1]
        args = upgrade["args"]
        assert args[:5] == ["helm", "upgrade", "--install", "web", "masimple/web"]
        for expected in (["--version", "1.2.3"], ["--namespace", "apps"]):
            index = args.index(expected[0])
            assert args[index:index + 2] == expected
        assert "--create-namespace" in args
        assert "--wait" in args and "--timeout=120s" in args and "--rollback-on-failure" in args
        assert upgrade["values"] == "image:\n  tag: v9\n"
        assert upgrade["timeout"] > 120  # the subprocess deadline outlasts helm's own

    def test_each_call_gets_its_own_helm_home_and_kubeconfig(self, provider, calls):
        provider.helm_upgrade(HTTPS_CHART, "web", "apps", "")
        env = calls[-1]["env"]
        for key in ("HELM_CONFIG_HOME", "HELM_CACHE_HOME", "HELM_DATA_HOME", "KUBECONFIG"):
            assert key in env
        assert not os.path.exists(env["HELM_CONFIG_HOME"])  # removed once the call returns

    def test_zero_wait_skips_wait_and_rollback(self, provider, calls):
        provider.helm_upgrade(HTTPS_CHART, "web", "apps", "", wait_timeout_seconds=0)
        args = calls[-1]["args"]
        assert "--wait" not in args and "--rollback-on-failure" not in args

    def test_failure_is_reported_with_helms_output(self, provider, calls):
        calls.responses["upgrade"] = _completed(returncode=1, stderr="UPGRADE FAILED: timed out")
        result = provider.helm_upgrade(HTTPS_CHART, "web", "apps", "")
        assert result.success is False
        assert "UPGRADE FAILED" in result.log

    def test_chart_setup_failure_fails_the_deploy_without_upgrading(self, provider, calls):
        calls.responses["repo"] = _completed(returncode=1, stderr="no such host")
        result = provider.helm_upgrade(HTTPS_CHART, "web", "apps", "")
        assert result.success is False and "no such host" in result.error
        assert all(call["args"][1] != "upgrade" for call in calls)


class TestUninstallAndStatus:
    def test_uninstall_ignores_an_already_missing_release(self, provider, calls):
        assert provider.helm_uninstall("web", "apps").success is True
        assert calls[-1]["args"] == ["helm", "uninstall", "web", "--namespace", "apps", "--ignore-not-found"]

    @pytest.mark.parametrize(
        "status,expected", [("deployed", "live"), ("uninstalled", "missing"), ("failed", "unknown"), ("pending-upgrade", "unknown")]
    )
    def test_status_mapping(self, status, expected):
        assert live_status_from_json(json.dumps({"info": {"status": status}})) == expected

    def test_missing_release_is_missing(self, provider, calls):
        calls.responses["status"] = _completed(returncode=1, stderr="Error: release: not found")
        assert provider.helm_live_status("web", "apps") == "missing"

    def test_other_status_errors_raise(self, provider, calls):
        calls.responses["status"] = _completed(returncode=1, stderr="Unauthorized")
        with pytest.raises(RuntimeError, match="Unauthorized"):
            provider.helm_live_status("web", "apps")


class TestHelmRestart:
    def test_rolling_restarts_each_workload_in_the_release_namespace(self, provider, calls):
        calls.responses["get"] = _completed(stdout=RELEASE_MANIFEST)
        result = provider.helm_restart("web", "apps", wait_timeout_seconds=0)

        assert result.success is True
        assert calls[0]["args"] == ["helm", "get", "manifest", "web", "--namespace", "apps"]
        assert calls[1]["args"] == ["kubectl", "rollout", "restart", "deployment/web", "-n", "apps"]
        assert len(calls) == 2  # the Service isn't restarted, and no wait was asked for

    def test_waits_for_rollout_in_the_release_namespace(self, provider, calls, monkeypatch):
        calls.responses["get"] = _completed(stdout=RELEASE_MANIFEST)
        waited = {}

        def _fake_wait(self, manifest_yaml, wait_timeout_seconds, default_namespace=None):
            waited.update(timeout=wait_timeout_seconds, namespace=default_namespace)
            from app.services.deployment.base import DeployResult

            return DeployResult(success=True, log="ready")

        monkeypatch.setattr(KubernetesProvider, "_wait_for_rollout", _fake_wait)
        assert provider.helm_restart("web", "apps", wait_timeout_seconds=60).success is True
        assert waited == {"timeout": 60, "namespace": "apps"}

    def test_missing_release_fails(self, provider, calls):
        calls.responses["get"] = _completed(returncode=1, stderr="release: not found")
        assert provider.helm_restart("web", "apps").success is False
