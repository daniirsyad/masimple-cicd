import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import ActivityLog, DeploymentServer, ErrorLog, Permission, Role, User
from app.services.deployment.base import DeployResult
from app.services.deployment.kubernetes_provider import KubernetesProvider
from app.utils.crypto import encrypt

POD_PASSWORD = "PodPass123!"
MANAGE_PASSWORD = "PodManagePass123!"

# Reused across most namespace/secret provider-call fakes below so a test's
# create/edit/delete route can render its post-redirect page (which lists
# namespaces for the create-secret dropdown, and secrets for the list table)
# without ever actually invoking kubectl.
def _one_namespace(self, kubectl_kind, namespace=None, all_namespaces=False):
    return [{"metadata": {"name": "default"}}]


def _no_secrets(self, namespace=None):
    return []


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


@pytest.fixture
def manage_user(app):
    """A user whose role grants deployment_pod.view plus both new .manage
    permissions — the positive-path counterpart to pod_user, which only has
    .view and is used for the 403 negative-path tests below.
    """
    with app.app_context():
        permissions = [
            Permission(code="deployment_pod.view", description="view pods"),
            Permission(code="deployment_namespace.manage", description="manage namespaces"),
            Permission(code="deployment_secret.manage", description="manage secrets"),
            Permission(code="deployment_configmap.manage", description="manage configmaps"),
            Permission(code="deployment_workload.restart", description="restart workloads"),
        ]
        db.session.add_all(permissions)
        role = Role(name="PodManager", description="Test pod manager role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="pod_manage_test",
            password_hash=generate_password_hash(MANAGE_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return {"user_id": user.id, "role_id": role.id}


@pytest.fixture
def manage_client(client, manage_user):
    client.post("/login", data={"username": "pod_manage_test", "password": MANAGE_PASSWORD}, follow_redirects=True)
    return client


@pytest.fixture
def manage_kube_server(app, manage_user):
    with app.app_context():
        server = DeploymentServer(
            name="srv-manage", connection_type="kube", encrypted_credentials=encrypt("fake-kubeconfig")
        )
        server.allowed_roles = [Role.query.get(manage_user["role_id"])]
        db.session.add(server)
        db.session.commit()
        return server.id


@pytest.fixture
def manage_api_server(app, manage_user):
    with app.app_context():
        server = DeploymentServer(name="srv-manage-api", connection_type="api")
        server.allowed_roles = [Role.query.get(manage_user["role_id"])]
        db.session.add(server)
        db.session.commit()
        return server.id


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/deployment-pods/").status_code == 403

    def test_index_redirects_to_deployment_servers(self, pod_client):
        # /deployment-pods no longer has its own server-picker page — that
        # was pure duplication of /deployment-servers, which already links
        # straight into Pods/Namespaces/Secrets/ConfigMaps per kube-type row.
        response = pod_client.get("/deployment-pods/")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/deployment-servers/")

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

    def test_logs_provider_error_returns_json_error_not_500(self, pod_client, kube_server, monkeypatch, app):
        def _raise(self, namespace, pod_name, container=None):
            raise RuntimeError("kubectl logs failed.")

        monkeypatch.setattr(KubernetesProvider, "pod_logs", _raise)
        response = pod_client.get(f"/deployment-pods/{kube_server}/pods/default/app-abc123/logs")
        assert response.status_code == 200
        data = response.get_json()
        assert data["logs"] is None
        assert "Could not fetch logs" in data["error"]

        with app.app_context():
            error_log = ErrorLog.query.filter_by(source="deployment_pods.pod_logs").first()
            assert error_log is not None
            assert data["error_log_url"] == f"/logs/errors/{error_log.id}"

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

    def test_namespaces_is_no_longer_a_generic_resource_kind(self, pod_client, kube_server):
        # Namespaces moved to their own dedicated CRUD page/routes (see
        # TestNamespaceList etc. below) — the generic read-only registry no
        # longer knows about them.
        assert pod_client.get(f"/deployment-pods/{kube_server}/resources/namespaces").status_code == 404

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
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/nodes/describe")
        assert response.status_code == 400

    def test_returns_provider_output_as_json(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "describe_resource",
            lambda self, kubectl_kind, name, namespace=None: f"Name: {name}\nStatus: Ready\n",
        )
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/nodes/describe?name=node-1")
        assert response.status_code == 200
        data = response.get_json()
        assert "Status: Ready" in data["description"]
        assert data["error"] is None

    def test_cluster_scoped_kind_never_passes_namespace_to_provider(self, pod_client, kube_server, monkeypatch):
        received = {}

        def _fake_describe(self, kubectl_kind, name, namespace=None):
            received["namespace"] = namespace
            return "described"

        monkeypatch.setattr(KubernetesProvider, "describe_resource", _fake_describe)
        response = pod_client.get(
            f"/deployment-pods/{kube_server}/resources/nodes/describe?name=node-1&namespace=ignored"
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
        response = pod_client.get(f"/deployment-pods/{kube_server}/resources/nodes/describe?name=node-1")
        assert response.status_code == 200
        data = response.get_json()
        assert data["description"] is None
        assert "Could not describe resource" in data["error"]


class TestNamespaceList:
    def test_lists_namespaces(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "list_resources",
            lambda self, kubectl_kind, namespace=None, all_namespaces=False: [
                {"metadata": {"name": "prod", "creationTimestamp": "2026-08-01T00:00:00Z"}, "status": {"phase": "Active"}}
            ],
        )
        response = pod_client.get(f"/deployment-pods/{kube_server}/namespaces")
        assert response.status_code == 200
        assert b"prod" in response.data
        assert b"Active" in response.data

    def test_requires_permission(self, noperm_client, app):
        with app.app_context():
            server = DeploymentServer(name="srv", connection_type="kube")
            db.session.add(server)
            db.session.commit()
            server_id = server.id
        assert noperm_client.get(f"/deployment-pods/{server_id}/namespaces").status_code == 403

    def test_api_type_server_is_404(self, pod_client, api_server):
        assert pod_client.get(f"/deployment-pods/{api_server}/namespaces").status_code == 404

    def test_provider_error_shows_inline_error_not_500(self, pod_client, kube_server, monkeypatch):
        def _raise(self, kubectl_kind, namespace=None, all_namespaces=False):
            raise RuntimeError("kubectl get failed.")

        monkeypatch.setattr(KubernetesProvider, "list_resources", _raise)
        response = pod_client.get(f"/deployment-pods/{kube_server}/namespaces")
        assert response.status_code == 200
        assert b"Could not list namespaces" in response.data

    def test_nav_still_shows_the_other_resource_kind_tabs(self, pod_client, kube_server, monkeypatch):
        # Regression test: _render_namespaces/_render_secrets must pass
        # resource_kinds through to the template, same as list_pods/
        # list_resources — otherwise _nav.html's loop over RESOURCE_KINDS
        # silently renders nothing and Nodes/Services/Ingress/PVs/PVCs
        # disappear from the tab bar on these two pages.
        monkeypatch.setattr(
            KubernetesProvider, "list_resources", lambda self, kubectl_kind, namespace=None, all_namespaces=False: []
        )
        response = pod_client.get(f"/deployment-pods/{kube_server}/namespaces")
        assert response.status_code == 200
        for label in (b"Nodes", b"Services", b"Ingress", b"Persistent Volumes", b"Persistent Volume Claims"):
            assert label in response.data


class TestNamespaceWritePermissionGating:
    """deployment_pod.view alone (pod_client) is not enough to write —
    deployment_namespace.manage is required for create/edit/delete.
    """

    def test_create_requires_manage_permission(self, pod_client, kube_server):
        response = pod_client.post(
            f"/deployment-pods/{kube_server}/namespaces/create", data={"create-namespace-name": "test-ns"}
        )
        assert response.status_code == 403

    def test_edit_requires_manage_permission(self, pod_client, kube_server):
        response = pod_client.post(f"/deployment-pods/{kube_server}/namespaces/test-ns/edit", data={})
        assert response.status_code == 403

    def test_delete_requires_manage_permission(self, pod_client, kube_server):
        response = pod_client.post(f"/deployment-pods/{kube_server}/namespaces/test-ns/delete", data={})
        assert response.status_code == 403


class TestCreateNamespace:
    def test_creates_namespace_and_logs_activity(self, manage_client, manage_kube_server, monkeypatch, app):
        called = {}

        def _fake_create(self, name, labels=None):
            called["name"] = name
            called["labels"] = labels
            return DeployResult(success=True, log="namespace/test-ns created")

        monkeypatch.setattr(KubernetesProvider, "create_namespace", _fake_create)
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/namespaces/create",
            data={"create-namespace-name": "test-ns", "create-namespace-labels": "team=platform\nenv=prod"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert called["name"] == "test-ns"
        assert called["labels"] == {"team": "platform", "env": "prod"}

        with app.app_context():
            entry = ActivityLog.query.filter_by(action="CREATE_NAMESPACE").first()
            assert entry is not None
            assert "test-ns" in entry.description

    def test_invalid_name_is_rejected_without_calling_provider(self, manage_client, manage_kube_server, monkeypatch):
        def _fail_if_called(self, name, labels=None):
            raise AssertionError("create_namespace should not be called for an invalid name")

        monkeypatch.setattr(KubernetesProvider, "create_namespace", _fail_if_called)
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/namespaces/create",
            data={"create-namespace-name": "Invalid_Name!"},
        )
        assert response.status_code == 200

    def test_api_type_server_is_404(self, manage_client, manage_api_server):
        response = manage_client.post(
            f"/deployment-pods/{manage_api_server}/namespaces/create", data={"create-namespace-name": "test-ns"}
        )
        assert response.status_code == 404


class TestEditNamespace:
    def test_updates_labels_and_logs_activity(self, manage_client, manage_kube_server, monkeypatch, app):
        called = {}

        def _fake_create(self, name, labels=None):
            called["name"] = name
            called["labels"] = labels
            return DeployResult(success=True, log="namespace/test-ns configured")

        monkeypatch.setattr(KubernetesProvider, "create_namespace", _fake_create)
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/namespaces/test-ns/edit",
            data={"namespace-test-ns-labels": "team=platform"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert called["name"] == "test-ns"
        assert called["labels"] == {"team": "platform"}

        with app.app_context():
            entry = ActivityLog.query.filter_by(action="UPDATE_NAMESPACE").first()
            assert entry is not None


class TestDeleteNamespace:
    def test_deletes_and_logs_activity(self, manage_client, manage_kube_server, monkeypatch, app):
        called = {}

        def _fake_delete(self, name):
            called["name"] = name
            return DeployResult(success=True, log="namespace/test-ns deleted")

        monkeypatch.setattr(KubernetesProvider, "delete_namespace", _fake_delete)
        monkeypatch.setattr(KubernetesProvider, "list_resources", lambda self, kubectl_kind, namespace=None, all_namespaces=False: [])

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/namespaces/test-ns/delete", follow_redirects=True
        )
        assert response.status_code == 200
        assert called["name"] == "test-ns"

        with app.app_context():
            entry = ActivityLog.query.filter_by(action="DELETE_NAMESPACE").first()
            assert entry is not None

    def test_kubectl_failure_shows_flash_not_500(self, manage_client, manage_kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "delete_namespace",
            lambda self, name: DeployResult(success=False, log="", error="namespaces \"test-ns\" is forbidden"),
        )
        monkeypatch.setattr(KubernetesProvider, "list_resources", lambda self, kubectl_kind, namespace=None, all_namespaces=False: [])

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/namespaces/test-ns/delete", follow_redirects=True
        )
        assert response.status_code == 200
        assert b"Could not delete namespace" in response.data


class TestListSecrets:
    def test_lists_secrets_by_key_name_only(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "list_secrets",
            lambda self, namespace=None: [
                {
                    "name": "db-creds",
                    "namespace": "default",
                    "type": "Opaque",
                    "keys": ["username", "password"],
                    "created_at": "2026-08-01T00:00:00Z",
                }
            ],
        )
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)

        response = pod_client.get(f"/deployment-pods/{kube_server}/secrets")
        assert response.status_code == 200
        assert b"db-creds" in response.data
        assert b"username" in response.data
        assert b"password" in response.data

    def test_pull_secret_shows_no_key_names(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "list_secrets",
            lambda self, namespace=None: [
                {
                    "name": "regcred",
                    "namespace": "default",
                    "type": "kubernetes.io/dockerconfigjson",
                    "keys": [".dockerconfigjson"],
                    "created_at": None,
                }
            ],
        )
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)

        response = pod_client.get(f"/deployment-pods/{kube_server}/secrets")
        assert response.status_code == 200
        assert b"regcred" in response.data
        assert b"Image Pull Secret" in response.data

    def test_api_type_server_is_404(self, pod_client, api_server):
        assert pod_client.get(f"/deployment-pods/{api_server}/secrets").status_code == 404

    def test_nav_still_shows_the_other_resource_kind_tabs(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(KubernetesProvider, "list_secrets", _no_secrets)
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)
        response = pod_client.get(f"/deployment-pods/{kube_server}/secrets")
        assert response.status_code == 200
        for label in (b"Nodes", b"Services", b"Ingress", b"Persistent Volumes", b"Persistent Volume Claims"):
            assert label in response.data


