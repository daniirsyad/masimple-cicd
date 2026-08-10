import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import DeploymentManifest, DeploymentServer, ErrorLog, Permission, Role, User
from app.services.deployment.kubernetes_provider import KubernetesProvider
from app.utils.crypto import decrypt

SERVER_PASSWORD = "ServerPass123!"


@pytest.fixture
def server_user(app):
    with app.app_context():
        permissions = [
            Permission(code="deployment_server.view", description="view"),
            Permission(code="deployment_server.manage", description="manage"),
        ]
        db.session.add_all(permissions)
        role = Role(name="ServerAdmin", description="Test server admin role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="server_test",
            password_hash=generate_password_hash(SERVER_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def server_client(client, server_user):
    client.post("/login", data={"username": "server_test", "password": SERVER_PASSWORD}, follow_redirects=True)
    return client


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/deployment-servers/").status_code == 403

    def test_create_requires_permission(self, noperm_client):
        assert noperm_client.post("/deployment-servers/create", data={}).status_code == 403


class TestCreateServer:
    def test_kube_server_encrypts_the_kubeconfig(self, server_client, app):
        response = server_client.post(
            "/deployment-servers/create",
            data={
                "create-server-name": "cluster-1",
                "create-server-connection_type": "kube",
                "create-server-kubeconfig": "apiVersion: v1\nkind: Config",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            server = DeploymentServer.query.filter_by(name="cluster-1").first()
            assert server is not None
            assert server.connection_type == "kube"
            assert server.status == "unverified"
            assert server.encrypted_credentials != "apiVersion: v1\nkind: Config"
            assert decrypt(server.encrypted_credentials) == "apiVersion: v1\nkind: Config"

    def test_api_server_encrypts_url_and_token_together(self, server_client, app):
        response = server_client.post(
            "/deployment-servers/create",
            data={
                "create-server-name": "agent-1",
                "create-server-connection_type": "api",
                "create-server-api_url": "https://agent.example.com/apply",
                "create-server-api_token": "secret-token",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            server = DeploymentServer.query.filter_by(name="agent-1").first()
            assert server is not None
            decrypted = decrypt(server.encrypted_credentials)
            assert "https://agent.example.com/apply" in decrypted
            assert "secret-token" in decrypted

    def test_missing_credentials_are_rejected(self, server_client, app):
        server_client.post(
            "/deployment-servers/create",
            data={"create-server-name": "no-creds", "create-server-connection_type": "kube"},
        )
        with app.app_context():
            assert DeploymentServer.query.filter_by(name="no-creds").first() is None


class TestEditServer:
    def test_editing_without_new_credentials_keeps_the_existing_ones(self, server_client, app):
        with app.app_context():
            from app.utils.crypto import encrypt

            server = DeploymentServer(name="srv", connection_type="kube", encrypted_credentials=encrypt("original"))
            db.session.add(server)
            db.session.commit()
            server_id = server.id

        server_client.post(
            f"/deployment-servers/{server_id}/edit",
            data={
                f"server-{server_id}-name": "srv",
                f"server-{server_id}-connection_type": "kube",
                f"server-{server_id}-kubeconfig": "",
            },
        )

        with app.app_context():
            server = DeploymentServer.query.get(server_id)
            assert decrypt(server.encrypted_credentials) == "original"

    def test_editing_with_new_credentials_replaces_them_and_resets_status(self, server_client, app):
        with app.app_context():
            from app.utils.crypto import encrypt

            server = DeploymentServer(
                name="srv", connection_type="kube", encrypted_credentials=encrypt("original"), status="healthy"
            )
            db.session.add(server)
            db.session.commit()
            server_id = server.id

        server_client.post(
            f"/deployment-servers/{server_id}/edit",
            data={
                f"server-{server_id}-name": "srv",
                f"server-{server_id}-connection_type": "kube",
                f"server-{server_id}-kubeconfig": "new-config",
            },
        )

        with app.app_context():
            server = DeploymentServer.query.get(server_id)
            assert decrypt(server.encrypted_credentials) == "new-config"
            assert server.status == "unverified"

    def test_the_edit_form_never_shows_stored_secrets(self, server_client, app):
        with app.app_context():
            from app.utils.crypto import encrypt

            server = DeploymentServer(name="srv", connection_type="kube", encrypted_credentials=encrypt("super-secret"))
            db.session.add(server)
            db.session.commit()

        response = server_client.get("/deployment-servers/")
        assert b"super-secret" not in response.data


class TestDeleteServer:
    def test_deletes_when_unreferenced(self, server_client, app):
        with app.app_context():
            server = DeploymentServer(name="srv", connection_type="kube")
            db.session.add(server)
            db.session.commit()
            server_id = server.id

        response = server_client.post(f"/deployment-servers/{server_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert DeploymentServer.query.get(server_id) is None

    def test_blocked_when_a_manifest_still_targets_it(self, server_client, app):
        with app.app_context():
            server = DeploymentServer(name="srv", connection_type="kube")
            db.session.add(server)
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [server]
            db.session.add(manifest)
            db.session.commit()
            server_id = server.id

        response = server_client.post(f"/deployment-servers/{server_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        assert b"still target it" in response.data
        with app.app_context():
            assert DeploymentServer.query.get(server_id) is not None


class TestConnectionTest:
    def test_successful_connection_marks_healthy(self, server_client, app, monkeypatch):
        monkeypatch.setattr(KubernetesProvider, "test_connection", lambda self: True)
        with app.app_context():
            from app.utils.crypto import encrypt

            server = DeploymentServer(name="srv", connection_type="kube", encrypted_credentials=encrypt("config"))
            db.session.add(server)
            db.session.commit()
            server_id = server.id

        response = server_client.post(f"/deployment-servers/{server_id}/test", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            server = DeploymentServer.query.get(server_id)
            assert server.status == "healthy"
            assert server.last_checked_at is not None

    def test_failed_connection_marks_unreachable(self, server_client, app, monkeypatch):
        def _raise(self):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(KubernetesProvider, "test_connection", _raise)
        with app.app_context():
            from app.utils.crypto import encrypt

            server = DeploymentServer(name="srv", connection_type="kube", encrypted_credentials=encrypt("config"))
            db.session.add(server)
            db.session.commit()
            server_id = server.id

        response = server_client.post(f"/deployment-servers/{server_id}/test", follow_redirects=True)
        assert response.status_code == 200
        # The flash banner stays short and links straight to the Error Logs
        # entry — the actual exception text ("connection refused") must not
        # leak into it, only into the ErrorLog entry (checked below).
        assert b"connection refused" not in response.data
        assert b"View error details" in response.data
        with app.app_context():
            server = DeploymentServer.query.get(server_id)
            assert server.status == "unreachable"

            error_log = ErrorLog.query.filter_by(source="deployment_servers.test_connection").first()
            assert error_log is not None
            assert "connection refused" in error_log.message
            assert f"/logs/errors/{error_log.id}".encode() in response.data
            assert "connection refused" in error_log.traceback
