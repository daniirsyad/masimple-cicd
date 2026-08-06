def test_users_list_without_permission_is_forbidden(noperm_client):
    response = noperm_client.get("/users/")
    assert response.status_code == 403


def test_users_list_with_permission_is_allowed(admin_client):
    response = admin_client.get("/users/")
    assert response.status_code == 200
