import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import AIProviderConfig, ChangeType, Object, PromptTemplate, Permission, Role, User
from app.utils.crypto import decrypt

AI_PASSWORD = "AiPass123!"


@pytest.fixture
def ai_user(app):
    with app.app_context():
        permission = Permission(code="aiprovider.manage", description="Manage AI settings")
        db.session.add(permission)
        role = Role(name="AiAdmin", description="Test AI admin role")
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            username="ai_test",
            password_hash=generate_password_hash(AI_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def ai_client(client, ai_user):
    client.post("/login", data={"username": "ai_test", "password": AI_PASSWORD}, follow_redirects=True)
    return client


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/ai-settings/").status_code == 403

    def test_create_provider_requires_permission(self, noperm_client):
        assert noperm_client.post("/ai-settings/providers/create", data={}).status_code == 403

    def test_test_commit_message_requires_permission(self, noperm_client):
        assert noperm_client.post("/ai-settings/test-commit-message", data={}).status_code == 403


class TestProviderCrud:
    def test_create_provider_encrypts_the_api_key(self, ai_client, app):
        response = ai_client.post(
            "/ai-settings/providers/create",
            data={
                "create-provider-provider_type": "qwen",
                "create-provider-model_name": "qwen-plus",
                "create-provider-api_key": "super-secret-key",
                "create-provider-is_active": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            provider = AIProviderConfig.query.filter_by(provider_type="qwen").first()
            assert provider is not None
            assert provider.encrypted_api_key is not None
            assert provider.encrypted_api_key != "super-secret-key"
            assert decrypt(provider.encrypted_api_key) == "super-secret-key"

    def test_create_provider_without_api_key_leaves_it_unset(self, ai_client, app):
        ai_client.post(
            "/ai-settings/providers/create",
            data={
                "create-provider-provider_type": "qwen",
                "create-provider-model_name": "qwen-plus",
                "create-provider-api_key": "",
            },
        )
        with app.app_context():
            provider = AIProviderConfig.query.filter_by(provider_type="qwen").first()
            assert provider.encrypted_api_key is None

    def test_only_one_provider_can_be_default(self, ai_client, app):
        ai_client.post(
            "/ai-settings/providers/create",
            data={
                "create-provider-provider_type": "qwen",
                "create-provider-model_name": "qwen-plus",
                "create-provider-is_default": "y",
            },
        )
        ai_client.post(
            "/ai-settings/providers/create",
            data={
                "create-provider-provider_type": "claude",
                "create-provider-model_name": "claude-x",
                "create-provider-is_default": "y",
            },
        )

        with app.app_context():
            defaults = AIProviderConfig.query.filter_by(is_default=True).all()
            assert len(defaults) == 1
            assert defaults[0].provider_type == "claude"

    def test_editing_without_a_new_key_keeps_the_existing_one(self, ai_client, app):
        with app.app_context():
            from app.utils.crypto import encrypt

            provider = AIProviderConfig(
                provider_type="qwen", model_name="qwen-plus",
                encrypted_api_key=encrypt("original-key"), is_active=True,
            )
            db.session.add(provider)
            db.session.commit()
            provider_id = provider.id

        ai_client.post(
            f"/ai-settings/providers/{provider_id}/edit",
            data={
                f"provider-{provider_id}-provider_type": "qwen",
                f"provider-{provider_id}-model_name": "qwen-max",
                f"provider-{provider_id}-api_key": "",
            },
        )

        with app.app_context():
            provider = AIProviderConfig.query.get(provider_id)
            assert provider.model_name == "qwen-max"
            assert decrypt(provider.encrypted_api_key) == "original-key"

    def test_editing_with_a_new_key_replaces_it(self, ai_client, app):
        with app.app_context():
            from app.utils.crypto import encrypt

            provider = AIProviderConfig(
                provider_type="qwen", model_name="qwen-plus",
                encrypted_api_key=encrypt("original-key"), is_active=True,
            )
            db.session.add(provider)
            db.session.commit()
            provider_id = provider.id

        ai_client.post(
            f"/ai-settings/providers/{provider_id}/edit",
            data={
                f"provider-{provider_id}-provider_type": "qwen",
                f"provider-{provider_id}-model_name": "qwen-plus",
                f"provider-{provider_id}-api_key": "new-key",
            },
        )

        with app.app_context():
            provider = AIProviderConfig.query.get(provider_id)
            assert decrypt(provider.encrypted_api_key) == "new-key"

    def test_the_edit_form_never_shows_the_existing_key(self, ai_client, app):
        with app.app_context():
            from app.utils.crypto import encrypt

            provider = AIProviderConfig(
                provider_type="qwen", model_name="qwen-plus",
                encrypted_api_key=encrypt("super-secret-key"), is_active=True,
            )
            db.session.add(provider)
            db.session.commit()

        response = ai_client.get("/ai-settings/")
        assert b"super-secret-key" not in response.data

    def test_delete_provider(self, ai_client, app):
        with app.app_context():
            provider = AIProviderConfig(provider_type="qwen", model_name="qwen-plus", is_active=True)
            db.session.add(provider)
            db.session.commit()
            provider_id = provider.id

        response = ai_client.post(f"/ai-settings/providers/{provider_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert AIProviderConfig.query.get(provider_id) is None


class TestPromptTemplateCrud:
    def test_create_template(self, ai_client, app):
        response = ai_client.post(
            "/ai-settings/templates/create",
            data={
                "create-template-name": "My Template",
                "create-template-template_text": "Branch: {{branch_name}}",
                "create-template-is_active": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            template = PromptTemplate.query.filter_by(name="My Template").first()
            assert template is not None
            assert template.template_text == "Branch: {{branch_name}}"

    def test_only_one_template_can_be_default(self, ai_client, app):
        ai_client.post(
            "/ai-settings/templates/create",
            data={
                "create-template-name": "Template A",
                "create-template-template_text": "A",
                "create-template-is_default": "y",
            },
        )
        ai_client.post(
            "/ai-settings/templates/create",
            data={
                "create-template-name": "Template B",
                "create-template-template_text": "B",
                "create-template-is_default": "y",
            },
        )
        with app.app_context():
            defaults = PromptTemplate.query.filter_by(is_default=True).all()
            assert len(defaults) == 1
            assert defaults[0].name == "Template B"

    def test_edit_template(self, ai_client, app):
        with app.app_context():
            template = PromptTemplate(name="Old", template_text="old text", is_active=True)
            db.session.add(template)
            db.session.commit()
            template_id = template.id

        ai_client.post(
            f"/ai-settings/templates/{template_id}/edit",
            data={
                f"template-{template_id}-name": "New",
                f"template-{template_id}-template_text": "new text",
            },
        )
        with app.app_context():
            template = PromptTemplate.query.get(template_id)
            assert template.name == "New"
            assert template.template_text == "new text"

    def test_delete_template(self, ai_client, app):
        with app.app_context():
            template = PromptTemplate(name="ToDelete", template_text="x", is_active=True)
            db.session.add(template)
            db.session.commit()
            template_id = template.id

        ai_client.post(f"/ai-settings/templates/{template_id}/delete", follow_redirects=True)
        with app.app_context():
            assert PromptTemplate.query.get(template_id) is None


class TestCommitMessageTester:
    # Assertions below target the exact badge markup the live test-result
    # panel renders, not bare substrings — the page's own "How does autofill
    # work?" documentation already contains overlapping plain-text examples
    # (Checkout Flow, Bug Fix, minor/patch), so a naive substring check
    # would pass even if this route were broken.

    def test_bump_type_and_direct_matches_render_without_any_ai_provider_configured(self, ai_client, app):
        with app.app_context():
            db.session.add(Object(name="Checkout Flow"))
            db.session.add(ChangeType(name="Bug Fix"))
            db.session.commit()

        response = ai_client.post(
            "/ai-settings/test-commit-message",
            data={"commit_messages": "fix: resolve pagination bug in Checkout Flow (Bug Fix)"},
        )
        assert response.status_code == 200
        body = response.data.decode()
        assert '<span class="badge badge-primary badge-sm">patch</span>' in body
        assert '<span class="badge badge-ghost badge-sm mr-1 mb-1">Checkout Flow</span>' in body
        assert '<span class="badge badge-ghost badge-sm">Bug Fix</span>' in body

    def test_multiple_lines_are_each_treated_as_a_separate_commit_message(self, ai_client):
        response = ai_client.post(
            "/ai-settings/test-commit-message",
            data={"commit_messages": "feat: add a thing\nfix: unrelated bug"},
        )
        assert response.status_code == 200
        # feat (minor) outranks fix (patch) across the two lines.
        assert '<span class="badge badge-primary badge-sm">minor</span>' in response.data.decode()

    def test_explicit_bump_type_word_is_honored(self, ai_client):
        response = ai_client.post(
            "/ai-settings/test-commit-message",
            data={"commit_messages": "major: overhauled the auth system"},
        )
        assert response.status_code == 200
        assert '<span class="badge badge-primary badge-sm">major</span>' in response.data.decode()

    def test_blank_input_is_rejected_by_required_validation(self, ai_client):
        response = ai_client.post(
            "/ai-settings/test-commit-message",
            data={"commit_messages": "   \n   "},
        )
        assert response.status_code == 200
        body = response.data.decode()
        assert "This field is required." in body
        # No result panel should render for a rejected submission.
        assert "badge-primary badge-sm" not in body

    def test_validation_error_does_not_leak_into_an_unrelated_invalid_provider_submission(self, ai_client):
        # Regression check: a POST to a *different* form on the same page
        # must not spuriously trigger the commit-message field's own
        # validation error just because the request method is also POST —
        # the tester's form must only ever validate against its own route.
        # (The provider form's own missing model_name legitimately produces
        # one "This field is required." already, hence count == 1, not 0.)
        response = ai_client.post(
            "/ai-settings/providers/create",
            data={"create-provider-provider_type": "qwen"},  # missing required model_name -> invalid
        )
        assert response.status_code == 200
        assert response.data.decode().count("This field is required.") == 1
