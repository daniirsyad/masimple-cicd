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


def test_deleting_a_user_keeps_their_history_unattributed(perm_client, app):
    from werkzeug.security import generate_password_hash

    from app.models import BuildBatch, DeploymentRun, PasswordResetToken, Version, VersionType, Workflow, WorkflowRun

    client = perm_client("user.view", "user.delete")
    with app.app_context():
        target = User(username="leaver", password_hash=generate_password_hash("Whatever123!"), is_active=True)
        db.session.add(target)
        db.session.flush()
        version_type = VersionType(name="DEV")
        db.session.add(version_type)
        db.session.flush()
        version = Version(name="svc", version_type_id=version_type.id)
        db.session.add(version)
        db.session.flush()
        batch = BuildBatch(version_id=version.id, bump_type="patch", requested_by=target.id)
        deploy_run = DeploymentRun(action="deploy", triggered_by=target.id)
        workflow = Workflow(name="wf", created_by=target.id)
        db.session.add_all([batch, deploy_run, workflow])
        db.session.flush()
        workflow_run = WorkflowRun(workflow_id=workflow.id, triggered_by=target.id)
        token = PasswordResetToken(user_id=target.id, token_hash="x", expires_at=datetime.utcnow())
        db.session.add_all([workflow_run, token])
        db.session.commit()
        target_id, batch_id, deploy_run_id, workflow_run_id = target.id, batch.id, deploy_run.id, workflow_run.id

    response = client.post(f"/users/{target_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert User.query.get(target_id) is None
        assert BuildBatch.query.get(batch_id).requested_by is None
        assert DeploymentRun.query.get(deploy_run_id).triggered_by is None
        assert WorkflowRun.query.get(workflow_run_id).triggered_by is None
        assert PasswordResetToken.query.filter_by(user_id=target_id).count() == 0


def test_a_user_can_delete_their_own_account_and_is_signed_out(perm_client, app):
    client = perm_client("user.view", "user.delete")
    with app.app_context():
        me = User.query.filter_by(username="perm_client").one()
        me_id = me.id

    html = client.get("/users/").data.decode()
    assert "This is your own account: you will be signed out." in html

    response = client.post(f"/users/{me_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert User.query.get(me_id) is None
    assert client.get("/users/").status_code == 302  # signed out: redirected to login


def test_user_rows_say_who_is_locked_deactivated_or_roleless(perm_client, app):
    from werkzeug.security import generate_password_hash

    client = perm_client("user.view", "user.unlock")
    with app.app_context():
        pw = generate_password_hash("Whatever123!")
        db.session.add_all(
            [
                User(username="locked1", password_hash=pw, is_active=True, failed_login_attempts=99),
                User(username="gone1", password_hash=pw, is_active=False),
            ]
        )
        db.session.commit()

    html = client.get("/users/").data.decode()
    assert "Locked after 99 failed sign-ins" in html
    assert "entry-rail-warning" in html
    assert ">Unlock</button>" in html
    assert "Deactivated, can&#39;t sign in" in html or "Deactivated, can't sign in" in html
    assert "no role, so no permissions" in html
    assert ">You</span>" in html
