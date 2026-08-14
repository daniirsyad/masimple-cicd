from datetime import datetime

from app.extensions import db
from app.models import User


def test_users_list_without_permission_is_forbidden(noperm_client):
    response = noperm_client.get("/users/")
    assert response.status_code == 403


def test_users_list_with_permission_is_allowed(admin_client):
    response = admin_client.get("/users/")
    assert response.status_code == 200


class TestUnlockUser:
    def test_requires_user_unlock_permission(self, noperm_client, noperm_user):
        response = noperm_client.post(f"/users/{noperm_user}/unlock")
        assert response.status_code == 403

    def test_clears_the_lockout_and_logs_activity(self, admin_client, admin_user, app):
        with app.app_context():
            user = User.query.get(admin_user)
            user.failed_login_attempts = 5
            user.locked_at = datetime.utcnow()
            db.session.commit()

        response = admin_client.post(f"/users/{admin_user}/unlock", follow_redirects=True)
        assert response.status_code == 200

        with app.app_context():
            user = User.query.get(admin_user)
            assert user.failed_login_attempts == 0
            assert user.locked_at is None

            from app.models import ActivityLog

            assert ActivityLog.query.filter_by(action="UNLOCK_USER").first() is not None

    def test_locked_badge_and_unlock_button_appear_only_when_locked(self, admin_client, admin_user, app):
        response = admin_client.get("/users/")
        assert b"Locked" not in response.data

        with app.app_context():
            user = User.query.get(admin_user)
            user.failed_login_attempts = 5
            db.session.commit()

        response = admin_client.get("/users/")
        assert b"Locked" in response.data
        assert f'/users/{admin_user}/unlock'.encode() in response.data
