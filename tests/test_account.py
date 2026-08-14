from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import ActivityLog, User

ADMIN_PASSWORD = "AdminPass123!"


class TestAccountPage:
    def test_requires_login(self, client):
        response = client.get("/account/")
        assert response.status_code == 302
        assert response.location.startswith("/login")

    def test_any_logged_in_user_can_reach_it(self, client, noperm_client):
        # noperm_client's role grants zero permissions — this page must
        # still be reachable, since it's about editing yourself, not an
        # admin-gated resource.
        response = noperm_client.get("/account/")
        assert response.status_code == 200
        assert b"My Account" in response.data

    def test_shows_the_current_username_read_only(self, admin_client):
        response = admin_client.get("/account/")
        assert b"admin_test" in response.data
        assert b'disabled' in response.data

    def test_updates_full_name_and_telegram_chat_id(self, admin_client, admin_user, app):
        response = admin_client.post(
            "/account/",
            data={"full_name": "New Name", "telegram_chat_id": "123456789"},
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            user = User.query.get(admin_user)
            assert user.full_name == "New Name"
            assert user.telegram_chat_id == "123456789"

    def test_rejects_a_non_numeric_chat_id(self, admin_client, admin_user, app):
        response = admin_client.post(
            "/account/",
            data={"full_name": "New Name", "telegram_chat_id": "not-a-number"},
        )
        assert response.status_code == 200  # re-renders with a validation error
        assert b"numeric Telegram chat ID" in response.data

        with app.app_context():
            assert User.query.get(admin_user).telegram_chat_id is None

    def test_logs_activity_without_password_change_note(self, admin_client, admin_user, app):
        admin_client.post("/account/", data={"full_name": "New Name"}, follow_redirects=True)
        with app.app_context():
            entry = ActivityLog.query.filter_by(action="UPDATE_OWN_ACCOUNT").first()
            assert entry is not None
            assert "password changed" not in entry.description


class TestAccountPasswordChange:
    def test_changes_the_password_with_correct_current_password(self, client, admin_user, app):
        client.post("/login", data={"username": "admin_test", "password": ADMIN_PASSWORD})

        response = client.post(
            "/account/",
            data={
                "current_password": ADMIN_PASSWORD,
                "new_password": "BrandNewPass123!",
                "confirm_new_password": "BrandNewPass123!",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            assert check_password_hash(User.query.get(admin_user).password_hash, "BrandNewPass123!")

        # And can log back in with the new password.
        client.get("/logout")
        login = client.post(
            "/login", data={"username": "admin_test", "password": "BrandNewPass123!"}, follow_redirects=True
        )
        assert login.request.path == "/"

    def test_rejects_an_incorrect_current_password(self, client, admin_user, app):
        client.post("/login", data={"username": "admin_test", "password": ADMIN_PASSWORD})

        response = client.post(
            "/account/",
            data={
                "current_password": "WrongCurrentPassword!",
                "new_password": "BrandNewPass123!",
                "confirm_new_password": "BrandNewPass123!",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Current password is incorrect" in response.data

        with app.app_context():
            assert check_password_hash(User.query.get(admin_user).password_hash, ADMIN_PASSWORD)

    def test_rejects_a_missing_current_password(self, client, admin_user, app):
        client.post("/login", data={"username": "admin_test", "password": ADMIN_PASSWORD})

        response = client.post(
            "/account/",
            data={"new_password": "BrandNewPass123!", "confirm_new_password": "BrandNewPass123!"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Current password is incorrect" in response.data

    def test_rejects_mismatched_new_passwords(self, client, admin_user, app):
        client.post("/login", data={"username": "admin_test", "password": ADMIN_PASSWORD})

        response = client.post(
            "/account/",
            data={
                "current_password": ADMIN_PASSWORD,
                "new_password": "BrandNewPass123!",
                "confirm_new_password": "SomethingElse123!",
            },
        )
        assert response.status_code == 200  # re-renders with a validation error
        with app.app_context():
            assert check_password_hash(User.query.get(admin_user).password_hash, ADMIN_PASSWORD)

    def test_blank_password_fields_leave_the_password_unchanged(self, client, admin_user, app):
        client.post("/login", data={"username": "admin_test", "password": ADMIN_PASSWORD})
        client.post("/account/", data={"full_name": "Just A Name Change"}, follow_redirects=True)

        with app.app_context():
            assert check_password_hash(User.query.get(admin_user).password_hash, ADMIN_PASSWORD)


class TestAccountCannotEditRestrictedFields:
    def test_username_role_and_active_status_are_not_form_fields(self, admin_client):
        response = admin_client.get("/account/")
        assert b'name="username"' not in response.data
        assert b'name="role_id"' not in response.data
        assert b'name="is_active"' not in response.data
