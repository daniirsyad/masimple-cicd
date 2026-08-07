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
