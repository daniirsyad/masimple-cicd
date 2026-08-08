import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import DeploymentServer, Permission, Role, User
from app.services.deployment.kubernetes_provider import KubernetesProvider
from app.utils.crypto import encrypt

POD_PASSWORD = "PodPass123!"


@pytest.fixture
def pod_user(app):
    with app.app_context():
        permissions = [Permission(code="deployment_pod.view", description="view pods")]
        db.session.add_all(permissions)
        role = Role(name="PodViewer", description="Test pod viewer role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="pod_test",
            password_hash=generate_password_hash(POD_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return {"user_id": user.id, "role_id": role.id}


@pytest.fixture
def pod_client(client, pod_user):
    client.post("/login", data={"username": "pod_test", "password": POD_PASSWORD}, follow_redirects=True)
    return client


@pytest.fixture
def kube_server(app, pod_user):
    with app.app_context():
        server = DeploymentServer(
            name="srv1", connection_type="kube", encrypted_credentials=encrypt("fake-kubeconfig")
        )
        server.allowed_roles = [Role.query.get(pod_user["role_id"])]
        db.session.add(server)
        db.session.commit()
        return server.id


@pytest.fixture
def inaccessible_kube_server(app):
    """A kube server with no allowed_roles — restricted to
    deployment_server.manage users only, per DeploymentServer.is_accessible_to.
    """
    with app.app_context():
        server = DeploymentServer(name="srv2", connection_type="kube")
        db.session.add(server)
        db.session.commit()
        return server.id


@pytest.fixture
def api_server(app, pod_user):
    with app.app_context():
        server = DeploymentServer(name="srv3", connection_type="api")
        server.allowed_roles = [Role.query.get(pod_user["role_id"])]
        db.session.add(server)
        db.session.commit()
        return server.id


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/deployment-pods/").status_code == 403

    def test_list_pods_requires_permission(self, noperm_client, app):
        with app.app_context():
            server = DeploymentServer(name="srv", connection_type="kube")
            db.session.add(server)
            db.session.commit()
            server_id = server.id
        assert noperm_client.get(f"/deployment-pods/{server_id}").status_code == 403


class TestServerAccessGating:
    def test_inaccessible_server_is_403(self, pod_client, inaccessible_kube_server):
        response = pod_client.get(f"/deployment-pods/{inaccessible_kube_server}")
        assert response.status_code == 403

    def test_api_type_server_is_404(self, pod_client, api_server):
        response = pod_client.get(f"/deployment-pods/{api_server}")
        assert response.status_code == 404

    def test_index_only_lists_accessible_kube_servers(self, pod_client, kube_server, inaccessible_kube_server, api_server):
        response = pod_client.get("/deployment-pods/")
        assert response.status_code == 200
        assert b"srv1" in response.data
        assert b"srv2" not in response.data
        assert b"srv3" not in response.data


class TestListPods:
    def test_lists_pods_from_provider(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "list_pods",
            lambda self, namespace=None: [
                {
                    "name": "app-abc123",
                    "namespace": "default",
                    "phase": "Running",
                    "ready": "1/1",
                    "restarts": 0,
                    "node": "node-1",
                    "created_at": "2026-08-08T00:00:00Z",
                }
            ],
        )
        response = pod_client.get(f"/deployment-pods/{kube_server}")
        assert response.status_code == 200
        assert b"app-abc123" in response.data
        assert b"Running" in response.data

    def test_provider_error_shows_inline_error_not_500(self, pod_client, kube_server, monkeypatch):
        def _raise(self, namespace=None):
            raise RuntimeError("kubectl get pods failed.")

        monkeypatch.setattr(KubernetesProvider, "list_pods", _raise)
        response = pod_client.get(f"/deployment-pods/{kube_server}")
        assert response.status_code == 200
        assert b"Could not list pods" in response.data


class TestPodLogsAndDescribe:
    """Both endpoints return JSON now — fetched by the Logs/Describe modals
    on deployment_pods/list.html rather than navigated to as pages.
    """

    def test_logs_returns_provider_output_as_json(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider, "pod_logs", lambda self, namespace, pod_name, container=None: "hello from the pod\n"
        )
        response = pod_client.get(f"/deployment-pods/{kube_server}/pods/default/app-abc123/logs")
        assert response.status_code == 200
        data = response.get_json()
        assert data["logs"] == "hello from the pod\n"
        assert data["error"] is None

    def test_logs_passes_container_query_param_through(self, pod_client, kube_server, monkeypatch):
        received = {}

        def _fake_logs(self, namespace, pod_name, container=None):
            received["container"] = container
            return "log line\n"

        monkeypatch.setattr(KubernetesProvider, "pod_logs", _fake_logs)
        response = pod_client.get(f"/deployment-pods/{kube_server}/pods/default/app-abc123/logs?container=sidecar")
        assert response.status_code == 200
        assert received["container"] == "sidecar"

    def test_logs_provider_error_returns_json_error_not_500(self, pod_client, kube_server, monkeypatch):
        def _raise(self, namespace, pod_name, container=None):
            raise RuntimeError("kubectl logs failed.")

        monkeypatch.setattr(KubernetesProvider, "pod_logs", _raise)
        response = pod_client.get(f"/deployment-pods/{kube_server}/pods/default/app-abc123/logs")
        assert response.status_code == 200
        data = response.get_json()
        assert data["logs"] is None
        assert "Could not fetch logs" in data["error"]

    def test_describe_returns_provider_output_as_json(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider, "describe_pod", lambda self, namespace, pod_name: "Name: app-abc123\nStatus: Running\n"
        )
        response = pod_client.get(f"/deployment-pods/{kube_server}/pods/default/app-abc123/describe")
        assert response.status_code == 200
        data = response.get_json()
        assert "Status: Running" in data["description"]
        assert data["error"] is None

    def test_describe_provider_error_returns_json_error_not_500(self, pod_client, kube_server, monkeypatch):
        def _raise(self, namespace, pod_name):
            raise RuntimeError("kubectl describe failed.")

        monkeypatch.setattr(KubernetesProvider, "describe_pod", _raise)
        response = pod_client.get(f"/deployment-pods/{kube_server}/pods/default/app-abc123/describe")
        assert response.status_code == 200
        data = response.get_json()
        assert data["description"] is None
        assert "Could not describe pod" in data["error"]


class TestListResources:
    def test_unknown_kind_is_404(self, pod_client, kube_server):
        assert pod_client.get(f"/deployment-pods/{kube_server}/resources/bogus").status_code == 404

    def test_requires_permission(self, noperm_client, app):
        with app.app_context():
            server = DeploymentServer(name="srv", connection_type="kube")
            db.session.add(server)
            db.session.commit()
            server_id = server.id
        assert noperm_client.get(f"/deployment-pods/{server_id}/resources/namespaces").status_code == 403

    def test_lists_namespaces_with_summary(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "list_resources",
            lambda self, kubectl_kind, namespace=None, all_namespaces=False: [
                {"metadata": {"name": "prod", "creationTimestamp": "2026-08-01T00:00:00Z"}, "status": {"phase": "Active"}}
            ],
        )
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/namespaces")
        assert response.status_code == 200
        assert b"prod" in response.data
        assert b"Active" in response.data

    def test_cluster_scoped_kind_ignores_namespace_filter_and_requests_no_namespace_flag(
        self, pod_client, kube_server, monkeypatch
    ):
        received = {}

        def _fake_list(self, kubectl_kind, namespace=None, all_namespaces=False):
            received["kubectl_kind"] = kubectl_kind
            received["namespace"] = namespace
            received["all_namespaces"] = all_namespaces
            return []

        monkeypatch.setattr(KubernetesProvider, "list_resources", _fake_list)
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/nodes?namespace=ignored")
        assert response.status_code == 200
        assert received["kubectl_kind"] == "node"
        assert received["namespace"] is None
        assert received["all_namespaces"] is False

    def test_namespaced_kind_defaults_to_all_namespaces(self, pod_client, kube_server, monkeypatch):
        received = {}

        def _fake_list(self, kubectl_kind, namespace=None, all_namespaces=False):
            received["namespace"] = namespace
            received["all_namespaces"] = all_namespaces
            return []

        monkeypatch.setattr(KubernetesProvider, "list_resources", _fake_list)
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/services")
        assert response.status_code == 200
        assert received["namespace"] is None
        assert received["all_namespaces"] is True

    def test_namespaced_kind_respects_namespace_filter(self, pod_client, kube_server, monkeypatch):
        received = {}

        def _fake_list(self, kubectl_kind, namespace=None, all_namespaces=False):
            received["namespace"] = namespace
            received["all_namespaces"] = all_namespaces
            return []

        monkeypatch.setattr(KubernetesProvider, "list_resources", _fake_list)
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/services?namespace=prod")
        assert response.status_code == 200
        assert received["namespace"] == "prod"
        assert received["all_namespaces"] is False

    def test_provider_error_shows_inline_error_not_500(self, pod_client, kube_server, monkeypatch):
        def _raise(self, kubectl_kind, namespace=None, all_namespaces=False):
            raise RuntimeError("kubectl get failed.")

        monkeypatch.setattr(KubernetesProvider, "list_resources", _raise)
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/services")
        assert response.status_code == 200
        assert b"Could not list" in response.data


class TestDescribeResource:
    def test_unknown_kind_is_404(self, pod_client, kube_server):
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/bogus/describe?name=x")
        assert response.status_code == 404

    def test_missing_name_is_400(self, pod_client, kube_server):
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/namespaces/describe")
        assert response.status_code == 400

    def test_returns_provider_output_as_json(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "describe_resource",
            lambda self, kubectl_kind, name, namespace=None: f"Name: {name}\nStatus: Active\n",
        )
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/namespaces/describe?name=prod")
        assert response.status_code == 200
        data = response.get_json()
        assert "Status: Active" in data["description"]
        assert data["error"] is None

    def test_cluster_scoped_kind_never_passes_namespace_to_provider(self, pod_client, kube_server, monkeypatch):
        received = {}

        def _fake_describe(self, kubectl_kind, name, namespace=None):
            received["namespace"] = namespace
            return "described"

        monkeypatch.setattr(KubernetesProvider, "describe_resource", _fake_describe)
        response = pod_client.get(
            f"/deployment-pods/{kube_server}/resources/namespaces/describe?name=prod&namespace=ignored"
        )
        assert response.status_code == 200
        assert received["namespace"] is None

    def test_namespaced_kind_passes_namespace_to_provider(self, pod_client, kube_server, monkeypatch):
        received = {}

        def _fake_describe(self, kubectl_kind, name, namespace=None):
            received["namespace"] = namespace
            return "described"

        monkeypatch.setattr(KubernetesProvider, "describe_resource", _fake_describe)
        response = pod_client.get(
            f"/deployment-pods/{kube_server}/resources/services/describe?name=web&namespace=prod"
        )
        assert response.status_code == 200
        assert received["namespace"] == "prod"

    def test_provider_error_returns_json_error_not_500(self, pod_client, kube_server, monkeypatch):
        def _raise(self, kubectl_kind, name, namespace=None):
            raise RuntimeError("kubectl describe failed.")

        monkeypatch.setattr(KubernetesProvider, "describe_resource", _raise)
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/namespaces/describe?name=prod")
        assert response.status_code == 200
        data = response.get_json()
        assert data["description"] is None
        assert "Could not describe resource" in data["error"]
