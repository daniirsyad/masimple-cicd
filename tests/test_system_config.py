from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Permission, Role, SystemConfig, User
from app.utils.system_config import get_system_config
from app.utils.timezone import format_local, to_local

CONFIG_PASSWORD = "ConfigPass123!"


@pytest.fixture
def config_user(app):
    with app.app_context():
        permission = Permission(code="system.manage", description="Manage system configuration")
        db.session.add(permission)
        role = Role(name="ConfigAdmin", description="Test system config admin role")
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            username="config_test",
            password_hash=generate_password_hash(CONFIG_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def config_client(client, config_user):
    client.post(
        "/login", data={"username": "config_test", "password": CONFIG_PASSWORD}, follow_redirects=True
    )
    return client


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/config/").status_code == 403

    def test_update_requires_permission(self, noperm_client):
        response = noperm_client.post(
            "/config/", data={"timezone": "UTC", "session_timeout_minutes": "30"}
        )
        assert response.status_code == 403


class TestSingletonGetter:
    def test_creates_default_row_on_first_access(self, app):
        with app.app_context():
            assert SystemConfig.query.count() == 0
            config = get_system_config()
            assert config.timezone == "UTC"
            assert config.session_timeout_minutes == 60
            assert config.build_engine == "docker"
            assert config.hide_navbar_title_when_sidebar_open is True
            assert SystemConfig.query.count() == 1

    def test_returns_the_same_row_on_repeat_access(self, app):
        with app.app_context():
            first = get_system_config()
            second = get_system_config()
            assert first.id == second.id
            assert SystemConfig.query.count() == 1


class TestIndexPage:
    def test_get_renders_current_values(self, config_client, app):
        with app.app_context():
            config = get_system_config()
            config.timezone = "Asia/Jakarta"
            config.session_timeout_minutes = 45
            db.session.commit()

        response = config_client.get("/config/")
        assert response.status_code == 200
        assert b"Asia/Jakarta" in response.data

    def test_post_updates_timezone_and_session_timeout(self, config_client, app):
        response = config_client.post(
            "/config/",
            data={
                "timezone": "Asia/Jakarta",
                "session_timeout_minutes": "120",
                "build_engine": "docker",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            config = get_system_config()
            assert config.timezone == "Asia/Jakarta"
            assert config.session_timeout_minutes == 120

    def test_post_updates_build_engine(self, config_client, app):
        with app.app_context():
            config = get_system_config()
            config.build_engine = "kaniko"  # pre-existing value, bypassing the form's choices
            db.session.commit()

        response = config_client.post(
            "/config/",
            data={"timezone": "UTC", "session_timeout_minutes": "60", "build_engine": "docker"},
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            assert get_system_config().build_engine == "docker"

    def test_post_rejects_kaniko_as_not_a_valid_choice(self, config_client, app):
        """"kaniko" was removed from BUILD_ENGINE_CHOICES after it corrupted
        a live app container's filesystem mid-build — see
        app/blueprints/system_config/forms.py. Submitting it should fail
        validation, not silently save it.
        """
        with app.app_context():
            assert get_system_config().build_engine == "docker"

        response = config_client.post(
            "/config/",
            data={"timezone": "UTC", "session_timeout_minutes": "60", "build_engine": "kaniko"},
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            assert get_system_config().build_engine == "docker"

    def test_post_can_enable_hide_navbar_title(self, config_client, app):
        response = config_client.post(
            "/config/",
            data={
                "timezone": "UTC",
                "session_timeout_minutes": "60",
                "build_engine": "docker",
                "hide_navbar_title_when_sidebar_open": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            assert get_system_config().hide_navbar_title_when_sidebar_open is True

    def test_post_omitting_the_checkbox_disables_it(self, config_client, app):
        with app.app_context():
            config = get_system_config()
            config.hide_navbar_title_when_sidebar_open = True
            db.session.commit()

        response = config_client.post(
            "/config/",
            data={"timezone": "UTC", "session_timeout_minutes": "60", "build_engine": "docker"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            assert get_system_config().hide_navbar_title_when_sidebar_open is False

    def test_navbar_reflects_the_configured_flag(self, config_client, app):
        with app.app_context():
            config = get_system_config()
            config.hide_navbar_title_when_sidebar_open = False
            db.session.commit()

        response = config_client.get("/")
        assert b'data-hide-when-sidebar-open="false"' in response.data

        with app.app_context():
            config = get_system_config()
            config.hide_navbar_title_when_sidebar_open = True
            db.session.commit()

        response = config_client.get("/")
        assert b'data-hide-when-sidebar-open="true"' in response.data

    def test_post_rejects_out_of_range_timeout(self, config_client, app):
        response = config_client.post(
            "/config/",
            data={"timezone": "UTC", "session_timeout_minutes": "0", "build_engine": "docker"},
        )
        assert response.status_code == 200  # re-renders form with validation error

        with app.app_context():
            assert get_system_config().session_timeout_minutes != 0

    def test_post_updates_commit_log_limit(self, config_client, app):
        response = config_client.post(
            "/config/",
            data={
                "timezone": "UTC",
                "session_timeout_minutes": "60",
                "build_engine": "docker",
                "deployment_status_check_interval_seconds": "60",
                "commit_log_limit": "50",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            assert get_system_config().commit_log_limit == 50

    def test_post_rejects_out_of_range_commit_log_limit(self, config_client, app):
        response = config_client.post(
            "/config/",
            data={
                "timezone": "UTC",
                "session_timeout_minutes": "60",
                "build_engine": "docker",
                "deployment_status_check_interval_seconds": "60",
                "commit_log_limit": "0",
            },
        )
        assert response.status_code == 200  # re-renders form with validation error
        with app.app_context():
            assert get_system_config().commit_log_limit != 0

    def test_post_updates_max_login_attempts(self, config_client, app):
        response = config_client.post(
            "/config/",
            data={
                "timezone": "UTC",
                "session_timeout_minutes": "60",
                "build_engine": "docker",
                "max_login_attempts": "10",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            assert get_system_config().max_login_attempts == 10

    def test_post_can_enable_telegram_notifications(self, config_client, app):
        response = config_client.post(
            "/config/",
            data={
                "timezone": "UTC",
                "session_timeout_minutes": "60",
                "build_engine": "docker",
                "max_login_attempts": "5",
                "telegram_notifications_enabled": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            assert get_system_config().telegram_notifications_enabled is True

    def test_post_with_bot_token_stores_it_encrypted(self, config_client, app):
        config_client.post(
            "/config/",
            data={
                "timezone": "UTC",
                "session_timeout_minutes": "60",
                "build_engine": "docker",
                "max_login_attempts": "5",
                "telegram_bot_token": "123456:ABC-DEF",
            },
            follow_redirects=True,
        )
        with app.app_context():
            from app.utils.crypto import decrypt

            config = get_system_config()
            assert config.encrypted_telegram_bot_token is not None
            assert config.encrypted_telegram_bot_token != "123456:ABC-DEF"
            assert decrypt(config.encrypted_telegram_bot_token) == "123456:ABC-DEF"

    def test_post_with_blank_bot_token_keeps_the_existing_one(self, config_client, app):
        with app.app_context():
            from app.utils.crypto import encrypt

            config = get_system_config()
            config.encrypted_telegram_bot_token = encrypt("original-token")
            db.session.commit()

        config_client.post(
            "/config/",
            data={
                "timezone": "UTC",
                "session_timeout_minutes": "60",
                "build_engine": "docker",
                "max_login_attempts": "5",
                "telegram_bot_token": "",
            },
            follow_redirects=True,
        )

        with app.app_context():
            from app.utils.crypto import decrypt

            assert decrypt(get_system_config().encrypted_telegram_bot_token) == "original-token"

    def test_bot_token_is_never_rendered_back_into_the_page(self, config_client, app):
        with app.app_context():
            from app.utils.crypto import encrypt

            config = get_system_config()
            config.encrypted_telegram_bot_token = encrypt("super-secret-token")
            db.session.commit()

        response = config_client.get("/config/")
        assert b"super-secret-token" not in response.data

    def test_post_sets_the_security_notification_recipient(self, config_client, app, config_user):
        response = config_client.post(
            "/config/",
            data={
                "timezone": "UTC",
                "session_timeout_minutes": "60",
                "build_engine": "docker",
                "max_login_attempts": "5",
                "security_notification_user_id": str(config_user),
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            assert get_system_config().security_notification_user_id == config_user

    def test_post_can_clear_the_security_notification_recipient(self, config_client, app, config_user):
        with app.app_context():
            config = get_system_config()
            config.security_notification_user_id = config_user
            db.session.commit()

        config_client.post(
            "/config/",
            data={
                "timezone": "UTC",
                "session_timeout_minutes": "60",
                "build_engine": "docker",
                "max_login_attempts": "5",
                "security_notification_user_id": "",
            },
            follow_redirects=True,
        )
        with app.app_context():
            assert get_system_config().security_notification_user_id is None

    def test_get_shows_the_current_recipient_selected(self, config_client, app, config_user):
        with app.app_context():
            config = get_system_config()
            config.security_notification_user_id = config_user
            db.session.commit()

        response = config_client.get("/config/")
        assert response.status_code == 200
        assert f'selected value="{config_user}"'.encode() in response.data

    def test_post_logs_activity(self, config_client, app):
        config_client.post(
            "/config/",
            data={
                "timezone": "Asia/Jakarta",
                "session_timeout_minutes": "90",
                "build_engine": "docker",
            },
            follow_redirects=True,
        )
        with app.app_context():
            from app.models import ActivityLog

            entry = ActivityLog.query.filter_by(action="UPDATE_SYSTEM_CONFIG").first()
            assert entry is not None
            assert "Asia/Jakarta" in entry.description


class TestTimezoneConversion:
    def test_to_local_converts_naive_utc_to_configured_timezone(self, app):
        with app.app_context():
            config = get_system_config()
            config.timezone = "Asia/Jakarta"  # UTC+7, no DST
            db.session.commit()

            naive_utc = datetime(2026, 1, 1, 0, 0, 0)
            local = to_local(naive_utc)
            assert local.utcoffset() == timedelta(hours=7)
            assert local.hour == 7

    def test_to_local_returns_none_for_none(self, app):
        with app.app_context():
            assert to_local(None) is None

    def test_format_local_uses_configured_format(self, app):
        with app.app_context():
            config = get_system_config()
            config.timezone = "UTC"
            db.session.commit()

            naive_utc = datetime(2026, 1, 1, 13, 30, 0)
            assert format_local(naive_utc, "%Y-%m-%d %H:%M") == "2026-01-01 13:30"

    def test_format_local_returns_empty_string_for_none(self, app):
        with app.app_context():
            assert format_local(None) == ""

    def test_falls_back_to_utc_for_unknown_timezone_name(self, app):
        with app.app_context():
            config = get_system_config()
            config.timezone = "Not/A_Real_Zone"
            db.session.commit()

            naive_utc = datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
            local = to_local(naive_utc.replace(tzinfo=None))
            assert local.utcoffset() == timedelta(0)


class TestSessionTimeout:
    def test_login_applies_configured_session_lifetime(self, config_client, app):
        with app.app_context():
            config = get_system_config()
            config.session_timeout_minutes = 15
            db.session.commit()

        # any authenticated request re-syncs app.permanent_session_lifetime
        config_client.get("/config/")
        assert app.permanent_session_lifetime == timedelta(minutes=15)
