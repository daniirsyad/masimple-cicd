from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Builder,
    BuildBatch,
    ChangeType,
    GitSource,
    ImageBuild,
    Permission,
    RegistryTarget,
    Repository,
    Role,
    User,
    Version,
    VersionDocumentation,
    VersionLink,
    VersionType,
)

DOC_PASSWORD = "DocPass123!"


@pytest.fixture
def doc_user(app):
    with app.app_context():
        permission = Permission(code="documentation.edit", description="View and edit documentation")
        db.session.add(permission)
        role = Role(name="DocEditor", description="Test doc editor role")
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            username="doc_test",
            password_hash=generate_password_hash(DOC_PASSWORD),
            full_name="Doc Test",
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def doc_client(client, doc_user):
    client.post("/login", data={"username": "doc_test", "password": DOC_PASSWORD}, follow_redirects=True)
    return client


@pytest.fixture
def base_entities(app):
    with app.app_context():
        original_builder_role = Role(name="OriginalBuilderRole")
        db.session.add(original_builder_role)
        db.session.flush()
        original_builder = User(
            username="original_builder",
            password_hash=generate_password_hash("Whatever123!"),
            is_active=True,
            role_id=original_builder_role.id,
        )
        db.session.add(original_builder)

        git_source = GitSource(name="conn", provider_type="github", encrypted_token="x")
        db.session.add(git_source)
        db.session.flush()
        repository = Repository(
            git_source_id=git_source.id,
            full_name="org/repo",
            local_path="/tmp/repo",
            status="ready",
            default_branch="main",
        )
        db.session.add(repository)
        registry_target = RegistryTarget(name="reg", provider_type="dockerhub")
        db.session.add(registry_target)
        version_type = VersionType(name="DEV")
        db.session.add(version_type)
        db.session.flush()
        version = Version(name="svc", version_type_id=version_type.id)
        db.session.add(version)
        db.session.flush()
        builder = Builder(
            name="b1",
            version_id=version.id,
            repository_id=repository.id,
            default_branch="main",
            dockerfile_path="Dockerfile",
            registry_target_id=registry_target.id,
        )
        db.session.add(builder)
        db.session.commit()
        return {
            "version_id": version.id,
            "builder_id": builder.id,
            "original_builder_id": original_builder.id,
        }


def _make_batch(base_entities, full_version_string="DEV.0.0.1.010101010101", requested_by=None, status="success"):
    batch = BuildBatch(
        version_id=base_entities["version_id"],
        full_version_string=full_version_string,
        bump_type="patch",
        status=status,
        requested_by=requested_by,
    )
    db.session.add(batch)
    db.session.flush()
    db.session.add(
        ImageBuild(batch_id=batch.id, builder_id=base_entities["builder_id"], branch_used="main", status="success")
    )
    db.session.flush()
    return batch


def _make_doc(batch, **overrides):
    # Friendly single-name override kept for test readability/minimal churn —
    # VersionDocumentation.object is gone (now a many-to-many via `objects`),
    # so this translates a plain `object="name"` kwarg into a resolved Object
    # row the same way Object.resolve() would.
    object_name = overrides.pop("object", None)
    if object_name is not None:
        from app.models import Object

        obj = Object.query.filter_by(name=object_name).first()
        if obj is None:
            obj = Object(name=object_name)
            db.session.add(obj)
            db.session.flush()
        overrides["objects"] = [obj]

    doc = VersionDocumentation(batch_id=batch.id, **overrides)
    db.session.add(doc)
    db.session.flush()
    return doc


def _make_documented_batch(base_entities, full_version_string="DEV.0.0.1.010101010101", requested_by=None, **doc_overrides):
    """A batch that already has its (auto-created, per the worker) documentation
    row — the realistic state `view_documentation` now requires, since it no
    longer creates one on the fly for a batch that lacks it.
    """
    batch = _make_batch(base_entities, full_version_string=full_version_string, requested_by=requested_by)
    doc = _make_doc(batch, built_by=requested_by, **doc_overrides)
    return batch, doc


def _make_change_type(name="Bug Fix"):
    ct = ChangeType(name=name, is_active=True)
    db.session.add(ct)
    db.session.flush()
    return ct


