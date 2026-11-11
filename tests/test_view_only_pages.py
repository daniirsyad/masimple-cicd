"""Pages a user can view but not change must not offer the change actions.

Each page's create modal stands in for its whole set of gated actions:
hidden for a view-only user, present once the action permission is added
(so the test can't pass just because the marker never renders).
"""

import pytest

PAGES = [
    ("/deployment-servers/", "deployment_server.view", "deployment_server.manage", "create-server-modal"),
    ("/versions/", "version.view", "version.manage", "create-version-modal"),
    ("/users/", "user.view", "user.create", 'id="create-modal"'),
    ("/roles/", "role.view", "role.create", 'id="create-modal"'),
    ("/permissions/", "permission.view", "permission.create", 'id="create-modal"'),
    ("/menus/", "menu.view", "menu.edit", 'id="create-modal"'),
    ("/builders/", "builder.view", "builder.manage", "create-builder-modal"),
    ("/workflows/", "workflow.view", "workflow.manage", "create-workflow-modal"),
    ("/deployment-manifests/", "deployment_manifest.view", "deployment_manifest.manage", "create-manifest-modal"),
]


@pytest.mark.parametrize("url,view_perm,action_perm,marker", PAGES)
def test_view_only_user_gets_no_create_action(perm_client, url, view_perm, action_perm, marker):
    response = perm_client(view_perm).get(url)
    assert response.status_code == 200
    assert marker not in response.get_data(as_text=True)


@pytest.mark.parametrize("url,view_perm,action_perm,marker", PAGES)
def test_user_with_action_permission_gets_create_action(perm_client, url, view_perm, action_perm, marker):
    response = perm_client(view_perm, action_perm).get(url)
    assert response.status_code == 200
    assert marker in response.get_data(as_text=True)
