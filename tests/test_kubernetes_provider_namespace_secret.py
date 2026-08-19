"""Unit tests for the namespace/secret methods added to KubernetesProvider.

Unlike tests/test_deployment_pods.py (which exercises the Flask routes with
KubernetesProvider fully monkeypatched out), these mock only `subprocess.run`
so the provider's own JSON-manifest-building and merge logic — especially
update_secret's "keep untouched keys verbatim, never decode them" behavior —
actually gets exercised somewhere.
"""
import base64
import json
import subprocess

import pytest

from app.services.deployment.kubernetes_provider import KubernetesProvider


@pytest.fixture
def provider():
    return KubernetesProvider(kubeconfig="fake-kubeconfig-yaml")


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestCreateNamespace:
    def test_builds_namespace_manifest_and_applies_it(self, provider, monkeypatch):
        captured = {}

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            captured["args"] = args
            captured["input"] = input
            return _completed(returncode=0, stdout="namespace/test-ns created\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        result = provider.create_namespace("test-ns", labels={"team": "platform"})

        assert result.success is True
        assert captured["args"] == ["kubectl", "apply", "-f", "-"]
        manifest = json.loads(captured["input"])
        assert manifest["kind"] == "Namespace"
        assert manifest["metadata"]["name"] == "test-ns"
        assert manifest["metadata"]["labels"] == {"team": "platform"}

    def test_no_labels_key_when_labels_omitted(self, provider, monkeypatch):
        captured = {}

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            captured["input"] = input
            return _completed(returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        provider.create_namespace("test-ns")
        manifest = json.loads(captured["input"])
        assert "labels" not in manifest["metadata"]


class TestDeleteNamespace:
    def test_calls_kubectl_delete_namespace(self, provider, monkeypatch):
        captured = {}

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            captured["args"] = args
            return _completed(returncode=0, stdout="namespace/test-ns deleted\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.delete_namespace("test-ns")

        assert result.success is True
        assert captured["args"] == ["kubectl", "delete", "namespace", "test-ns", "--ignore-not-found=true"]


class TestListSecrets:
    def test_returns_key_names_never_values(self, provider, monkeypatch):
        fake_output = json.dumps(
            {
                "items": [
                    {
                        "metadata": {"name": "db-creds", "namespace": "default", "creationTimestamp": "2026-08-01T00:00:00Z"},
                        "type": "Opaque",
                        "data": {"username": "dXNlcg==", "password": "cGFzcw=="},
                    }
                ]
            }
        )

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            return _completed(returncode=0, stdout=fake_output)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        secrets = provider.list_secrets()

        assert len(secrets) == 1
        assert secrets[0]["name"] == "db-creds"
        assert secrets[0]["keys"] == ["password", "username"]
        # The raw base64 values from `data` must never surface in the result.
        assert "dXNlcg==" not in json.dumps(secrets)
        assert "cGFzcw==" not in json.dumps(secrets)


class TestGetSecret:
    def test_returns_type_and_keys_only(self, provider, monkeypatch):
        fake_output = json.dumps({"type": "Opaque", "data": {"api_key": "c2VjcmV0"}})

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            return _completed(returncode=0, stdout=fake_output)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        info = provider.get_secret("default", "some-secret")

        assert info == {"type": "Opaque", "keys": ["api_key"]}


class TestCreateSecret:
    def test_builds_opaque_manifest_with_string_data(self, provider, monkeypatch):
        captured = {}

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            captured["args"] = args
            captured["input"] = input
            return _completed(returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        provider.create_secret("default", "db-creds", {"username": "admin", "password": "hunter2"})

        manifest = json.loads(captured["input"])
        assert manifest["kind"] == "Secret"
        assert manifest["type"] == "Opaque"
        assert manifest["metadata"] == {"name": "db-creds", "namespace": "default"}
        assert manifest["stringData"] == {"username": "admin", "password": "hunter2"}
        assert "data" not in manifest


class TestUpdateSecret:
    def test_keeps_untouched_keys_verbatim_and_never_decodes_them(self, provider, monkeypatch):
        get_output = json.dumps(
            {"type": "Opaque", "data": {"username": "dXNlcg==", "password": "b2xkcGFzcw=="}}
        )
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append({"args": args, "input": input})
            if args[:2] == ["kubectl", "get"]:
                return _completed(returncode=0, stdout=get_output)
            return _completed(returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        provider.update_secret("default", "db-creds", data_updates={"password": "newpass"})

        apply_call = next(c for c in calls if c["args"][:2] == ["kubectl", "apply"])
        manifest = json.loads(apply_call["input"])
        # "username" was never mentioned in data_updates -> carried through
        # as its still-base64 value, untouched, never decoded.
        assert manifest["data"] == {"username": "dXNlcg=="}
        assert manifest["stringData"] == {"password": "newpass"}

    def test_removed_keys_are_dropped_from_both_data_and_string_data(self, provider, monkeypatch):
        get_output = json.dumps({"type": "Opaque", "data": {"username": "dXNlcg==", "password": "b2xkcGFzcw=="}})
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append({"args": args, "input": input})
            if args[:2] == ["kubectl", "get"]:
                return _completed(returncode=0, stdout=get_output)
            return _completed(returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        provider.update_secret("default", "db-creds", data_updates={}, removed_keys=["password"])

        apply_call = next(c for c in calls if c["args"][:2] == ["kubectl", "apply"])
        manifest = json.loads(apply_call["input"])
        assert manifest["data"] == {"username": "dXNlcg=="}
        assert manifest["stringData"] == {}


class TestCreateImagePullSecret:
    def test_builds_dockerconfigjson_with_base64_auth(self, provider, monkeypatch):
        captured = {}

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            captured["input"] = input
            return _completed(returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        provider.create_image_pull_secret(
            "default", "regcred", "https://index.docker.io/v1/", "myuser", "mypassword", email="a@example.com"
        )

        manifest = json.loads(captured["input"])
        assert manifest["type"] == "kubernetes.io/dockerconfigjson"
        dockerconfigjson = json.loads(manifest["stringData"][".dockerconfigjson"])
        entry = dockerconfigjson["auths"]["https://index.docker.io/v1/"]
        assert entry["username"] == "myuser"
        assert entry["password"] == "mypassword"
        assert entry["email"] == "a@example.com"
        assert base64.b64decode(entry["auth"]).decode() == "myuser:mypassword"


class TestDeleteSecret:
    def test_calls_kubectl_delete_secret_with_namespace(self, provider, monkeypatch):
        captured = {}

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            captured["args"] = args
            return _completed(returncode=0, stdout="secret/db-creds deleted\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.delete_secret("default", "db-creds")

        assert result.success is True
        assert captured["args"] == ["kubectl", "delete", "secret", "db-creds", "-n", "default", "--ignore-not-found=true"]


class TestFindDeploymentsUsingSecret:
    def _deployment(self, name, pod_spec):
        return {"metadata": {"name": name}, "spec": {"template": {"spec": pod_spec}}}

    def test_matches_env_value_from_secret_key_ref(self, provider, monkeypatch):
        deployments = [
            self._deployment(
                "api",
                {
                    "containers": [
                        {
                            "name": "api",
                            "env": [{"name": "DB_PASS", "valueFrom": {"secretKeyRef": {"name": "db-creds"}}}],
                        }
                    ]
                },
            ),
            self._deployment("worker", {"containers": [{"name": "worker", "env": []}]}),
        ]
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: _completed(returncode=0, stdout=json.dumps({"items": deployments})),
        )

        assert provider.find_deployments_using_secret("default", "db-creds") == ["api"]

    def test_matches_env_from_secret_ref(self, provider, monkeypatch):
        deployments = [
            self._deployment(
                "api", {"containers": [{"name": "api", "envFrom": [{"secretRef": {"name": "db-creds"}}]}]}
            ),
        ]
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: _completed(returncode=0, stdout=json.dumps({"items": deployments})),
        )

        assert provider.find_deployments_using_secret("default", "db-creds") == ["api"]

    def test_matches_volume_mounted_secret(self, provider, monkeypatch):
        deployments = [
            self._deployment(
                "api",
                {"containers": [{"name": "api"}], "volumes": [{"name": "creds", "secret": {"secretName": "db-creds"}}]},
            ),
        ]
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: _completed(returncode=0, stdout=json.dumps({"items": deployments})),
        )

        assert provider.find_deployments_using_secret("default", "db-creds") == ["api"]

    def test_matches_init_container_reference(self, provider, monkeypatch):
        deployments = [
            self._deployment(
                "api",
                {
                    "containers": [{"name": "api"}],
                    "initContainers": [
                        {"name": "migrate", "envFrom": [{"secretRef": {"name": "db-creds"}}]}
                    ],
                },
            ),
        ]
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: _completed(returncode=0, stdout=json.dumps({"items": deployments})),
        )

        assert provider.find_deployments_using_secret("default", "db-creds") == ["api"]

    def test_no_matches_for_unrelated_secret(self, provider, monkeypatch):
        deployments = [
            self._deployment(
                "api",
                {"containers": [{"name": "api", "envFrom": [{"secretRef": {"name": "other-secret"}}]}]},
            ),
        ]
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: _completed(returncode=0, stdout=json.dumps({"items": deployments})),
        )

        assert provider.find_deployments_using_secret("default", "db-creds") == []
