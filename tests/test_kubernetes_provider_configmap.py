"""Unit tests for the ConfigMap methods added to KubernetesProvider — mocks
only `subprocess.run`, same approach as
tests/test_kubernetes_provider_namespace_secret.py.
"""
import json
import subprocess

import pytest

from app.services.deployment.kubernetes_provider import KubernetesProvider


@pytest.fixture
def provider():
    return KubernetesProvider(kubeconfig="fake-kubeconfig-yaml")


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestListConfigMaps:
    def test_returns_real_values_not_just_key_names(self, provider, monkeypatch):
        fake_output = json.dumps(
            {
                "items": [
                    {
                        "metadata": {
                            "name": "app-config",
                            "namespace": "default",
                            "creationTimestamp": "2026-08-01T00:00:00Z",
                        },
                        "data": {"LOG_LEVEL": "debug", "FEATURE_X": "on"},
                    }
                ]
            }
        )

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            return _completed(returncode=0, stdout=fake_output)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        configmaps = provider.list_configmaps()

        assert len(configmaps) == 1
        assert configmaps[0]["name"] == "app-config"
        assert configmaps[0]["data"] == {"LOG_LEVEL": "debug", "FEATURE_X": "on"}

    def test_missing_data_defaults_to_empty_dict(self, provider, monkeypatch):
        fake_output = json.dumps(
            {"items": [{"metadata": {"name": "empty-cm", "namespace": "default"}}]}
        )

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            return _completed(returncode=0, stdout=fake_output)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        configmaps = provider.list_configmaps()
        assert configmaps[0]["data"] == {}


class TestCreateConfigMap:
    def test_builds_configmap_manifest_and_applies_it(self, provider, monkeypatch):
        captured = {}

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            captured["args"] = args
            captured["input"] = input
            return _completed(returncode=0, stdout="configmap/app-config created\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.create_configmap("default", "app-config", {"LOG_LEVEL": "debug"})

        assert result.success is True
        assert captured["args"] == ["kubectl", "apply", "-f", "-"]
        manifest = json.loads(captured["input"])
        assert manifest["kind"] == "ConfigMap"
        assert manifest["metadata"] == {"name": "app-config", "namespace": "default"}
        assert manifest["data"] == {"LOG_LEVEL": "debug"}
        # Unlike a Secret, a ConfigMap has no stringData/type distinction.
        assert "stringData" not in manifest
        assert "type" not in manifest


class TestDeleteConfigMap:
    def test_calls_kubectl_delete_configmap_with_namespace(self, provider, monkeypatch):
        captured = {}

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            captured["args"] = args
            return _completed(returncode=0, stdout="configmap/app-config deleted\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.delete_configmap("default", "app-config")

        assert result.success is True
        assert captured["args"] == [
            "kubectl", "delete", "configmap", "app-config", "-n", "default", "--ignore-not-found=true",
        ]
