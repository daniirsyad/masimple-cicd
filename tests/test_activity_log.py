from app.models import ActivityLog, Role, User


def test_creating_user_writes_activity_log(app, admin_client):
    with app.app_context():
        role_id = str(Role.query.filter_by(name="Admin").first().id)

    response = admin_client.post(
        "/users/create",
        data={
            "username": "new_hire",
            "password": "NewHirePass123!",
            "full_name": "New Hire",
            "role_id": role_id,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        user = User.query.filter_by(username="new_hire").first()
        assert user is not None

        log = ActivityLog.query.filter_by(action="CREATE_USER", target_id=str(user.id)).first()
        assert log is not None
        assert "new_hire" in log.description
