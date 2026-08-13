import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Builder,
    ChangeType,
    DeploymentManifest,
    GitSource,
    Permission,
    RegistryTarget,
    Repository,
    Role,
    User,
    Version,
    VersionType,
    Workflow,
    WorkflowRun,
    WorkflowStep,
    WorkflowStepRun,
)

WORKFLOW_PASSWORD = "WorkflowPass123!"
LIMITED_PASSWORD = "LimitedPass123!"

WORKFLOW_PERMISSIONS = ["workflow.view", "workflow.manage", "workflow.run"]


@pytest.fixture
def workflow_user(app):
    with app.app_context():
        permissions = [Permission(code=code, description=code) for code in WORKFLOW_PERMISSIONS]
        db.session.add_all(permissions)
        role = Role(name="WorkflowAdmin", description="Test workflow admin role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="workflow_test", password_hash=generate_password_hash(WORKFLOW_PASSWORD), is_active=True, role_id=role.id
        )
        db.session.add(user)
        db.session.commit()
        return {"user_id": user.id, "role_id": role.id}


@pytest.fixture
def workflow_client(client, workflow_user):
    client.post("/login", data={"username": "workflow_test", "password": WORKFLOW_PASSWORD}, follow_redirects=True)
    return client


@pytest.fixture
def view_only_role(app):
    """workflow.view only — no workflow.manage/run — for permission-gating checks."""
    with app.app_context():
        permission = Permission.query.filter_by(code="workflow.view").first()
        if permission is None:
            permission = Permission(code="workflow.view", description="workflow.view")
            db.session.add(permission)
            db.session.flush()
        role = Role(name="WorkflowViewer", description="View-only workflow role")
        role.permissions = [permission]
        db.session.add(role)
        db.session.commit()
        return role.id


@pytest.fixture
def view_only_client(client, app, view_only_role):
    with app.app_context():
        user = User(
            username="viewonly_test", password_hash=generate_password_hash(LIMITED_PASSWORD), is_active=True, role_id=view_only_role
        )
        db.session.add(user)
        db.session.commit()
    client.post("/login", data={"username": "viewonly_test", "password": LIMITED_PASSWORD}, follow_redirects=True)
    return client


@pytest.fixture
def base_entities(app):
    with app.app_context():
        git_source = GitSource(name="conn", provider_type="github", encrypted_token="x")
        db.session.add(git_source)
        db.session.flush()
        repository = Repository(
            git_source_id=git_source.id, full_name="org/repo", local_path="/tmp/repo", status="ready"
        )
        db.session.add(repository)
        registry_target = RegistryTarget(name="reg", provider_type="dockerhub")
        db.session.add(registry_target)
        version_type = VersionType(name="DEV")
        db.session.add(version_type)
        db.session.flush()
        version = Version(name="svc", version_type_id=version_type.id)
        db.session.add(version)
        change_type = ChangeType(name="Bug Fix")
        db.session.add(change_type)
        db.session.commit()
        return {
            "repository_id": repository.id,
            "registry_target_id": registry_target.id,
            "version_id": version.id,
            "change_type_id": change_type.id,
        }


def _make_builder(base_entities, name="b1", group_name=None):
    builder = Builder(
        name=name,
        version_id=base_entities["version_id"],
        repository_id=base_entities["repository_id"],
        default_branch="main",
        group_name=group_name,
        registry_target_id=base_entities["registry_target_id"],
    )
    db.session.add(builder)
    db.session.flush()
    return builder


def _make_manifest(name="m1", group_name=None):
    manifest = DeploymentManifest(name=name, yaml_content="kind: Pod", group_name=group_name)
    db.session.add(manifest)
    db.session.flush()
    return manifest


def _make_workflow(name="wf"):
    workflow = Workflow(name=name)
    db.session.add(workflow)
    db.session.commit()
    return workflow


class TestPermissionGating:
    def test_index_requires_workflow_view(self, client):
        response = client.get("/workflows/")
        assert response.status_code in (302, 401)

    def test_add_build_step_requires_workflow_manage(self, view_only_client, app):
        with app.app_context():
            workflow = _make_workflow()
            workflow_id = workflow.id
        response = view_only_client.post(f"/workflows/{workflow_id}/steps/build", data={})
        assert response.status_code == 403

    def test_run_requires_workflow_run(self, view_only_client, app):
        with app.app_context():
            workflow = _make_workflow()
            workflow_id = workflow.id
        response = view_only_client.post(f"/workflows/{workflow_id}/run")
        assert response.status_code == 403


