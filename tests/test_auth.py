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