class TestPermissionGating:
    def test_view_requires_permission(self, noperm_client, app, base_entities):
        with app.app_context():
            batch = _make_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        response = noperm_client.get(f"/documentation/{batch_id}")
        assert response.status_code == 403

    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/documentation/").status_code == 403

    def test_generate_requires_permission(self, noperm_client, app, base_entities):
        with app.app_context():
            batch = _make_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        response = noperm_client.post(f"/documentation/{batch_id}/generate", json={"prompt": "hello"})
        assert response.status_code == 403


class TestDocumentationIndex:
    def test_empty_state(self, doc_client):
        response = doc_client.get("/documentation/")
        assert response.status_code == 200
        assert b"No documented batches yet." in response.data

    def test_lists_only_successful_documented_batches(self, doc_client, app, base_entities):
        with app.app_context():
            documented, _ = _make_documented_batch(
                base_entities, full_version_string="DEV.0.0.1.010101010101", object="checkout"
            )
            _make_batch(base_entities, full_version_string="DEV.0.0.2.020202020202", status="failed")
            db.session.commit()

        response = doc_client.get("/documentation/")
        assert response.status_code == 200
        assert b"DEV.0.0.1.010101010101" in response.data
        assert b"DEV.0.0.2.020202020202" not in response.data
        assert b"checkout" in response.data


class TestDocumentationFilters:
    def _make_two_batches(self, app, base_entities):
        """One fully-documented batch (change type set, built by the seeded
        "original_builder" user, object "checkout") and one still-pending
        batch (no change type, no builder attribution, object "backend") —
        distinguishable by every filter dimension tested below.
        """
        with app.app_context():
            change_type = _make_change_type(name="Feature")
            documented, _ = _make_documented_batch(
                base_entities,
                full_version_string="DEV.0.0.1.010101010101",
                requested_by=base_entities["original_builder_id"],
                object="checkout",
                change_type_id=change_type.id,
            )
            pending, _ = _make_documented_batch(
                base_entities, full_version_string="DEV.0.0.2.020202020202", object="backend"
            )
            db.session.commit()
            return {
                "documented_batch_id": documented.id,
                "pending_batch_id": pending.id,
                "change_type_id": change_type.id,
                "original_builder_id": base_entities["original_builder_id"],
            }

    def test_filters_by_version(self, doc_client, app, base_entities):
        ids = self._make_two_batches(app, base_entities)
        with app.app_context():
            other_version_type = VersionType(name="STAGING")
            db.session.add(other_version_type)
            db.session.flush()
            other_version = Version(name="other-svc", version_type_id=other_version_type.id)
            db.session.add(other_version)
            db.session.commit()

        response = doc_client.get(f"/documentation/?version_id={base_entities['version_id']}")
        assert b"DEV.0.0.1.010101010101" in response.data
        assert b"DEV.0.0.2.020202020202" in response.data

        with app.app_context():
            other_version_id = Version.query.filter_by(name="other-svc").first().id
        response = doc_client.get(f"/documentation/?version_id={other_version_id}")
        assert b"DEV.0.0.1.010101010101" not in response.data
        assert b"DEV.0.0.2.020202020202" not in response.data

    def test_filters_by_version_string_substring(self, doc_client, app, base_entities):
        self._make_two_batches(app, base_entities)
        response = doc_client.get("/documentation/?version_string=0.0.1")
        assert b"DEV.0.0.1.010101010101" in response.data
        assert b"DEV.0.0.2.020202020202" not in response.data

    def test_filters_by_change_type(self, doc_client, app, base_entities):
        ids = self._make_two_batches(app, base_entities)
        response = doc_client.get(f"/documentation/?change_type_id={ids['change_type_id']}")
        assert b"DEV.0.0.1.010101010101" in response.data
        assert b"DEV.0.0.2.020202020202" not in response.data

    def test_filters_by_object_substring(self, doc_client, app, base_entities):
        self._make_two_batches(app, base_entities)
        response = doc_client.get("/documentation/?object=check")
        assert b"DEV.0.0.1.010101010101" in response.data
        assert b"DEV.0.0.2.020202020202" not in response.data

    def test_filters_by_built_by(self, doc_client, app, base_entities):
        ids = self._make_two_batches(app, base_entities)
        response = doc_client.get(f"/documentation/?built_by={ids['original_builder_id']}")
        assert b"DEV.0.0.1.010101010101" in response.data
        assert b"DEV.0.0.2.020202020202" not in response.data

    def test_filters_by_documented_status(self, doc_client, app, base_entities):
        self._make_two_batches(app, base_entities)

        response = doc_client.get("/documentation/?status=documented")
        assert b"DEV.0.0.1.010101010101" in response.data
        assert b"DEV.0.0.2.020202020202" not in response.data

        response = doc_client.get("/documentation/?status=pending")
        assert b"DEV.0.0.1.010101010101" not in response.data
        assert b"DEV.0.0.2.020202020202" in response.data

    def test_filters_by_date_range(self, doc_client, app, base_entities):
        with app.app_context():
            documented, _ = _make_documented_batch(
                base_entities, full_version_string="DEV.0.0.1.010101010101"
            )
            documented.created_at = datetime(2020, 1, 1)
            db.session.commit()

        response = doc_client.get("/documentation/?date_from=2025-01-01")
        assert b"DEV.0.0.1.010101010101" not in response.data

        response = doc_client.get("/documentation/?date_from=2019-01-01&date_to=2020-12-31")
        assert b"DEV.0.0.1.010101010101" in response.data

    def test_combining_filters_narrows_further(self, doc_client, app, base_entities):
        ids = self._make_two_batches(app, base_entities)
        response = doc_client.get(
            f"/documentation/?status=documented&object=backend&change_type_id={ids['change_type_id']}"
        )
        assert b"DEV.0.0.1.010101010101" not in response.data
        assert b"DEV.0.0.2.020202020202" not in response.data

    def test_no_match_shows_filtered_empty_state(self, doc_client, app, base_entities):
        self._make_two_batches(app, base_entities)
        response = doc_client.get("/documentation/?object=nonexistent-object")
        assert b"No documented batches match these filters." in response.data