class TestSecretWritePermissionGating:
    def test_create_requires_manage_permission(self, pod_client, kube_server):
        response = pod_client.post(
            f"/deployment-pods/{kube_server}/secrets/create", data={"secret_kind": "opaque"}
        )
        assert response.status_code == 403

    def test_edit_requires_manage_permission(self, pod_client, kube_server):
        response = pod_client.post(
            f"/deployment-pods/{kube_server}/secrets/default/db-creds/edit", data={"secret_kind": "opaque"}
        )
        assert response.status_code == 403

    def test_delete_requires_manage_permission(self, pod_client, kube_server):
        response = pod_client.post(f"/deployment-pods/{kube_server}/secrets/default/db-creds/delete", data={})
        assert response.status_code == 403


class TestCreateSecret:
    def test_creates_opaque_secret_and_logs_key_names_only(self, manage_client, manage_kube_server, monkeypatch, app):
        called = {}

        def _fake_create(self, namespace, name, data):
            called.update(namespace=namespace, name=name, data=data)
            return DeployResult(success=True, log="secret/db-creds created")

        monkeypatch.setattr(KubernetesProvider, "create_secret", _fake_create)
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)
        monkeypatch.setattr(KubernetesProvider, "list_secrets", _no_secrets)

        secret_value = "SuperSecretPlaintextValue123"
        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/secrets/create",
            data={
                "secret_kind": "opaque",
                "create-secret-opaque-name": "db-creds",
                "create-secret-opaque-namespace": "default",
                "create-secret-opaque-entries-0-key": "password",
                "create-secret-opaque-entries-0-value": secret_value,
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert called["name"] == "db-creds"
        assert called["namespace"] == "default"
        assert called["data"] == {"password": secret_value}

        with app.app_context():
            entry = ActivityLog.query.filter_by(action="CREATE_SECRET").first()
            assert entry is not None
            assert "password" in entry.description
            assert secret_value not in entry.description

    def test_creates_image_pull_secret(self, manage_client, manage_kube_server, monkeypatch, app):
        called = {}

        def _fake_create(self, namespace, name, registry_server, username, password, email=None):
            called.update(
                namespace=namespace, name=name, registry_server=registry_server, username=username,
                password=password, email=email,
            )
            return DeployResult(success=True, log="secret/regcred created")

        monkeypatch.setattr(KubernetesProvider, "create_image_pull_secret", _fake_create)
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)
        monkeypatch.setattr(KubernetesProvider, "list_secrets", _no_secrets)

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/secrets/create",
            data={
                "secret_kind": "pull",
                "create-secret-pull-name": "regcred",
                "create-secret-pull-namespace": "default",
                "create-secret-pull-registry_server": "https://index.docker.io/v1/",
                "create-secret-pull-username": "myuser",
                "create-secret-pull-password": "mypassword",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert called["name"] == "regcred"
        assert called["registry_server"] == "https://index.docker.io/v1/"
        assert called["password"] == "mypassword"

        with app.app_context():
            entry = ActivityLog.query.filter_by(action="CREATE_SECRET").first()
            assert entry is not None
            assert "mypassword" not in entry.description

    def test_opaque_secret_requires_at_least_one_key(self, manage_client, manage_kube_server, monkeypatch):
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)
        monkeypatch.setattr(KubernetesProvider, "list_secrets", _no_secrets)

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/secrets/create",
            data={
                "secret_kind": "opaque",
                "create-secret-opaque-name": "empty-secret",
                "create-secret-opaque-namespace": "default",
                "create-secret-opaque-entries-0-key": "",
                "create-secret-opaque-entries-0-value": "",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"At least one key is required" in response.data

    def test_api_type_server_is_404(self, manage_client, manage_api_server):
        response = manage_client.post(
            f"/deployment-pods/{manage_api_server}/secrets/create", data={"secret_kind": "opaque"}
        )
        assert response.status_code == 404


class TestEditSecret:
    def test_blank_existing_value_keeps_it_and_new_entries_are_added(
        self, manage_client, manage_kube_server, monkeypatch, app
    ):
        called = {}

        def _fake_update(self, namespace, name, data_updates, removed_keys=None):
            called.update(namespace=namespace, name=name, data_updates=data_updates, removed_keys=removed_keys)
            return DeployResult(success=True, log="secret/db-creds configured")

        monkeypatch.setattr(KubernetesProvider, "update_secret", _fake_update)
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)
        monkeypatch.setattr(
            KubernetesProvider,
            "list_secrets",
            lambda self, namespace=None: [
                {
                    "name": "db-creds", "namespace": "default", "type": "Opaque",
                    "keys": ["username", "password"], "created_at": None,
                }
            ],
        )

        prefix = "secret-default-db-creds-"
        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/secrets/default/db-creds/edit",
            data={
                "secret_kind": "opaque",
                f"{prefix}existing_entries-0-key": "username",
                f"{prefix}existing_entries-0-value": "newadmin",
                f"{prefix}existing_entries-1-key": "password",
                f"{prefix}existing_entries-1-value": "",
                f"{prefix}new_entries-0-key": "api_token",
                f"{prefix}new_entries-0-value": "abc123",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert called["namespace"] == "default"
        assert called["name"] == "db-creds"
        assert called["data_updates"] == {"username": "newadmin", "api_token": "abc123"}
        assert called["removed_keys"] == []

        with app.app_context():
            entry = ActivityLog.query.filter_by(action="UPDATE_SECRET").first()
            assert entry is not None
            assert "newadmin" not in entry.description
            assert "abc123" not in entry.description

    def test_checked_remove_drops_the_key(self, manage_client, manage_kube_server, monkeypatch):
        called = {}

        def _fake_update(self, namespace, name, data_updates, removed_keys=None):
            called["removed_keys"] = removed_keys
            return DeployResult(success=True, log="")

        monkeypatch.setattr(KubernetesProvider, "update_secret", _fake_update)
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)
        monkeypatch.setattr(
            KubernetesProvider,
            "list_secrets",
            lambda self, namespace=None: [
                {"name": "db-creds", "namespace": "default", "type": "Opaque", "keys": ["username"], "created_at": None}
            ],
        )

        prefix = "secret-default-db-creds-"
        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/secrets/default/db-creds/edit",
            data={
                "secret_kind": "opaque",
                f"{prefix}existing_entries-0-key": "username",
                f"{prefix}existing_entries-0-value": "",
                f"{prefix}existing_entries-0-remove": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert called["removed_keys"] == ["username"]

    def test_edits_image_pull_secret_by_full_replace(self, manage_client, manage_kube_server, monkeypatch):
        called = {}

        def _fake_create(self, namespace, name, registry_server, username, password, email=None):
            called.update(registry_server=registry_server, username=username, password=password)
            return DeployResult(success=True, log="")

        monkeypatch.setattr(KubernetesProvider, "create_image_pull_secret", _fake_create)
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)
        monkeypatch.setattr(
            KubernetesProvider,
            "list_secrets",
            lambda self, namespace=None: [
                {
                    "name": "regcred", "namespace": "default", "type": "kubernetes.io/dockerconfigjson",
                    "keys": [".dockerconfigjson"], "created_at": None,
                }
            ],
        )

        prefix = "secret-default-regcred-"
        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/secrets/default/regcred/edit",
            data={
                "secret_kind": "pull",
                f"{prefix}registry_server": "https://index.docker.io/v1/",
                f"{prefix}username": "newuser",
                f"{prefix}password": "newpassword",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert called["username"] == "newuser"
        assert called["password"] == "newpassword"


class TestDeleteSecret:
    def test_deletes_and_logs_activity(self, manage_client, manage_kube_server, monkeypatch, app):
        called = {}

        def _fake_delete(self, namespace, name):
            called.update(namespace=namespace, name=name)
            return DeployResult(success=True, log="secret/db-creds deleted")

        monkeypatch.setattr(KubernetesProvider, "delete_secret", _fake_delete)
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)
        monkeypatch.setattr(KubernetesProvider, "list_secrets", _no_secrets)

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/secrets/default/db-creds/delete", follow_redirects=True
        )
        assert response.status_code == 200
        assert called == {"namespace": "default", "name": "db-creds"}

        with app.app_context():
            entry = ActivityLog.query.filter_by(action="DELETE_SECRET").first()
            assert entry is not None


