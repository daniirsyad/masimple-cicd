"""Helm chart manifests end to end inside the app: the form/routes, and the
deploy worker's Helm paths (deploy, stop, restart, live status). Helm itself
is faked at the KubernetesProvider method level; tests/test_helm.py covers
the commands those methods run."""
import pytest

from app.blueprints.deployment_manifests import routes as manifest_routes
from app.extensions import db
from app.models import DeploymentExecution, DeploymentManifest, DeploymentServer
from app.services.deployment.base import DeployResult
from app.services.deployment.helm import HelmError, decode_credentials
from app.services.deployment.kubernetes_provider import KubernetesProvider
from app.services.deployment.worker import _refresh_live_status, _run_deployment, enqueue_deployment_run
from app.utils.crypto import encrypt

MANAGE = ("deployment_manifest.view", "deployment_manifest.manage")


@pytest.fixture
def servers(app):
    with app.app_context():
        kube = DeploymentServer(name="k8s", connection_type="kube", encrypted_credentials=encrypt("fake-kubeconfig"))
        agent = DeploymentServer(
            name="agent", connection_type="api", encrypted_credentials=encrypt('{"api_url": "https://agent.example.com"}')
        )
        db.session.add_all([kube, agent])
        db.session.commit()
        return {"kube": kube.id, "api": agent.id}


def _helm_manifest(app, server_id, **overrides):
    with app.app_context():
        fields = dict(
            name="web", yaml_content="replicaCount: 2\n", source_type="helm",
            helm_chart_repo="https://charts.example.com", helm_chart_name="web", helm_chart_version="1.2.3",
            helm_release_name="web", helm_namespace="apps",
        )
        fields.update(overrides)
        manifest = DeploymentManifest(**fields)
        manifest.target_servers = [DeploymentServer.query.get(server_id)]
        db.session.add(manifest)
        db.session.commit()
        return manifest.id


def _form(prefix, server_id, **overrides):
    data = {
        f"{prefix}name": "web",
        f"{prefix}source_type": "helm",
        f"{prefix}yaml_content": "",
        f"{prefix}helm_chart_repo": "oci://ghcr.io/org/charts",
        f"{prefix}helm_chart_name": "web",
        f"{prefix}helm_chart_version": "1.2.3",
        f"{prefix}helm_release_name": "web",
        f"{prefix}helm_namespace": "apps",
        f"{prefix}server_ids": [str(server_id)],
    }
    data.update({f"{prefix}{key}": value for key, value in overrides.items()})
    return data