class TestAccessGating:
    def test_restricted_workflow_is_403_for_a_user_without_the_role(self, view_only_client, app):
        # workflow.manage always grants full access regardless of
        # allowed_roles (see Workflow.is_accessible_to) — this needs a user
        # with only workflow.view, not the workflow_client fixture.
        with app.app_context():
            other_role = Role(name="Other", description="not allowed")
            db.session.add(other_role)
            db.session.flush()
            workflow = Workflow(name="restricted")
            workflow.allowed_roles = [other_role]
            db.session.add(workflow)
            db.session.commit()
            workflow_id = workflow.id

        response = view_only_client.get(f"/workflows/{workflow_id}")
        assert response.status_code == 403


class TestIndexRendering:
    def test_renders_with_an_existing_workflow_listed(self, workflow_client, app):
        with app.app_context():
            workflow = Workflow(name="Existing WF", description="desc")
            db.session.add(workflow)
            db.session.commit()

        response = workflow_client.get("/workflows/")
        assert response.status_code == 200
        assert b"Existing WF" in response.data


def _form_html(html, action_substring):
    """The <form ...>...</form> block whose action contains `action_substring`
    — used to check a specific form's own fields, not just "csrf_token
    appears somewhere on the page" (which every WTForms-backed form on the
    same page would already satisfy, masking a plain-HTML form's missing
    token).
    """
    start = html.index(action_substring)
    form_start = html.rindex("<form", 0, start)
    form_end = html.index("</form>", start)
    return html[form_start:form_end]


class TestDetailPageIncludesCsrfTokens:
    """Regression test: the Run/Delete Workflow/Delete Step forms were plain
    HTML forms (not WTForms-backed with hidden_tag()) and were missing their
    csrf_token field entirely, so submitting them failed with "The CSRF
    token is missing" outside of TestingConfig — which disables CSRF here,
    so a plain functional POST test wouldn't have caught this; only
    inspecting the rendered HTML does.
    """

    def test_run_delete_workflow_and_delete_step_forms_include_csrf_token(
        self, workflow_client, app, base_entities
    ):
        with app.app_context():
            builder = _make_builder(base_entities)
            workflow = _make_workflow()
            step = WorkflowStep(
                workflow_id=workflow.id,
                order=0,
                step_type="build",
                on_failure="stop",
                bump_type="patch",
                object="svc",
            )
            step.selected_builders = [builder]
            db.session.add(step)
            db.session.commit()
            workflow_id, step_id = workflow.id, step.id

        response = workflow_client.get(f"/workflows/{workflow_id}")
        assert response.status_code == 200
        html = response.data.decode()

        run_form = _form_html(html, f'action="/workflows/{workflow_id}/run"')
        assert 'name="csrf_token"' in run_form

        delete_workflow_form = _form_html(html, f'action="/workflows/{workflow_id}/delete"')
        assert 'name="csrf_token"' in delete_workflow_form

        delete_step_form = _form_html(html, f'action="/workflows/{workflow_id}/steps/{step_id}/delete"')
        assert 'name="csrf_token"' in delete_step_form