class TestSecretValuesNeverLeakIntoLogs:
    """Values entered in a Secret create/edit form must never reach
    ActivityLog.description or ErrorLog.message/traceback — only key NAMES
    (metadata) and kubectl's own stderr are ever logged, never the plaintext
    payload this app sent to kubectl.
    """

    def test_create_failure_does_not_leak_value_into_error_log(self, manage_client, manage_kube_server, monkeypatch, app):
        secret_value = "AnotherSecretValue456"

        monkeypatch.setattr(
            KubernetesProvider,
            "create_secret",
            lambda self, namespace, name, data: DeployResult(
                success=False, log="secret/db-creds\nerror: some generic apply failure", error="apply failed"
            ),
        )
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)
        monkeypatch.setattr(KubernetesProvider, "list_secrets", _no_secrets)

        manage_client.post(
            f"/deployment-pods/{manage_kube_server}/secrets/create",
            data={
                "secret_kind": "opaque",
                "create-secret-opaque-name": "db-creds",
                "create-secret-opaque-namespace": "default",
                "create-secret-opaque-entries-0-key": "password",
                "create-secret-opaque-entries-0-value": secret_value,
            },
        )

        with app.app_context():
            for entry in ErrorLog.query.all():
                assert secret_value not in (entry.message or "")
                assert secret_value not in (entry.traceback or "")
            for entry in ActivityLog.query.all():
                assert secret_value not in (entry.description or "")

    def test_create_success_does_not_leak_value_into_activity_log(self, manage_client, manage_kube_server, monkeypatch, app):
        secret_value = "YetAnotherSecretValue789"

        monkeypatch.setattr(
            KubernetesProvider, "create_secret", lambda self, namespace, name, data: DeployResult(success=True, log="")
        )
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)
        monkeypatch.setattr(KubernetesProvider, "list_secrets", _no_secrets)

        manage_client.post(
            f"/deployment-pods/{manage_kube_server}/secrets/create",
            data={
                "secret_kind": "opaque",
                "create-secret-opaque-name": "db-creds",
                "create-secret-opaque-namespace": "default",
                "create-secret-opaque-entries-0-key": "password",
                "create-secret-opaque-entries-0-value": secret_value,
            },
        )

        with app.app_context():
            for entry in ActivityLog.query.all():
                assert secret_value not in (entry.description or "")