class TestHelmForm:
    def test_creates_helm_manifest_with_empty_values_and_encrypted_login(self, perm_client, app, servers):
        response = perm_client(*MANAGE).post(
            "/deployment-manifests/create",
            data=_form("create-manifest-", servers["kube"], helm_repo_username="bot", helm_repo_password="tok"),
            follow_redirects=True,
        )
        assert "Manifest &#39;web&#39; created." in response.get_data(as_text=True)
        with app.app_context():
            manifest = DeploymentManifest.query.filter_by(name="web").one()
            assert manifest.is_helm and manifest.helm_chart_repo == "oci://ghcr.io/org/charts"
            assert manifest.yaml_content == ""
            assert "tok" not in manifest.encrypted_helm_repo_credentials
            assert decode_credentials(manifest.encrypted_helm_repo_credentials) == {"username": "bot", "password": "tok"}

    def test_missing_chart_fields_and_bad_release_name_are_rejected(self, perm_client, app, servers):
        response = perm_client(*MANAGE).post(
            "/deployment-manifests/create",
            data=_form("create-manifest-", servers["kube"], helm_chart_repo="charts.example.com",
                       helm_chart_version="", helm_release_name="Web_App"),
        )
        html = response.get_data(as_text=True)
        assert "Enter the chart repository" in html and "Enter the chart version" in html
        assert "Use lowercase letters" in html
        with app.app_context():
            assert DeploymentManifest.query.count() == 0

    def test_helm_manifest_cannot_target_a_custom_agent_server(self, perm_client, app, servers):
        response = perm_client(*MANAGE).post("/deployment-manifests/create", data=_form("create-manifest-", servers["api"]))
        assert "Helm charts need a Kubernetes server: agent is not one." in response.get_data(as_text=True)
        with app.app_context():
            assert DeploymentManifest.query.count() == 0

    def test_yaml_manifest_still_requires_yaml(self, perm_client, app, servers):
        response = perm_client(*MANAGE).post(
            "/deployment-manifests/create",
            data={"create-manifest-name": "plain", "create-manifest-source_type": "yaml",
                  "create-manifest-yaml_content": " ", "create-manifest-server_ids": [str(servers["kube"])]},
        )
        assert "This field is required." in response.get_data(as_text=True)

    def test_blank_password_on_edit_keeps_the_stored_one(self, perm_client, app, servers):
        manifest_id = _helm_manifest(app, servers["kube"], encrypted_helm_repo_credentials=None)
        client = perm_client(*MANAGE)
        prefix = f"manifest-{manifest_id}-"
        client.post(f"/deployment-manifests/{manifest_id}/edit",
                    data=_form(prefix, servers["kube"], helm_repo_username="bot", helm_repo_password="tok"))
        client.post(f"/deployment-manifests/{manifest_id}/edit",
                    data=_form(prefix, servers["kube"], helm_repo_username="bot", helm_repo_password=""))
        with app.app_context():
            stored = DeploymentManifest.query.get(manifest_id).encrypted_helm_repo_credentials
            assert decode_credentials(stored) == {"username": "bot", "password": "tok"}

    def test_switching_back_to_yaml_clears_helm_fields(self, perm_client, app, servers):
        manifest_id = _helm_manifest(app, servers["kube"])
        prefix = f"manifest-{manifest_id}-"
        perm_client(*MANAGE).post(
            f"/deployment-manifests/{manifest_id}/edit",
            data=_form(prefix, servers["kube"], source_type="yaml", yaml_content="kind: ConfigMap"),
        )
        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            assert manifest.source_type == "yaml" and manifest.helm_chart_repo is None

    def test_release_name_cannot_change_while_deployed(self, perm_client, app, servers, monkeypatch):
        manifest_id = _helm_manifest(app, servers["kube"])
        monkeypatch.setattr(manifest_routes, "is_currently_deployed", lambda manifest_id, server_id: True)
        response = perm_client(*MANAGE).post(
            f"/deployment-manifests/{manifest_id}/edit",
            data=_form(f"manifest-{manifest_id}-", servers["kube"], helm_release_name="web-2"),
        )
        assert "Stop this manifest before changing its type, release name or namespace." in response.get_data(as_text=True)
        with app.app_context():
            assert DeploymentManifest.query.get(manifest_id).helm_release_name == "web"

    def test_index_shows_the_chart_and_release(self, perm_client, app, servers):
        _helm_manifest(app, servers["kube"])
        html = perm_client(*MANAGE).get("/deployment-manifests/").get_data(as_text=True)
        assert "web 1.2.3" in html and "Load chart defaults" in html


class TestDefaultValuesApi:
    def test_returns_the_charts_values(self, perm_client, monkeypatch):
        seen = {}

        def _fake_show_values(chart):
            seen["chart"] = chart
            return "replicaCount: 1\n"

        monkeypatch.setattr(manifest_routes, "show_values", _fake_show_values)
        response = perm_client(*MANAGE).post(
            "/deployment-manifests/api/helm-default-values",
            data={"repo": "https://charts.example.com", "name": "web", "version": "1.2.3"},
        )
        assert response.json == {"ok": True, "values": "replicaCount: 1\n"}
        assert seen["chart"].label() == "web@1.2.3"

    def test_uses_the_stored_password_when_left_blank(self, perm_client, app, servers, monkeypatch):
        from app.services.deployment.helm import encode_credentials

        manifest_id = _helm_manifest(app, servers["kube"], encrypted_helm_repo_credentials=encode_credentials("bot", "tok"))
        seen = {}
        monkeypatch.setattr(manifest_routes, "show_values", lambda chart: seen.setdefault("password", chart.password) or "")
        perm_client(*MANAGE).post(
            "/deployment-manifests/api/helm-default-values",
            data={"repo": "oci://ghcr.io/org", "name": "web", "version": "1", "username": "bot", "password": "",
                  "manifest_id": str(manifest_id)},
        )
        assert seen["password"] == "tok"

    def test_helm_error_is_returned_as_json(self, perm_client, monkeypatch):
        def _fail(chart):
            raise HelmError("chart not found")

        monkeypatch.setattr(manifest_routes, "show_values", _fail)
        response = perm_client(*MANAGE).post(
            "/deployment-manifests/api/helm-default-values", data={"repo": "https://x", "name": "web", "version": "9"}
        )
        assert response.status_code == 502 and response.json == {"ok": False, "error": "chart not found"}

    def test_missing_chart_fields_is_a_400(self, perm_client):
        response = perm_client(*MANAGE).post("/deployment-manifests/api/helm-default-values", data={"repo": "https://x"})
        assert response.status_code == 400

    def test_requires_manage_permission(self, perm_client):
        response = perm_client("deployment_manifest.view").post(
            "/deployment-manifests/api/helm-default-values", data={}, headers={"Sec-Fetch-Mode": "cors"}
        )
        assert response.status_code == 403


