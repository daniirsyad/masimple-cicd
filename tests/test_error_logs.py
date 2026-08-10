import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import ErrorLog, Permission, Role, User

LOGS_PASSWORD = "LogsPass123!"
IMAGE_PASSWORD = "ImagePass123!"


@pytest.fixture
def logs_user(app):
    with app.app_context():
        permission = Permission(code="logs.view", description="View logs")
        db.session.add(permission)
        role = Role(name="LogsViewer", description="Test logs viewer role")
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            username="logs_test",
            password_hash=generate_password_hash(LOGS_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def logs_client(client, logs_user):
    client.post("/login", data={"username": "logs_test", "password": LOGS_PASSWORD}, follow_redirects=True)
    return client


@pytest.fixture
def image_user(app):
    with app.app_context():
        permission = Permission(code="image.view", description="View images")
        db.session.add(permission)
        role = Role(name="ImageViewerForErrorLogs", description="Test image viewer role")
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            username="image_test_errlog",
            password_hash=generate_password_hash(IMAGE_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def image_client(client, image_user):
    client.post(
        "/login", data={"username": "image_test_errlog", "password": IMAGE_PASSWORD}, follow_redirects=True
    )
    return client


class TestLogErrorUtility:
    def test_records_an_exceptions_message_and_traceback(self, app):
        with app.app_context():
            try:
                raise ValueError("something broke")
            except ValueError as exc:
                from app.utils.error_logger import log_error

                entry = log_error(source="unit.test", exc=exc)

            assert entry.source == "unit.test"
            assert entry.message == "something broke"
            assert entry.traceback is not None
            assert "ValueError" in entry.traceback
            assert "something broke" in entry.traceback

    def test_records_a_plain_description_with_no_exception_object(self, app):
        with app.app_context():
            from app.utils.error_logger import log_error

            entry = log_error(source="unit.test", description="a provider returned an error string")

            assert entry.message == "a provider returned an error string"
            assert entry.traceback is None

    def test_a_description_overrides_the_exceptions_own_message(self, app):
        with app.app_context():
            from app.utils.error_logger import log_error

            try:
                raise RuntimeError("raw message")
            except RuntimeError as exc:
                entry = log_error(source="unit.test", exc=exc, description="friendlier message")

            assert entry.message == "friendlier message"
            assert entry.traceback is not None


class TestPermissionGating:
    def test_view_error_logs_requires_permission(self, noperm_client):
        assert noperm_client.get("/logs/errors").status_code == 403


class TestViewErrorLogsPage:
    def test_empty_state(self, logs_client):
        response = logs_client.get("/logs/errors")
        assert response.status_code == 200
        assert b"No error logs found." in response.data

    def test_lists_an_error_log_entry(self, logs_client, app):
        with app.app_context():
            db.session.add(
                ErrorLog(source="builders.generate_description", message="No Qwen API key configured")
            )
            db.session.commit()

        response = logs_client.get("/logs/errors")
        assert response.status_code == 200
        assert b"builders.generate_description" in response.data
        assert b"No Qwen API key configured" in response.data

    def test_filters_by_source(self, logs_client, app):
        with app.app_context():
            db.session.add(ErrorLog(source="worker.run_build", message="build blew up"))
            db.session.add(ErrorLog(source="registries.validate_credentials", message="bad token"))
            db.session.commit()

        response = logs_client.get("/logs/errors?source=worker.run_build")
        assert response.status_code == 200
        assert b"build blew up" in response.data
        assert b"bad token" not in response.data

    def test_shows_traceback_when_present(self, logs_client, app):
        with app.app_context():
            db.session.add(
                ErrorLog(source="unit.test", message="boom", traceback="Traceback (most recent call last):\n...")
            )
            db.session.commit()

        response = logs_client.get("/logs/errors")
        assert response.status_code == 200
        assert b"Traceback" in response.data

    def test_traceback_has_a_copy_button_with_an_svg_icon(self, logs_client, app):
        with app.app_context():
            log = ErrorLog(source="unit.test", message="boom", traceback="Traceback (most recent call last):\n...")
            db.session.add(log)
            db.session.commit()
            log_id = log.id

        response = logs_client.get("/logs/errors")
        assert response.status_code == 200
        assert b"copy-to-clipboard-btn" in response.data
        assert f'data-copy-target="traceback-{log_id}"'.encode() in response.data
        assert b"<svg" in response.data

    def test_no_copy_button_when_there_is_no_traceback(self, logs_client, app):
        with app.app_context():
            db.session.add(ErrorLog(source="unit.test", message="boom"))
            db.session.commit()

        response = logs_client.get("/logs/errors")
        assert response.status_code == 200
        # The generic copy-to-clipboard class is also used by the always-
        # present Share button now, so check specifically for the
        # traceback-targeting button rather than the shared class name.
        assert b'data-copy-target="traceback-' not in response.data


class TestUnhandledExceptionIsLogged:
    def test_an_unexpected_exception_is_recorded_via_the_global_handler(self, image_client, app, monkeypatch):
        def _raise(*args, **kwargs):
            raise RuntimeError("totally unexpected crash")

        monkeypatch.setattr("app.blueprints.images.routes.get_engine_status", _raise)

        with pytest.raises(RuntimeError):
            image_client.get("/images/status")

        with app.app_context():
            error_log = ErrorLog.query.filter_by(source="images.status").first()
            assert error_log is not None
            assert "totally unexpected crash" in error_log.message
            assert error_log.traceback is not None
            assert error_log.method == "GET"
            assert error_log.path == "/images/status"


class TestErrorDetailLink:
    def test_requires_permission(self, noperm_client, app):
        with app.app_context():
            log = ErrorLog(source="unit.test", message="boom")
            db.session.add(log)
            db.session.commit()
            log_id = log.id
        assert noperm_client.get(f"/logs/errors/{log_id}").status_code == 403

    def test_nonexistent_id_is_404(self, logs_client):
        assert logs_client.get("/logs/errors/00000000-0000-0000-0000-000000000000").status_code == 404

    def test_renders_only_the_one_row_with_its_modal_set_to_auto_open(self, logs_client, app):
        with app.app_context():
            # A second, unrelated error — must NOT show up in the single-error view.
            db.session.add(ErrorLog(source="other.source", message="unrelated"))
            log = ErrorLog(source="unit.test", message="the specific error")
            db.session.add(log)
            db.session.commit()
            log_id = log.id

        response = logs_client.get(f"/logs/errors/{log_id}")
        assert response.status_code == 200
        assert b"the specific error" in response.data
        assert b"unrelated" not in response.data
        assert f'data-open-modal="error-detail-modal-{log_id}"'.encode() in response.data
        assert b"Viewing one linked error" in response.data
        # Filter form / pagination controls are hidden in single-error view.
        assert b'name="source"' not in response.data

    def test_share_button_copy_target_holds_the_absolute_link(self, logs_client, app):
        with app.app_context():
            log = ErrorLog(source="unit.test", message="boom")
            db.session.add(log)
            db.session.commit()
            log_id = log.id

        response = logs_client.get(f"/logs/errors/{log_id}")
        assert response.status_code == 200
        assert f'data-copy-target="share-link-{log_id}"'.encode() in response.data
        assert f"/logs/errors/{log_id}".encode() in response.data