class TestCreateWorkflow:
    def test_creates_workflow_and_redirects_to_detail(self, workflow_client, app):
        response = workflow_client.post(
            "/workflows/create",
            data={"create-workflow-name": "Release Pipeline", "create-workflow-is_active": "y"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            assert Workflow.query.filter_by(name="Release Pipeline").first() is not None


class TestAddBuildStep:
    def test_group_selection_succeeds(self, workflow_client, app, base_entities):
        with app.app_context():
            _make_builder(base_entities, "a", group_name="prod")
            workflow = _make_workflow()
            workflow_id = workflow.id

        response = workflow_client.post(
            f"/workflows/{workflow_id}/steps/build",
            data={
                "group_names": ["prod"],
                "bump_type": "patch",
                "change_type_id": str(base_entities["change_type_id"]),
                "object": "svc",
                "on_failure": "stop",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            step = WorkflowStep.query.filter_by(workflow_id=workflow_id).first()
            assert step is not None
            assert [g.group_name for g in step.selected_groups] == ["prod"]

    def test_no_selection_shows_error_and_creates_no_step(self, workflow_client, app, base_entities):
        with app.app_context():
            workflow = _make_workflow()
            workflow_id = workflow.id

        response = workflow_client.post(
            f"/workflows/{workflow_id}/steps/build",
            data={
                "bump_type": "patch",
                "change_type_id": str(base_entities["change_type_id"]),
                "object": "svc",
                "on_failure": "stop",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Select at least one Builder group or individual Builder" in response.data
        with app.app_context():
            assert WorkflowStep.query.filter_by(workflow_id=workflow_id).count() == 0

    def test_grouped_builder_is_not_a_valid_individual_choice(self, workflow_client, app, base_entities):
        """A Builder that belongs to a group is only reachable by selecting
        that group — submitting its id via builder_ids (the ungrouped-only
        picker) must be rejected, not silently accepted."""
        with app.app_context():
            grouped = _make_builder(base_entities, "a", group_name="prod")
            workflow = _make_workflow()
            workflow_id = workflow.id
            grouped_id = str(grouped.id)

        response = workflow_client.post(
            f"/workflows/{workflow_id}/steps/build",
            data={
                "builder_ids": [grouped_id],
                "bump_type": "patch",
                "change_type_id": str(base_entities["change_type_id"]),
                "object": "svc",
                "on_failure": "stop",
            },
        )
        assert response.status_code == 200  # re-rendered with a validation error, not a redirect
        with app.app_context():
            assert WorkflowStep.query.filter_by(workflow_id=workflow_id).count() == 0


class _FakePreviewGitProvider:
    def __init__(self, messages=None):
        self.messages = messages or []

    def sync_repo(self, local_path, branch, repo_name=None):
        pass

    def get_commits(self, local_path, since_ref=None, until_ref=None, limit=None):
        return [{"sha": f"sha-{i}", "message": m} for i, m in enumerate(self.messages)]


class TestBuildStepPreview:
    def test_requires_at_least_one_selection(self, workflow_client, app, base_entities):
        with app.app_context():
            workflow = _make_workflow()
            workflow_id = workflow.id

        response = workflow_client.post(f"/workflows/{workflow_id}/steps/build/preview", data={})
        assert response.status_code == 400

    def test_rejects_a_selection_spanning_multiple_versions(self, workflow_client, app, base_entities):
        with app.app_context():
            builder1 = _make_builder(base_entities, "a")
            other_version_type = VersionType(name="STAGING")
            db.session.add(other_version_type)
            db.session.flush()
            other_version = Version(name="other", version_type_id=other_version_type.id)
            db.session.add(other_version)
            db.session.flush()
            builder2 = Builder(
                name="b",
                version_id=other_version.id,
                repository_id=base_entities["repository_id"],
                default_branch="main",
                registry_target_id=base_entities["registry_target_id"],
            )
            db.session.add(builder2)
            workflow = _make_workflow()
            db.session.commit()
            workflow_id, builder1_id, builder2_id = workflow.id, builder1.id, builder2.id

        response = workflow_client.post(
            f"/workflows/{workflow_id}/steps/build/preview",
            data={"builder_ids": [str(builder1_id), str(builder2_id)]},
        )
        assert response.status_code == 400

    def test_resolves_group_and_individual_selection_and_returns_prefill(
        self, workflow_client, app, base_entities, monkeypatch
    ):
        with app.app_context():
            _make_builder(base_entities, "a", group_name="prod")
            solo = _make_builder(base_entities, "solo")
            workflow = _make_workflow()
            db.session.commit()
            workflow_id, solo_id = workflow.id, solo.id

        fake_git = _FakePreviewGitProvider(messages=["feat: add thing", "fix: bug"])
        monkeypatch.setattr("app.services.build.prefill.provider_for_git_source", lambda source: fake_git)
        monkeypatch.setattr(
            "app.services.build.prefill.suggest_metadata",
            lambda messages, additional_description=None: {
                "matched_object_names": [],
                "new_object_names": ["backend"],
                "change_type_name": None,
                "description": "Added a thing and fixed a bug.",
            },
        )

        response = workflow_client.post(
            f"/workflows/{workflow_id}/steps/build/preview",
            data={"group_names": ["prod"], "builder_ids": [str(solo_id)]},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["bump_type"] == "minor"
        assert data["object"] == "backend"
        assert data["description"] == "Added a thing and fixed a bug."
        assert data["commit_count"] == 4  # 2 commits x 2 resolved builders


class TestAddDeployStep:
    def test_item_selection_succeeds(self, workflow_client, app):
        with app.app_context():
            manifest = _make_manifest("solo")
            workflow = _make_workflow()
            workflow_id = workflow.id
            manifest_id = str(manifest.id)

        response = workflow_client.post(
            f"/workflows/{workflow_id}/steps/deploy",
            data={"manifest_ids": [manifest_id], "on_failure": "continue"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            step = WorkflowStep.query.filter_by(workflow_id=workflow_id).first()
            assert step is not None
            assert [m.name for m in step.selected_manifests] == ["solo"]
            assert step.on_failure == "continue"


class TestDeleteStep:
    def test_step_with_run_history_cannot_be_deleted(self, workflow_client, app, base_entities):
        with app.app_context():
            workflow = _make_workflow()
            builder = _make_builder(base_entities)
            step = WorkflowStep(
                workflow_id=workflow.id,
                order=0,
                step_type="build",
                on_failure="stop",
                bump_type="patch",
                change_type_id=base_entities["change_type_id"],
                object="svc",
            )
            db.session.add(step)
            db.session.flush()
            step.selected_builders = [builder]
            run = WorkflowRun(workflow_id=workflow.id, status="success")
            db.session.add(run)
            db.session.flush()
            db.session.add(
                WorkflowStepRun(
                    workflow_run_id=run.id, workflow_step_id=step.id, step_order=0, step_type="build", status="success"
                )
            )
            db.session.commit()
            workflow_id, step_id = workflow.id, step.id

        response = workflow_client.post(
            f"/workflows/{workflow_id}/steps/{step_id}/delete", follow_redirects=True
        )
        assert response.status_code == 200
        assert b"recorded run" in response.data
        with app.app_context():
            assert WorkflowStep.query.get(step_id) is not None

    def test_never_run_step_can_be_deleted(self, workflow_client, app, base_entities):
        with app.app_context():
            workflow = _make_workflow()
            builder = _make_builder(base_entities)
            step = WorkflowStep(
                workflow_id=workflow.id,
                order=0,
                step_type="build",
                on_failure="stop",
                bump_type="patch",
                change_type_id=base_entities["change_type_id"],
                object="svc",
            )
            db.session.add(step)
            db.session.flush()
            step.selected_builders = [builder]
            db.session.commit()
            workflow_id, step_id = workflow.id, step.id

        response = workflow_client.post(
            f"/workflows/{workflow_id}/steps/{step_id}/delete", follow_redirects=True
        )
        assert response.status_code == 200
        with app.app_context():
            assert WorkflowStep.query.get(step_id) is None


class TestDeleteWorkflow:
    def test_workflow_with_run_history_cannot_be_deleted(self, workflow_client, app):
        with app.app_context():
            workflow = _make_workflow()
            db.session.add(WorkflowRun(workflow_id=workflow.id, status="success"))
            db.session.commit()
            workflow_id = workflow.id

        response = workflow_client.post(f"/workflows/{workflow_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        assert b"recorded run" in response.data
        with app.app_context():
            assert Workflow.query.get(workflow_id) is not None


class TestRunWorkflow:
    def test_run_with_no_steps_shows_error(self, workflow_client, app):
        with app.app_context():
            workflow = _make_workflow()
            workflow_id = workflow.id

        response = workflow_client.post(f"/workflows/{workflow_id}/run", follow_redirects=True)
        assert response.status_code == 200
        assert b"Add at least one step" in response.data
        with app.app_context():
            assert WorkflowRun.query.filter_by(workflow_id=workflow_id).count() == 0

    def test_run_creates_a_workflow_run_and_redirects_to_it(self, workflow_client, app, base_entities):
        with app.app_context():
            workflow = _make_workflow()
            builder = _make_builder(base_entities)
            step = WorkflowStep(
                workflow_id=workflow.id,
                order=0,
                step_type="build",
                on_failure="stop",
                bump_type="patch",
                change_type_id=base_entities["change_type_id"],
                object="svc",
            )
            db.session.add(step)
            db.session.flush()
            step.selected_builders = [builder]
            db.session.commit()
            workflow_id = workflow.id

        response = workflow_client.post(f"/workflows/{workflow_id}/run", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            run = WorkflowRun.query.filter_by(workflow_id=workflow_id).first()
            assert run is not None
            assert run.status == "queued"

    def test_inactive_workflow_cannot_be_run(self, workflow_client, app, base_entities):
        with app.app_context():
            workflow = _make_workflow()
            workflow.is_active = False
            builder = _make_builder(base_entities)
            step = WorkflowStep(
                workflow_id=workflow.id,
                order=0,
                step_type="build",
                on_failure="stop",
                bump_type="patch",
                change_type_id=base_entities["change_type_id"],
                object="svc",
            )
            db.session.add(step)
            db.session.flush()
            step.selected_builders = [builder]
            db.session.commit()
            workflow_id = workflow.id

        response = workflow_client.post(f"/workflows/{workflow_id}/run", follow_redirects=True)
        assert response.status_code == 200
        assert b"is inactive" in response.data
        with app.app_context():
            assert WorkflowRun.query.filter_by(workflow_id=workflow_id).count() == 0


class TestReorderSteps:
    def test_reorders_steps_by_submitted_id_order(self, workflow_client, app, base_entities):
        with app.app_context():
            workflow = _make_workflow()
            builder = _make_builder(base_entities)
            step_kwargs = dict(
                workflow_id=workflow.id,
                step_type="build",
                on_failure="stop",
                bump_type="patch",
                change_type_id=base_entities["change_type_id"],
                object="svc",
            )
            first = WorkflowStep(order=0, **step_kwargs)
            second = WorkflowStep(order=1, **step_kwargs)
            db.session.add_all([first, second])
            db.session.flush()
            first.selected_builders = [builder]
            second.selected_builders = [builder]
            db.session.commit()
            workflow_id, first_id, second_id = workflow.id, first.id, second.id

        response = workflow_client.post(
            f"/workflows/{workflow_id}/steps/reorder",
            json={"step_ids": [str(second_id), str(first_id)]},
        )
        assert response.status_code == 200
        with app.app_context():
            assert WorkflowStep.query.get(second_id).order == 0
            assert WorkflowStep.query.get(first_id).order == 1


class TestViewRunPageRendering:
    """The run detail page's own GET/template hasn't been exercised with a
    populated (non-empty) step_runs list — including one with a real
    deployment_run_url link and one with an error — by any of the tests
    above (they only ever see it via a fresh run's redirect, with zero
    WorkflowStepRuns yet). Renders both states for real via the orchestrator
    itself, not by hand-building step_runs, so a template/Jinja bug in
    _step_run_links or the "error" branch would actually surface as a 500.
    """

    def test_renders_a_successful_build_step_and_a_failed_build_step(self, workflow_client, app, base_entities):
        from app.models import BuildBatch
        from app.services.workflow.worker import _tick, enqueue_workflow_run

        with app.app_context():
            workflow = _make_workflow()
            builder = _make_builder(base_entities)
            empty_step = WorkflowStep(
                workflow_id=workflow.id,
                order=0,
                step_type="build",
                on_failure="continue",
                bump_type="patch",
                change_type_id=base_entities["change_type_id"],
                object="svc",
            )
            db.session.add(empty_step)
            db.session.flush()
            empty_step.selected_builders = []  # resolves to nothing -> _fail_step, no BuildBatch
            real_step = WorkflowStep(
                workflow_id=workflow.id,
                order=1,
                step_type="build",
                on_failure="stop",
                bump_type="patch",
                change_type_id=base_entities["change_type_id"],
                object="svc",
            )
            db.session.add(real_step)
            db.session.flush()
            real_step.selected_builders = [builder]
            db.session.commit()

            run = enqueue_workflow_run(workflow, triggered_by=None)
            run_id = run.id
            _tick(app)  # step 0 fails immediately (nothing resolved), continues to step 1

            step_run = WorkflowStepRun.query.filter_by(workflow_run_id=run_id, step_order=1).one()
            BuildBatch.query.get(step_run.batch_id).status = "success"
            db.session.commit()

            _tick(app)  # step 1 finishes, run completes

        response = workflow_client.get(f"/workflows/runs/{run_id}")
        assert response.status_code == 200
        assert b"No builders resolved" in response.data
        assert b"View build" in response.data

        status_response = workflow_client.get(f"/workflows/runs/{run_id}/status")
        assert status_response.status_code == 200
        payload = status_response.get_json()
        assert payload["status"] == "completed_with_failures"
        assert len(payload["step_runs"]) == 2


class TestBuilderDeleteGuardedByWorkflowReference:
    def test_builder_individually_selected_by_a_step_cannot_be_deleted(self, workflow_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            workflow = _make_workflow()
            step = WorkflowStep(
                workflow_id=workflow.id,
                order=0,
                step_type="build",
                on_failure="stop",
                bump_type="patch",
                change_type_id=base_entities["change_type_id"],
                object="svc",
            )
            db.session.add(step)
            db.session.flush()
            step.selected_builders = [builder]
            db.session.commit()
            builder_id = builder.id

        # builder.manage is needed for the delete route itself, builder.view
        # for the page it redirects to afterward.
        with app.app_context():
            user = User.query.filter_by(username="workflow_test").first()
            for code in ("builder.manage", "builder.view"):
                permission = Permission.query.filter_by(code=code).first()
                if permission is None:
                    permission = Permission(code=code, description=code)
                    db.session.add(permission)
                user.role.permissions.append(permission)
            db.session.commit()

        response = workflow_client.post(f"/builders/{builder_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        assert b"individually selected" in response.data
        with app.app_context():
            assert Builder.query.get(builder_id) is not None
