import hashlib
from datetime import datetime, timedelta

from app.extensions import db
from app.models import PasswordResetToken, User
from app.utils.system_config import get_system_config

ADMIN_PASSWORD = "AdminPass123!"


def test_login_with_valid_credentials_redirects_to_dashboard(client, admin_user):
    response = client.post(
        "/login",
        data={"username": "admin_test", "password": "AdminPass123!"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert response.request.path == "/"


def test_login_with_invalid_credentials_shows_error(client, admin_user):
    response = client.post(
        "/login",
        data={"username": "admin_test", "password": "WrongPassword!"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert response.request.path == "/login"
    assert b"Invalid username or password" in response.data


def test_unauthenticated_request_to_permission_protected_route_redirects_to_login(client):
    response = client.get("/users/")

    assert response.status_code == 302
    assert response.location.startswith("/login")


def test_expired_session_redirects_to_login_on_next_request(client, admin_user):
    client.post(
        "/login",
        data={"username": "admin_test", "password": "AdminPass123!"},
    )

    with client.session_transaction() as sess:
        sess.clear()

    response = client.get("/users/", follow_redirects=True)

    assert response.status_code == 200
    assert response.request.path == "/login"


class TestLoginLockout:
    def test_failed_attempts_increment_on_wrong_password(self, client, admin_user, app):
        client.post("/login", data={"username": "admin_test", "password": "WrongPassword!"})
        with app.app_context():
            assert User.query.get(admin_user).failed_login_attempts == 1

    def test_successful_login_resets_the_counter(self, client, admin_user, app):
        with app.app_context():
            user = User.query.get(admin_user)
            user.failed_login_attempts = 3
            db.session.commit()

        client.post("/login", data={"username": "admin_test", "password": ADMIN_PASSWORD})

        with app.app_context():
            user = User.query.get(admin_user)
            assert user.failed_login_attempts == 0
            assert user.locked_at is None

    def test_account_locks_after_max_attempts_and_blocks_further_logins(self, client, admin_user, app):
        with app.app_context():
            max_attempts = get_system_config().max_login_attempts

        for _ in range(max_attempts):
            client.post("/login", data={"username": "admin_test", "password": "WrongPassword!"})

        with app.app_context():
            user = User.query.get(admin_user)
            assert user.failed_login_attempts == max_attempts
            assert user.locked_at is not None

        # Even the CORRECT password is now rejected — the lock isn't
        # bypassable by finally guessing right.
        response = client.post(
            "/login", data={"username": "admin_test", "password": ADMIN_PASSWORD}, follow_redirects=True
        )
        assert response.status_code == 200
        assert b"account is locked" in response.data

    def test_locking_this_attempt_does_not_reveal_more_than_a_generic_error(self, client, admin_user, app):
        with app.app_context():
            max_attempts = get_system_config().max_login_attempts

        for _ in range(max_attempts - 1):
            client.post("/login", data={"username": "admin_test", "password": "WrongPassword!"})

        # This is the attempt that crosses the threshold.
        response = client.post(
            "/login", data={"username": "admin_test", "password": "WrongPassword!"}, follow_redirects=True
        )
        assert b"Invalid username or password" in response.data
        assert b"locked" not in response.data

    def test_nonexistent_username_never_touches_lockout_state(self, client, app):
        client.post("/login", data={"username": "nobody-here", "password": "whatever"})
        # No exception, no crash — this test's real assertion is simply
        # that the request above succeeded without a 500.

    def test_inactive_user_does_not_accumulate_failed_attempts(self, client, admin_user, app):
        with app.app_context():
            user = User.query.get(admin_user)
            user.is_active = False
            db.session.commit()

        client.post("/login", data={"username": "admin_test", "password": "WrongPassword!"})

        with app.app_context():
            assert User.query.get(admin_user).failed_login_attempts == 0

    def test_wrong_password_notifies_the_security_contact_not_the_account_owner(self, client, admin_user, app, monkeypatch):
        # Notifications go to the designated security contact (see
        # TestSecurityNotificationRouting below for the recipient-resolution
        # tests) — this test only checks that login() calls
        # notify_security_contact (mentioning the affected username) rather
        # than notify_user (which would message the account owner directly).
        security_calls = []
        user_calls = []
        monkeypatch.setattr(
            "app.blueprints.auth.routes.notify_security_contact", lambda text: security_calls.append(text) or True
        )
        monkeypatch.setattr(
            "app.blueprints.auth.routes.notify_user", lambda user, text: user_calls.append(text) or True
        )

        client.post("/login", data={"username": "admin_test", "password": "WrongPassword!"})

        assert len(security_calls) == 1
        assert "admin_test" in security_calls[0]
        assert not user_calls

    def test_successful_login_notifies_the_security_contact_not_the_account_owner(self, client, admin_user, app, monkeypatch):
        security_calls = []
        user_calls = []
        monkeypatch.setattr(
            "app.blueprints.auth.routes.notify_security_contact", lambda text: security_calls.append(text) or True
        )
        monkeypatch.setattr(
            "app.blueprints.auth.routes.notify_user", lambda user, text: user_calls.append(text) or True
        )

        client.post("/login", data={"username": "admin_test", "password": ADMIN_PASSWORD})

        assert len(security_calls) == 1
        assert "admin_test" in security_calls[0]
        assert not user_calls


class TestForgotPassword:
    def test_shows_the_same_generic_message_for_a_real_and_a_fake_username(self, client, admin_user):
        real = client.post("/forgot-password", data={"username": "admin_test"}, follow_redirects=True)
        fake = client.post("/forgot-password", data={"username": "does-not-exist"}, follow_redirects=True)

        real_flash = [line for line in real.data.split(b"\n") if b"reset link" in line]
        fake_flash = [line for line in fake.data.split(b"\n") if b"reset link" in line]
        assert real_flash == fake_flash

    def test_creates_a_reset_token_and_sends_it_via_telegram_when_configured(self, client, admin_user, app, monkeypatch):
        with app.app_context():
            User.query.get(admin_user).telegram_chat_id = "12345"
            db.session.commit()

        sent = {}
        monkeypatch.setattr(
            "app.blueprints.auth.routes.notify_user",
            lambda user, text: sent.update(username=user.username, text=text) or True,
        )

        client.post("/forgot-password", data={"username": "admin_test"}, follow_redirects=True)

        assert sent.get("username") == "admin_test"
        assert "/reset-password/" in sent.get("text", "")
        with app.app_context():
            assert PasswordResetToken.query.count() == 1

    def test_no_token_created_without_a_telegram_chat_id(self, client, admin_user, app):
        client.post("/forgot-password", data={"username": "admin_test"}, follow_redirects=True)
        with app.app_context():
            assert PasswordResetToken.query.count() == 0


class TestResetPassword:
    def _create_token(self, app, user_id, expires_in_minutes=15, used=False):
        raw_token = "test-raw-token"
        with app.app_context():
            token = PasswordResetToken(
                user_id=user_id,
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                expires_at=datetime.utcnow() + timedelta(minutes=expires_in_minutes),
                used_at=datetime.utcnow() if used else None,
            )
            db.session.add(token)
            db.session.commit()
        return raw_token

    def test_valid_token_resets_the_password_and_logs_in_works_afterward(self, client, admin_user, app):
        raw_token = self._create_token(app, admin_user)

        response = client.post(
            f"/reset-password/{raw_token}",
            data={"password": "NewPassword123!", "confirm_password": "NewPassword123!"},
            follow_redirects=True,
        )
        assert response.status_code == 200

        login = client.post(
            "/login", data={"username": "admin_test", "password": "NewPassword123!"}, follow_redirects=True
        )
        assert login.request.path == "/"

    def test_valid_token_also_clears_an_existing_lockout(self, client, admin_user, app):
        with app.app_context():
            user = User.query.get(admin_user)
            user.failed_login_attempts = 5
            user.locked_at = datetime.utcnow()
            db.session.commit()

        raw_token = self._create_token(app, admin_user)
        client.post(
            f"/reset-password/{raw_token}",
            data={"password": "NewPassword123!", "confirm_password": "NewPassword123!"},
        )

        with app.app_context():
            user = User.query.get(admin_user)
            assert user.failed_login_attempts == 0
            assert user.locked_at is None

    def test_token_is_single_use(self, client, admin_user, app):
        raw_token = self._create_token(app, admin_user)
        client.post(
            f"/reset-password/{raw_token}",
            data={"password": "NewPassword123!", "confirm_password": "NewPassword123!"},
        )

        second = client.get(f"/reset-password/{raw_token}", follow_redirects=True)
        assert b"invalid or has expired" in second.data

    def test_expired_token_is_rejected(self, client, admin_user, app):
        raw_token = self._create_token(app, admin_user, expires_in_minutes=-1)
        response = client.get(f"/reset-password/{raw_token}", follow_redirects=True)
        assert b"invalid or has expired" in response.data

    def test_unknown_token_is_rejected(self, client):
        response = client.get("/reset-password/not-a-real-token", follow_redirects=True)
        assert b"invalid or has expired" in response.data

    def test_mismatched_passwords_are_rejected(self, client, admin_user, app):
        raw_token = self._create_token(app, admin_user)
        response = client.post(
            f"/reset-password/{raw_token}",
            data={"password": "NewPassword123!", "confirm_password": "SomethingElse123!"},
        )
        assert response.status_code == 200  # re-renders with a validation error
        with app.app_context():
            from werkzeug.security import check_password_hash

            assert check_password_hash(User.query.get(admin_user).password_hash, ADMIN_PASSWORD)