def _run(app, manifest_id, action="deploy"):
    with app.app_context():
        manifest = DeploymentManifest.query.get(manifest_id)
        run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action=action)
        execution_id = DeploymentExecution.query.filter_by(run_id=run.id).first().id
    _run_deployment(app, execution_id)
    return execution_id


class TestHelmWorker:
    @pytest.fixture
    def helm_calls(self, monkeypatch):
        calls = []

        def _record(name):
            def _method(self, *args, **kwargs):
                calls.append((name, args, kwargs))
                return DeployResult(success=True, log=f"{name} ok")
            return _method

        for name in ("helm_upgrade", "helm_uninstall", "helm_restart"):
            monkeypatch.setattr(KubernetesProvider, name, _record(name))
        monkeypatch.setattr(KubernetesProvider, "apply", lambda *a, **k: pytest.fail("kubectl apply used for a Helm manifest"))
        return calls

    def test_deploy_upgrades_the_release_and_records_it(self, app, servers, helm_calls):
        manifest_id = _helm_manifest(app, servers["kube"], wait_for_ready_timeout_seconds=90)
        execution_id = _run(app, manifest_id)

        name, args, kwargs = helm_calls[0]
        chart, release, namespace, values = args
        assert name == "helm_upgrade" and (release, namespace, values) == ("web", "apps", "replicaCount: 2\n")
        assert chart.label() == "web@1.2.3" and kwargs["wait_timeout_seconds"] == 90
        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            assert execution.status == "success"
            assert (execution.helm_release_name, execution.helm_namespace) == ("web", "apps")
            assert execution.resolved_version_string == "helm chart=web@1.2.3"

    def test_stop_uninstalls_the_release_that_was_installed(self, app, servers, helm_calls):
        manifest_id = _helm_manifest(app, servers["kube"])
        _run(app, manifest_id)
        with app.app_context():
            # Edited after deploying (bypassing the form's guard): stop must still target "web".
            DeploymentManifest.query.get(manifest_id).helm_release_name = "renamed"
            db.session.commit()
        execution_id = _run(app, manifest_id, action="stop")

        assert helm_calls[-1][0:2] == ("helm_uninstall", ("web", "apps"))
        with app.app_context():
            assert DeploymentExecution.query.get(execution_id).status == "success"

    def test_restart_rolls_the_installed_release(self, app, servers, helm_calls):
        manifest_id = _helm_manifest(app, servers["kube"], wait_for_ready_timeout_seconds=30)
        _run(app, manifest_id)
        execution_id = _run(app, manifest_id, action="restart")

        assert helm_calls[-1] == ("helm_restart", ("web", "apps"), {"wait_timeout_seconds": 30})
        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            assert execution.status == "success" and execution.helm_release_name == "web"

    def test_failed_upgrade_fails_the_execution(self, app, servers, monkeypatch):
        monkeypatch.setattr(KubernetesProvider, "helm_upgrade",
                            lambda *a, **k: DeployResult(success=False, log="UPGRADE FAILED", error="helm upgrade exited with code 1"))
        execution_id = _run(app, _helm_manifest(app, servers["kube"]))
        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            assert execution.status == "failed" and "UPGRADE FAILED" in execution.log

    def test_custom_agent_server_fails_clearly(self, app, servers):
        execution_id = _run(app, _helm_manifest(app, servers["api"]))
        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            assert execution.status == "failed" and "Kubernetes (kubeconfig) server" in execution.log

    def test_live_status_asks_helm(self, app, servers, helm_calls, monkeypatch):
        execution_id = _run(app, _helm_manifest(app, servers["kube"]))
        monkeypatch.setattr(KubernetesProvider, "helm_live_status", lambda self, release, namespace: f"live:{release}:{namespace}")
        monkeypatch.setattr(KubernetesProvider, "get_live_status", lambda *a: pytest.fail("kubectl status used for a Helm release"))
        _refresh_live_status(app)
        with app.app_context():
            assert DeploymentExecution.query.get(execution_id).live_status == "live:web:apps"