class TestConfigMapList:
    def test_lists_configmaps(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "list_configmaps",
            lambda self, namespace=None: [
                {
                    "name": "app-config", "namespace": "default",
                    "data": {"LOG_LEVEL": "debug"}, "created_at": "2026-08-01T00:00:00Z",
                }
            ],
        )
        response = pod_client.get(f"/deployment-pods/{kube_server}/configmaps")
        assert response.status_code == 200
        assert b"app-config" in response.data
        assert b"LOG_LEVEL" in response.data

    def test_requires_permission(self, noperm_client, app):
        with app.app_context():
            server = DeploymentServer(name="srv", connection_type="kube")
            db.session.add(server)
            db.session.commit()
            server_id = server.id
        assert noperm_client.get(f"/deployment-pods/{server_id}/configmaps").status_code == 403

    def test_api_type_server_is_404(self, pod_client, api_server):
        assert pod_client.get(f"/deployment-pods/{api_server}/configmaps").status_code == 404

    def test_provider_error_shows_inline_error_not_500(self, pod_client, kube_server, monkeypatch):
        def _raise(self, namespace=None):
            raise RuntimeError("kubectl get failed.")

        monkeypatch.setattr(KubernetesProvider, "list_configmaps", _raise)
        response = pod_client.get(f"/deployment-pods/{kube_server}/configmaps")
        assert response.status_code == 200
        assert b"Could not list configmaps" in response.data

    def test_nav_still_shows_the_other_resource_kind_tabs(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(KubernetesProvider, "list_configmaps", lambda self, namespace=None: [])
        response = pod_client.get(f"/deployment-pods/{kube_server}/configmaps")
        assert response.status_code == 200
        for label in (b"Nodes", b"Services", b"Ingress", b"Persistent Volumes", b"Persistent Volume Claims"):
            assert label in response.data


class TestConfigMapWritePermissionGating:
    def test_create_requires_manage_permission(self, pod_client, kube_server):
        response = pod_client.post(
            f"/deployment-pods/{kube_server}/configmaps/create", data={"create-configmap-name": "app-config"}
        )
        assert response.status_code == 403

    def test_edit_requires_manage_permission(self, pod_client, kube_server):
        response = pod_client.post(f"/deployment-pods/{kube_server}/configmaps/default/app-config/edit", data={})
        assert response.status_code == 403

    def test_delete_requires_manage_permission(self, pod_client, kube_server):
        response = pod_client.post(f"/deployment-pods/{kube_server}/configmaps/default/app-config/delete", data={})
        assert response.status_code == 403


class TestCreateConfigMap:
    def test_creates_configmap_and_logs_activity(self, manage_client, manage_kube_server, monkeypatch, app):
        called = {}

        def _fake_create(self, namespace, name, data):
            called.update(namespace=namespace, name=name, data=data)
            return DeployResult(success=True, log="configmap/app-config created")

        monkeypatch.setattr(KubernetesProvider, "create_configmap", _fake_create)
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/configmaps/create",
            data={
                "create-configmap-name": "app-config",
                "create-configmap-namespace": "default",
                "create-configmap-entries-0-key": "LOG_LEVEL",
                "create-configmap-entries-0-value": "debug",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert called["name"] == "app-config"
        assert called["namespace"] == "default"
        assert called["data"] == {"LOG_LEVEL": "debug"}

        with app.app_context():
            entry = ActivityLog.query.filter_by(action="CREATE_CONFIGMAP").first()
            assert entry is not None
            assert "LOG_LEVEL" in entry.description

    def test_requires_at_least_one_key(self, manage_client, manage_kube_server, monkeypatch):
        monkeypatch.setattr(KubernetesProvider, "list_resources", _one_namespace)
        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/configmaps/create",
            data={
                "create-configmap-name": "empty-config",
                "create-configmap-namespace": "default",
                "create-configmap-entries-0-key": "",
                "create-configmap-entries-0-value": "",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"At least one key is required" in response.data

    def test_api_type_server_is_404(self, manage_client, manage_api_server):
        response = manage_client.post(
            f"/deployment-pods/{manage_api_server}/configmaps/create", data={"create-configmap-name": "app-config"}
        )
        assert response.status_code == 404


class TestEditConfigMap:
    def test_full_replace_reflects_added_and_removed_keys(self, manage_client, manage_kube_server, monkeypatch, app):
        called = {}

        def _fake_create(self, namespace, name, data):
            called.update(namespace=namespace, name=name, data=data)
            return DeployResult(success=True, log="configmap/app-config configured")

        monkeypatch.setattr(KubernetesProvider, "create_configmap", _fake_create)
        monkeypatch.setattr(
            KubernetesProvider,
            "list_configmaps",
            lambda self, namespace=None: [
                {"name": "app-config", "namespace": "default", "data": {"LOG_LEVEL": "info", "OLD_KEY": "x"}, "created_at": None}
            ],
        )

        prefix = "configmap-default-app-config-"
        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/configmaps/default/app-config/edit",
            data={
                # Editing LOG_LEVEL's value, dropping OLD_KEY entirely (as a
                # client-side row removal would before submit), and adding
                # a brand-new key — all via one full-replace submission.
                f"{prefix}entries-0-key": "LOG_LEVEL",
                f"{prefix}entries-0-value": "debug",
                f"{prefix}entries-1-key": "NEW_KEY",
                f"{prefix}entries-1-value": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert called["namespace"] == "default"
        assert called["name"] == "app-config"
        assert called["data"] == {"LOG_LEVEL": "debug", "NEW_KEY": "y"}

        with app.app_context():
            entry = ActivityLog.query.filter_by(action="UPDATE_CONFIGMAP").first()
            assert entry is not None

    def test_requires_at_least_one_key(self, manage_client, manage_kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "list_configmaps",
            lambda self, namespace=None: [
                {"name": "app-config", "namespace": "default", "data": {"LOG_LEVEL": "info"}, "created_at": None}
            ],
        )
        prefix = "configmap-default-app-config-"
        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/configmaps/default/app-config/edit",
            data={f"{prefix}entries-0-key": "", f"{prefix}entries-0-value": ""},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"At least one key is required" in response.data


class TestDeleteConfigMap:
    def test_deletes_and_logs_activity(self, manage_client, manage_kube_server, monkeypatch, app):
        called = {}

        def _fake_delete(self, namespace, name):
            called.update(namespace=namespace, name=name)
            return DeployResult(success=True, log="configmap/app-config deleted")

        monkeypatch.setattr(KubernetesProvider, "delete_configmap", _fake_delete)
        monkeypatch.setattr(KubernetesProvider, "list_configmaps", lambda self, namespace=None: [])

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/configmaps/default/app-config/delete", follow_redirects=True
        )
        assert response.status_code == 200
        assert called == {"namespace": "default", "name": "app-config"}

        with app.app_context():
            entry = ActivityLog.query.filter_by(action="DELETE_CONFIGMAP").first()
            assert entry is not None

    def test_kubectl_failure_shows_flash_not_500(self, manage_client, manage_kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "delete_configmap",
            lambda self, namespace, name: DeployResult(success=False, log="", error="configmaps \"app-config\" is forbidden"),
        )
        monkeypatch.setattr(KubernetesProvider, "list_configmaps", lambda self, namespace=None: [])

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/configmaps/default/app-config/delete", follow_redirects=True
        )
        assert response.status_code == 200
        assert b"Could not delete configmap" in response.data


class TestWorkloadList:
    def test_lists_workloads_with_ready_and_image_summary(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "list_resources",
            lambda self, kubectl_kind, namespace=None, all_namespaces=False: [
                {
                    "metadata": {"name": "web", "namespace": "default", "creationTimestamp": "2026-08-01T00:00:00Z"},
                    "spec": {"replicas": 3, "template": {"spec": {"containers": [{"image": "nginx:1.25"}]}}},
                    "status": {"readyReplicas": 2},
                }
            ],
        )
        response = pod_client.get(f"/deployment-pods/{kube_server}/workloads")
        assert response.status_code == 200
        assert b"web" in response.data
        assert b"2/3" in response.data
        assert b"nginx:1.25" in response.data

    def test_requires_permission(self, noperm_client, app):
        with app.app_context():
            server = DeploymentServer(name="srv", connection_type="kube")
            db.session.add(server)
            db.session.commit()
            server_id = server.id
        assert noperm_client.get(f"/deployment-pods/{server_id}/workloads").status_code == 403

    def test_api_type_server_is_404(self, pod_client, api_server):
        assert pod_client.get(f"/deployment-pods/{api_server}/workloads").status_code == 404

    def test_provider_error_shows_inline_error_not_500(self, pod_client, kube_server, monkeypatch):
        def _raise(self, kubectl_kind, namespace=None, all_namespaces=False):
            raise RuntimeError("kubectl get failed.")

        monkeypatch.setattr(KubernetesProvider, "list_resources", _raise)
        response = pod_client.get(f"/deployment-pods/{kube_server}/workloads")
        assert response.status_code == 200
        assert b"Could not list workloads" in response.data

    def test_nav_still_shows_the_other_resource_kind_tabs(self, pod_client, kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider, "list_resources", lambda self, kubectl_kind, namespace=None, all_namespaces=False: []
        )
        response = pod_client.get(f"/deployment-pods/{kube_server}/workloads")
        assert response.status_code == 200
        for label in (b"Nodes", b"Services", b"Ingress", b"Persistent Volumes", b"Persistent Volume Claims"):
            assert label in response.data


class TestRestartWorkload:
    def test_requires_permission(self, pod_client, kube_server):
        response = pod_client.post(f"/deployment-pods/{kube_server}/workloads/default/web/restart", data={})
        assert response.status_code == 403

    def test_restarts_and_logs_activity(self, manage_client, manage_kube_server, monkeypatch, app):
        called = {}

        def _fake_restart(self, namespace, name):
            called.update(namespace=namespace, name=name)
            return DeployResult(success=True, log="deployment.apps/web restarted")

        monkeypatch.setattr(KubernetesProvider, "restart_deployment", _fake_restart)
        monkeypatch.setattr(
            KubernetesProvider, "list_resources", lambda self, kubectl_kind, namespace=None, all_namespaces=False: []
        )

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/workloads/default/web/restart", follow_redirects=True
        )
        assert response.status_code == 200
        assert called == {"namespace": "default", "name": "web"}

        with app.app_context():
            entry = ActivityLog.query.filter_by(action="RESTART_WORKLOAD").first()
            assert entry is not None
            assert "web" in entry.description

    def test_kubectl_failure_shows_flash_not_500(self, manage_client, manage_kube_server, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider,
            "restart_deployment",
            lambda self, namespace, name: DeployResult(success=False, log="", error="not found"),
        )
        monkeypatch.setattr(
            KubernetesProvider, "list_resources", lambda self, kubectl_kind, namespace=None, all_namespaces=False: []
        )

        response = manage_client.post(
            f"/deployment-pods/{manage_kube_server}/workloads/default/web/restart", follow_redirects=True
        )
        assert response.status_code == 200
        assert b"Could not restart deployment" in response.data

    def test_api_type_server_is_404(self, manage_client, manage_api_server):
        response = manage_client.post(
            f"/deployment-pods/{manage_api_server}/workloads/default/web/restart", data={}
        )
        assert response.status_code == 404