class TestDocumentationPageDisplays:
    def test_redirects_when_the_batch_has_no_documentation_yet(self, doc_client, app, base_entities):
        """A batch only ever gets a VersionDocumentation row once the worker
        marks it fully successful — the page must not silently create one for
        a batch that (in this test) never went through that path.
        """
        with app.app_context():
            batch = _make_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}", follow_redirects=True)
        assert response.status_code == 200
        assert b"isn&#39;t documented" in response.data or b"isn't documented" in response.data

        with app.app_context():
            assert VersionDocumentation.query.filter_by(batch_id=batch_id).first() is None

    def test_shows_pending_badge_when_change_type_unset(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert response.status_code == 200
        assert b"Documentation Pending" in response.data

    def test_shows_branches_used_from_the_batchs_image_builds(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert b"main" in response.data

    def test_change_type_choices_come_from_the_lookup_table(self, doc_client, app, base_entities):
        with app.app_context():
            _make_change_type("Hot Fix")
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert b"Hot Fix" in response.data

    def test_object_autocomplete_lists_distinct_prior_values(self, doc_client, app, base_entities):
        with app.app_context():
            _make_documented_batch(
                base_entities, full_version_string="DEV.0.0.1.010101010101", object="checkout-flow"
            )
            batch, _ = _make_documented_batch(base_entities, full_version_string="DEV.0.0.2.020202020202")
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert b"checkout-flow" in response.data
        assert b"doc-object-suggestions-data" in response.data

    def test_linked_batches_exclude_the_batch_being_documented(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(base_entities, full_version_string="DEV.0.0.1.010101010101")
            _make_batch(base_entities, full_version_string="DEV.0.0.2.020202020202")
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert response.data.count(b"DEV.0.0.2.020202020202") >= 1
        assert f'value="{batch_id}"'.encode() not in response.data

    def test_linked_batches_exclude_non_successful_batches(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(base_entities, full_version_string="DEV.0.0.1.010101010101")
            _make_batch(base_entities, full_version_string="DEV.0.0.2.020202020202", status="failed")
            _make_batch(base_entities, full_version_string="DEV.0.0.3.030303030303", status="partial_failure")
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert b"DEV.0.0.2.020202020202" not in response.data
        assert b"DEV.0.0.3.030303030303" not in response.data


class TestCommitRangeDisplay:
    def test_shows_not_tracked_for_a_build_without_a_recorded_commit(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert b"not tracked" in response.data

    def test_shows_commit_range_and_list_for_a_tracked_build(self, doc_client, app, base_entities):
        with app.app_context():
            from app.models import ImageBuildCommit

            batch, _ = _make_documented_batch(base_entities)
            image = batch.image_builds[0]
            image.commit_sha = "abcdef1234567890"
            db.session.add(
                ImageBuildCommit(
                    image_build_id=image.id,
                    sha="abcdef1234567890",
                    author_name="Ann",
                    message="fix: something",
                )
            )
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert response.status_code == 200
        assert b"abcdef1" in response.data
        assert b"fix: something" in response.data
        assert b"Ann" in response.data

    def test_shows_the_from_sha_of_the_previous_successful_build(self, doc_client, app, base_entities):
        with app.app_context():
            first_batch = _make_batch(base_entities, full_version_string="DEV.0.0.1.010101010101")
            first_image = first_batch.image_builds[0]
            first_image.commit_sha = "aaaaaaa1111111"
            db.session.commit()

            second_batch, _ = _make_documented_batch(
                base_entities, full_version_string="DEV.0.0.2.020202020202"
            )
            second_image = second_batch.image_builds[0]
            second_image.commit_sha = "bbbbbbb2222222"
            db.session.commit()
            batch_id = second_batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert b"aaaaaaa" in response.data
        assert b"bbbbbbb" in response.data


class TestSavingDocumentation:
    def test_saves_object_description_and_change_type(self, doc_client, app, base_entities):
        with app.app_context():
            change_type = _make_change_type("Cherry Pick")
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id, change_type_id = batch.id, change_type.id

        response = doc_client.post(
            f"/documentation/{batch_id}",
            data={
                "change_type_id": str(change_type_id),
                "new_object_names": "checkout-flow",
                "description": "Fixed the checkout button.",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"saved" in response.data

        with app.app_context():
            doc = VersionDocumentation.query.filter_by(batch_id=batch_id).first()
            assert [o.name for o in doc.objects] == ["checkout-flow"]
            assert doc.description == "Fixed the checkout button."
            assert doc.change_type_id == change_type_id
            assert doc.change_type.name == "Cherry Pick"

    def test_saves_multiple_objects_mixing_existing_and_new(self, doc_client, app, base_entities):
        with app.app_context():
            from app.models import Object

            existing = Object(name="checkout-flow")
            db.session.add(existing)
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id, existing_id = batch.id, existing.id

        doc_client.post(
            f"/documentation/{batch_id}",
            data={
                "change_type_id": "",
                "object_ids": [str(existing_id)],
                "new_object_names": ["payments"],
                "description": "multi-object save",
            },
            follow_redirects=True,
        )

        with app.app_context():
            doc = VersionDocumentation.query.filter_by(batch_id=batch_id).first()
            assert {o.name for o in doc.objects} == {"checkout-flow", "payments"}

    def test_built_by_reflects_the_original_batch_requester(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(
                base_entities, requested_by=base_entities["original_builder_id"]
            )
            db.session.commit()
            batch_id = batch.id

        with app.app_context():
            doc = VersionDocumentation.query.filter_by(batch_id=batch_id).first()
            builder = User.query.get(doc.built_by)
            assert builder.username == "original_builder"

    def test_change_type_is_required_for_completeness(self, doc_client, app, base_entities):
        with app.app_context():
            change_type = _make_change_type("Update")
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id, change_type_id = batch.id, change_type.id

        doc_client.post(
            f"/documentation/{batch_id}",
            data={"change_type_id": "", "new_object_names": "thing", "description": "did the thing"},
        )
        with app.app_context():
            doc = VersionDocumentation.query.filter_by(batch_id=batch_id).first()
            assert doc.is_complete is False

        response = doc_client.post(
            f"/documentation/{batch_id}",
            data={"change_type_id": str(change_type_id), "new_object_names": "thing", "description": "did the thing"},
            follow_redirects=True,
        )
        with app.app_context():
            doc = VersionDocumentation.query.filter_by(batch_id=batch_id).first()
            assert doc.is_complete is True
        assert b"Documentation Complete" in response.data


class TestPromptPreview:
    def test_renders_the_default_template_with_batch_fields_substituted(self, doc_client, app, base_entities, monkeypatch):
        with app.app_context():
            from app.models import PromptTemplate

            db.session.add(
                PromptTemplate(
                    name="Default",
                    template_text="Branches={{branch_names}} Object={{object}} Commits={{commit_messages}}",
                    is_default=True,
                    is_active=True,
                )
            )
            batch, _ = _make_documented_batch(base_entities, object="checkout-flow")
            db.session.commit()
            batch_id = batch.id

        class FakeGitProvider:
            def get_commit_messages(self, local_path, since_ref=None):
                return ["fixed the bug", "added a test"]

        monkeypatch.setattr(
            "app.services.ai.context.provider_for_git_source", lambda source: FakeGitProvider()
        )

        response = doc_client.get(f"/documentation/{batch_id}")
        assert b"Branches=main" in response.data
        assert b"Object=checkout-flow" in response.data
        assert b"fixed the bug" in response.data

    def test_includes_the_additional_description_typed_in_at_trigger_time(
        self, doc_client, app, base_entities
    ):
        with app.app_context():
            from app.models import PromptTemplate

            db.session.add(
                PromptTemplate(
                    name="Default",
                    template_text="Notes={{additional_description}}",
                    is_default=True,
                    is_active=True,
                )
            )
            batch, _ = _make_documented_batch(base_entities)
            batch.additional_description = "please mention the hotfix"
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert b"Notes=please mention the hotfix" in response.data

    def test_blank_prompt_when_no_default_template_exists(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert response.status_code == 200

    def test_inactive_default_template_is_not_used(self, doc_client, app, base_entities):
        with app.app_context():
            from app.models import PromptTemplate

            db.session.add(
                PromptTemplate(
                    name="Inactive Default",
                    template_text="SHOULD_NOT_APPEAR {{branch_names}}",
                    is_default=True,
                    is_active=False,
                )
            )
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        response = doc_client.get(f"/documentation/{batch_id}")
        assert b"SHOULD_NOT_APPEAR" not in response.data


class TestGenerateDescriptionEndpoint:
    def test_successful_generation_returns_description_and_provider(self, doc_client, app, base_entities, monkeypatch):
        with app.app_context():
            batch = _make_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        class FakeProvider:
            def generate_description(self, prompt):
                assert prompt == "a test prompt"
                return "AI-generated description text"

        monkeypatch.setattr(
            "app.blueprints.documentation.routes.get_ai_provider", lambda provider_type=None: FakeProvider()
        )

        response = doc_client.post(
            f"/documentation/{batch_id}/generate",
            json={"prompt": "a test prompt", "provider_type": "qwen"},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["description"] == "AI-generated description text"
        assert data["provider"] == "qwen"

    def test_empty_prompt_is_rejected(self, doc_client, app, base_entities):
        with app.app_context():
            batch = _make_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        response = doc_client.post(f"/documentation/{batch_id}/generate", json={"prompt": "  "})
        assert response.status_code == 400
        assert "error" in response.get_json()

    def test_provider_error_is_surfaced_as_json_error(self, doc_client, app, base_entities, monkeypatch):
        with app.app_context():
            batch = _make_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        def _raise(provider_type=None):
            raise RuntimeError("No Qwen API key configured")

        monkeypatch.setattr("app.blueprints.documentation.routes.get_ai_provider", _raise)

        response = doc_client.post(f"/documentation/{batch_id}/generate", json={"prompt": "hello"})
        assert response.status_code == 400
        data = response.get_json()
        assert "No Qwen API key configured" in data["error"]

        with app.app_context():
            from app.models import ErrorLog

            error_log = ErrorLog.query.filter_by(source="documentation.generate_description").first()
            assert error_log is not None
            assert "No Qwen API key configured" in error_log.message
            assert data["error_log_url"] == f"/logs/errors/{error_log.id}"

    def test_unknown_batch_404s(self, doc_client):
        response = doc_client.post(
            "/documentation/00000000-0000-0000-0000-000000000000/generate", json={"prompt": "hello"}
        )
        assert response.status_code == 404


class TestSavingAIDescription:
    def test_saving_with_a_fresh_draft_persists_it(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        doc_client.post(
            f"/documentation/{batch_id}",
            data={
                "change_type_id": "",
                "new_object_names": "",
                "description": "final description",
                "ai_description": "raw ai draft text",
                "ai_provider_used": "qwen",
            },
        )

        with app.app_context():
            doc = VersionDocumentation.query.filter_by(batch_id=batch_id).first()
            assert doc.ai_description == "raw ai draft text"
            assert doc.ai_provider_used == "qwen"
            assert doc.description == "final description"

    def test_resaving_without_a_new_draft_keeps_the_prior_one(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        doc_client.post(
            f"/documentation/{batch_id}",
            data={
                "change_type_id": "",
                "new_object_names": "",
                "description": "first save",
                "ai_description": "original ai draft",
                "ai_provider_used": "qwen",
            },
        )
        doc_client.post(
            f"/documentation/{batch_id}",
            data={"change_type_id": "", "new_object_names": "", "description": "first save, but edited by hand"},
        )

        with app.app_context():
            doc = VersionDocumentation.query.filter_by(batch_id=batch_id).first()
            assert doc.ai_description == "original ai draft"
            assert doc.ai_provider_used == "qwen"
            assert doc.description == "first save, but edited by hand"

    def test_description_saved_without_ever_generating_has_no_ai_fields(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(base_entities)
            db.session.commit()
            batch_id = batch.id

        doc_client.post(
            f"/documentation/{batch_id}",
            data={"change_type_id": "", "new_object_names": "", "description": "hand-written, no AI involved"},
        )

        with app.app_context():
            doc = VersionDocumentation.query.filter_by(batch_id=batch_id).first()
            assert doc.ai_description is None
            assert doc.ai_provider_used is None


class TestLinkedBatches:
    def test_linking_batches_creates_version_link_rows(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(base_entities, full_version_string="DEV.0.0.1.010101010101")
            linked_1 = _make_batch(base_entities, full_version_string="DEV.0.0.2.020202020202")
            linked_2 = _make_batch(base_entities, full_version_string="DEV.0.0.3.030303030303")
            db.session.commit()
            batch_id, linked_id_1, linked_id_2 = batch.id, linked_1.id, linked_2.id

        doc_client.post(
            f"/documentation/{batch_id}",
            data={
                "change_type_id": "",
                "new_object_names": "",
                "description": "",
                "linked_batch_ids": [str(linked_id_1), str(linked_id_2)],
            },
        )

        with app.app_context():
            links = VersionLink.query.filter_by(batch_id=batch_id).all()
            linked_ids = {link.linked_batch_id for link in links}
            assert linked_ids == {linked_id_1, linked_id_2}

    def test_unchecking_a_link_on_resubmit_removes_it(self, doc_client, app, base_entities):
        with app.app_context():
            batch, _ = _make_documented_batch(base_entities, full_version_string="DEV.0.0.1.010101010101")
            linked_1 = _make_batch(base_entities, full_version_string="DEV.0.0.2.020202020202")
            linked_2 = _make_batch(base_entities, full_version_string="DEV.0.0.3.030303030303")
            db.session.commit()
            batch_id, linked_id_1, linked_id_2 = batch.id, linked_1.id, linked_2.id

        doc_client.post(
            f"/documentation/{batch_id}",
            data={
                "change_type_id": "",
                "new_object_names": "",
                "description": "",
                "linked_batch_ids": [str(linked_id_1), str(linked_id_2)],
            },
        )
        doc_client.post(
            f"/documentation/{batch_id}",
            data={
                "change_type_id": "",
                "new_object_names": "",
                "description": "",
                "linked_batch_ids": [str(linked_id_1)],
            },
        )

        with app.app_context():
            links = VersionLink.query.filter_by(batch_id=batch_id).all()
            assert {link.linked_batch_id for link in links} == {linked_id_1}


class TestGatherBatchAiContext:
    def test_concatenates_commit_messages_per_builder_branch(self, app, base_entities, monkeypatch):
        from app.services.ai.context import gather_batch_ai_context

        with app.app_context():
            batch = _make_batch(base_entities)
            db.session.commit()

            class FakeGitProvider:
                def get_commit_messages(self, local_path, since_ref=None):
                    assert since_ref is None
                    return ["did a thing", "did another thing"]

            monkeypatch.setattr(
                "app.services.ai.context.provider_for_git_source", lambda source: FakeGitProvider()
            )

            context = gather_batch_ai_context(batch)
            assert context["branch_names"] == "main"
            assert "did a thing" in context["commit_messages"]
            assert "b1 @ main" in context["commit_messages"]

    def test_skips_a_builder_whose_repo_cant_be_read(self, app, base_entities, monkeypatch):
        from app.services.ai.context import gather_batch_ai_context

        with app.app_context():
            batch = _make_batch(base_entities)
            db.session.commit()

            def _raise(source):
                raise RuntimeError("no local clone")

            monkeypatch.setattr("app.services.ai.context.provider_for_git_source", _raise)

            context = gather_batch_ai_context(batch)
            assert context["branch_names"] == "main"
            assert context["commit_messages"] == ""
