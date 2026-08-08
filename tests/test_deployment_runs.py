import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import DeploymentExecution, DeploymentManifest, DeploymentRun, DeploymentServer, Permission, Role, User

RUNS_PASSWORD = "RunsPass123!"


@pytest.fixture
def runs_user(app):
    with app.app_context():
        permission = Permission(code="deployment_run.view", description="view runs")
        db.session.add(permission)
        role = Role(name="RunsViewer", description="Test runs viewer role")
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            username="runs_test", password_hash=generate_password_hash(RUNS_PASSWORD), is_active=True, role_id=role.id
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def runs_client(client, runs_user):
    client.post("/login", data={"username": "runs_test", "password": RUNS_PASSWORD}, follow_redirects=True)
    return client


def _make_run_with_execution(status="success", manifest_name="m1", server_name="srv1"):
    manifest = DeploymentManifest(name=manifest_name, yaml_content="image: nginx")
    server = DeploymentServer(name=server_name, connection_type="kube")
    db.session.add_all([manifest, server])
    db.session.flush()
    run = DeploymentRun(status=status)
    db.session.add(run)
    db.session.flush()
    execution = DeploymentExecution(
        run_id=run.id, manifest_id=manifest.id, server_id=server.id, status=status, log="some log output"
    )
    db.session.add(execution)
    db.session.commit()
    return run, execution, manifest, server


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/deployment-runs/").status_code == 403

    def test_status_requires_permission(self, noperm_client):
        assert noperm_client.get("/deployment-runs/status").status_code == 403


class TestIndex:
    def test_lists_runs(self, runs_client, app):
        with app.app_context():
            _make_run_with_execution()

        response = runs_client.get("/deployment-runs/")
        assert response.status_code == 200
        assert b"m1 (standalone)" in response.data

    def test_standalone_run_with_no_executions_falls_back_to_generic_label(self, runs_client, app):
        with app.app_context():
            run = DeploymentRun(status="queued")
            db.session.add(run)
            db.session.commit()

        response = runs_client.get("/deployment-runs/")
        assert response.status_code == 200
        assert b"Standalone deploy" in response.data

    def test_filters_by_status(self, runs_client, app):
        with app.app_context():
            _make_run_with_execution(status="success", manifest_name="m-success")
            _make_run_with_execution(status="failed", manifest_name="m-failed")

        response = runs_client.get("/deployment-runs/?status=failed")
        assert response.status_code == 200
        assert b"failed" in response.data

    def test_filters_by_manifest(self, runs_client, app):
        with app.app_context():
            run1, _e1, manifest1, _s1 = _make_run_with_execution(manifest_name="m-one")
            run2, _e2, _manifest2, _s2 = _make_run_with_execution(manifest_name="m-two")
            run1_id, run2_id, manifest1_id = run1.id, run2.id, manifest1.id

        response = runs_client.get(f"/deployment-runs/?manifest_id={manifest1_id}")
        assert response.status_code == 200
        body = response.data.decode()
        assert f"/deployment-runs/{run1_id}" in body
        assert f"/deployment-runs/{run2_id}" not in body


class TestDetail:
    def test_shows_executions_and_logs(self, runs_client, app):
        with app.app_context():
            run, _execution, manifest, server = _make_run_with_execution()
            run_id = run.id

        response = runs_client.get(f"/deployment-runs/{run_id}")
        assert response.status_code == 200
        assert b"m1" in response.data
        assert b"srv1" in response.data
        assert b"some log output" in response.data

    def test_standalone_run_title_shows_manifest_name(self, runs_client, app):
        with app.app_context():
            run, _execution, _manifest, _server = _make_run_with_execution(manifest_name="my-app")
            run_id = run.id

        response = runs_client.get(f"/deployment-runs/{run_id}")
        assert response.status_code == 200
        assert b"my-app (standalone)" in response.data

    def test_group_run_title_still_shows_group_name(self, runs_client, app):
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            server = DeploymentServer(name="srv1", connection_type="kube")
            db.session.add_all([manifest, server])
            db.session.flush()
            run = DeploymentRun(status="success", group_name="grp1")
            db.session.add(run)
            db.session.flush()
            db.session.add(DeploymentExecution(run_id=run.id, manifest_id=manifest.id, server_id=server.id, status="success"))
            db.session.commit()
            run_id = run.id

        response = runs_client.get(f"/deployment-runs/{run_id}")
        assert response.status_code == 200
        assert b"grp1" in response.data
        assert b"(standalone)" not in response.data

    def test_404_for_unknown_run(self, runs_client):
        import uuid

        response = runs_client.get(f"/deployment-runs/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_shows_placeholder_when_manifest_was_deleted(self, runs_client, app):
        with app.app_context():
            server = DeploymentServer(name="srv1", connection_type="kube")
            db.session.add(server)
            db.session.flush()
            run = DeploymentRun(status="success")
            db.session.add(run)
            db.session.flush()
            db.session.add(DeploymentExecution(run_id=run.id, manifest_id=None, server_id=server.id, status="success"))
            db.session.commit()
            run_id = run.id

        response = runs_client.get(f"/deployment-runs/{run_id}")
        assert response.status_code == 200
        assert b"(deleted manifest)" in response.data


class TestStatusEndpoint:
    def test_reports_idle_when_nothing_running(self, runs_client):
        response = runs_client.get("/deployment-runs/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["busy"] is False
        assert data["running"] is None
        assert data["queue"] == []

    def test_reports_running_execution_with_progress(self, runs_client, app):
        with app.app_context():
            run, execution, _manifest, _server = _make_run_with_execution(status="running")
            execution.log = "log tail here"
            db.session.commit()

        response = runs_client.get("/deployment-runs/status")
        data = response.get_json()
        assert data["busy"] is True
        assert data["running"]["manifest"] == "m1"
        assert data["running"]["server"] == "srv1"
        assert "log tail here" in data["running"]["log_tail"]
        assert data["running"]["progress"] == {"total": 1, "finished": 0, "succeeded": 0}

    def test_running_execution_with_deleted_manifest_shows_placeholder(self, runs_client, app):
        with app.app_context():
            server = DeploymentServer(name="srv1", connection_type="kube")
            db.session.add(server)
            db.session.flush()
            run = DeploymentRun(status="running")
            db.session.add(run)
            db.session.flush()
            db.session.add(DeploymentExecution(run_id=run.id, manifest_id=None, server_id=server.id, status="running"))
            db.session.commit()

        response = runs_client.get("/deployment-runs/status")
        data = response.get_json()
        assert data["running"]["manifest"] == "(deleted manifest)"
