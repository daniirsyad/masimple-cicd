"""Tests for the Discord bot interaction dispatch
(app/services/discord/worker.py).

Same white-box style as tests/test_telegram_worker.py, adapted for
Discord's event-driven (not polled) shape: _handle_interaction() is called
directly with hand-built INTERACTION_CREATE payload dicts, DiscordClient's
outbound methods monkeypatched at the class level, rather than spinning a
real Gateway connection or hitting the real Discord API. _build_executor's
.submit() is monkeypatched to run synchronously so the 4 AI-backed
build-prefill interactions can be asserted deterministically (ack before
compute_build_prefill, followup after) without waiting on a real thread.
"""
import uuid

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Builder,
    ChangeType,
    DeploymentExecution,
    DeploymentManifest,
    DeploymentRun,
    DeploymentServer,
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
import app.services.bot_shared as bot_shared_module
import app.services.discord.worker as discord_worker_module
from app.services.discord.client import DiscordClient
from app.services.discord.worker import _group_hash, _handle_interaction

APPLICATION_COMMAND_TYPE = 2
MESSAGE_COMPONENT_TYPE = 3


def _command_interaction(name, discord_user_id, interaction_id="int-1", token="tok-1"):
    return {
        "id": interaction_id,
        "token": token,
        "type": APPLICATION_COMMAND_TYPE,
        "data": {"name": name},
        "user": {"id": discord_user_id},
    }


def _button_interaction(custom_id, discord_user_id, interaction_id="int-2", token="tok-2"):
    return {
        "id": interaction_id,
        "token": token,
        "type": MESSAGE_COMPONENT_TYPE,
        "data": {"custom_id": custom_id},
        "user": {"id": discord_user_id},
    }


def _select_interaction(select_id, value, discord_user_id, interaction_id="int-3", token="tok-3"):
    return {
        "id": interaction_id,
        "token": token,
        "type": MESSAGE_COMPONENT_TYPE,
        "data": {"custom_id": select_id, "values": [value]},
        "user": {"id": discord_user_id},
    }


def _enable_bot(app):
    from app.utils.crypto import encrypt
    from app.utils.system_config import get_system_config

    with app.app_context():
        config = get_system_config()
        config.discord_bot_commands_enabled = True
        config.encrypted_discord_bot_token = encrypt("bot-token")
        config.discord_application_id = "app-123"
        db.session.commit()


def _patch_client(monkeypatch):
    """Returns a dict of lists capturing every outbound call the worker makes."""
    calls = {"responded": [], "followups": [], "commands_registered": []}

    monkeypatch.setattr(
        DiscordClient,
        "respond_to_interaction",
        lambda self, interaction_id, interaction_token, response_type, data=None: calls["responded"].append(
            {"interaction_id": interaction_id, "response_type": response_type, "data": data}
        ),
    )
    monkeypatch.setattr(
        DiscordClient,
        "edit_original_response",
        lambda self, application_id, interaction_token, content, components=None: calls["followups"].append(
            {"application_id": application_id, "content": content, "components": components}
        ),
    )
    monkeypatch.setattr(
        DiscordClient,
        "bulk_register_commands",
        lambda self, application_id, commands: calls["commands_registered"].append((application_id, commands)),
    )
    return calls


def _run_build_executor_synchronously(monkeypatch):
    monkeypatch.setattr(discord_worker_module._build_executor, "submit", lambda fn, *a, **k: fn(*a, **k))


def _dispatch(app, client, interaction):
    with app.app_context():
        _handle_interaction(app, client, interaction)
        db.session.remove()


@pytest.fixture
def client():
    return DiscordClient("bot-token")


@pytest.fixture
def base_entities(app):
    with app.app_context():
        git_source = GitSource(name="conn", provider_type="github", encrypted_token="x")
        db.session.add(git_source)
        db.session.flush()
        repository = Repository(git_source_id=git_source.id, full_name="org/repo", local_path="/tmp/repo", status="ready")
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


def _make_builder(base_entities, name="b1", default_branch="main"):
    builder = Builder(
        name=name,
        version_id=base_entities["version_id"],
        repository_id=base_entities["repository_id"],
        default_branch=default_branch,
        registry_target_id=base_entities["registry_target_id"],
    )
    db.session.add(builder)
    db.session.flush()
    return builder


def _make_deployment_server(name="srv"):
    server = DeploymentServer(name=name, connection_type="kube")
    db.session.add(server)
    db.session.flush()
    return server


def _make_manifest(name, servers, group_name=None, order=0, is_active=True):
    manifest = DeploymentManifest(name=name, yaml_content="image: nginx", group_name=group_name, order=order, is_active=is_active)
    manifest.target_servers = servers
    db.session.add(manifest)
    db.session.flush()
    return manifest


def _mark_currently_deployed(manifest, server):
    run = DeploymentRun(action="deploy", status="success")
    db.session.add(run)
    db.session.flush()
    db.session.add(
        DeploymentExecution(run_id=run.id, manifest_id=manifest.id, server_id=server.id, status="success", rendered_yaml="image: nginx")
    )


@pytest.fixture
def linked_user(app):
    with app.app_context():
        permissions = [Permission(code="workflow.run", description="run"), Permission(code="workflow.manage", description="manage")]
        db.session.add_all(permissions)
        role = Role(name="DiscordWorkerRole", description="test")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()
        user = User(username="discord_worker_user", password_hash=generate_password_hash("x"), is_active=True, role_id=role.id, discord_user_id="555")
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def unprivileged_linked_user(app):
    with app.app_context():
        role = Role(name="NoPermDiscordRole", description="test")
        db.session.add(role)
        db.session.flush()
        user = User(username="discord_noperm_user", password_hash=generate_password_hash("x"), is_active=True, role_id=role.id, discord_user_id="777")
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def deploy_linked_user(app):
    with app.app_context():
        permissions = [
            Permission(code="deployment.deploy", description="deploy"),
            Permission(code="deployment.update", description="update"),
            Permission(code="deployment.stop", description="stop"),
            Permission(code="deployment.restart", description="restart"),
            Permission(code="deployment_server.manage", description="manage servers"),
        ]
        db.session.add_all(permissions)
        role = Role(name="DiscordDeployRole", description="test")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()
        user = User(username="discord_deploy_user", password_hash=generate_password_hash("x"), is_active=True, role_id=role.id, discord_user_id="600")
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def build_linked_user(app):
    with app.app_context():
        permissions = [Permission(code="builder.build", description="build"), Permission(code="builder.manage", description="manage")]
        db.session.add_all(permissions)
        role = Role(name="DiscordBuildRole", description="test")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()
        user = User(username="discord_build_user", password_hash=generate_password_hash("x"), is_active=True, role_id=role.id, discord_user_id="650")
        db.session.add(user)
        db.session.commit()
        return user.id


class TestUnlinkedAndUnrecognized:
    def test_unlinked_user_is_told_to_link_account(self, app, client, monkeypatch):
        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _command_interaction("run", "99999"))

        assert len(calls["responded"]) == 1
        assert "isn't linked" in calls["responded"][0]["data"]["content"]

    def test_unrecognized_command_name(self, app, client, linked_user, monkeypatch):
        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _command_interaction("nope", "555"))

        assert "Unrecognized command" in calls["responded"][0]["data"]["content"]

    def test_invalid_uuid_payload_is_rejected(self, app, client, linked_user, monkeypatch):
        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _button_interaction("run:not-a-uuid", "555"))

        assert "Invalid selection" in calls["responded"][0]["data"]["content"]


class TestRunCommandAndSelect:
    def test_denies_a_user_without_workflow_run_permission(self, app, client, unprivileged_linked_user, monkeypatch):
        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _command_interaction("run", "777"))

        assert "don't have permission" in calls["responded"][0]["data"]["content"]

    def test_lists_only_active_workflows_as_select_options(self, app, client, linked_user, monkeypatch):
        with app.app_context():
            db.session.add(Workflow(name="Active WF", is_active=True))
            db.session.add(Workflow(name="Archived WF", is_active=False))
            db.session.commit()

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _command_interaction("run", "555"))

        options = calls["responded"][0]["data"]["components"][0]["components"][0]["options"]
        assert len(options) == 1
        assert options[0]["label"] == "Active WF"

    def test_select_pick_enqueues_a_run_and_logs_activity(self, app, client, linked_user, monkeypatch):
        with app.app_context():
            workflow = Workflow(name="Deploy Everything", is_active=True)
            db.session.add(workflow)
            db.session.commit()
            workflow_id = workflow.id

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _select_interaction("run_select", f"run:{workflow_id}", "555"))

        assert "Queued a run" in calls["responded"][0]["data"]["content"]
        with app.app_context():
            assert WorkflowRun.query.filter_by(workflow_id=workflow_id).count() == 1
            from app.models import ActivityLog

            entry = ActivityLog.query.filter_by(action="RUN_WORKFLOW").one()
            assert "via Discord" in entry.description

    def test_confirmation_includes_a_check_status_button_not_typed_text(self, app, client, linked_user, monkeypatch):
        """The old copy told the user to type /status manually — now a real
        Button does it, reusing the exact status:<uuid> action token
        /status's own select already dispatches through (no new handler).
        """
        with app.app_context():
            workflow = Workflow(name="Deploy Everything", is_active=True)
            db.session.add(workflow)
            db.session.commit()
            workflow_id = workflow.id

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _select_interaction("run_select", f"run:{workflow_id}", "555"))

        with app.app_context():
            run_id = WorkflowRun.query.filter_by(workflow_id=workflow_id).one().id

        button = calls["responded"][0]["data"]["components"][0]["components"][0]
        assert button["custom_id"] == f"status:{run_id}"
        assert "Use /status" not in calls["responded"][0]["data"]["content"]

        # Tapping it re-enters the same handler /status's own select does.
        calls2 = _patch_client(monkeypatch)
        _dispatch(app, client, _button_interaction(f"status:{run_id}", "555"))
        assert "Deploy Everything" in calls2["responded"][0]["data"]["content"]


class TestStatusCommandAndSelect:
    def test_status_select_shows_the_users_own_run(self, app, client, linked_user, monkeypatch):
        with app.app_context():
            workflow = Workflow(name="WF", is_active=True)
            db.session.add(workflow)
            db.session.flush()
            run = WorkflowRun(workflow_id=workflow.id, status="success", triggered_by=linked_user)
            db.session.add(run)
            db.session.commit()
            run_id = run.id

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _select_interaction("status_select", f"status:{run_id}", "555"))

        assert "success" in calls["responded"][0]["data"]["content"]

    def test_status_select_rejects_someone_elses_run(self, app, client, linked_user, monkeypatch):
        with app.app_context():
            other_role = Role(name="OtherRole")
            db.session.add(other_role)
            db.session.flush()
            other = User(username="other", password_hash=generate_password_hash("x"), is_active=True, role_id=other_role.id)
            db.session.add(other)
            db.session.flush()
            workflow = Workflow(name="WF", is_active=True)
            db.session.add(workflow)
            db.session.flush()
            run = WorkflowRun(workflow_id=workflow.id, status="success", triggered_by=other.id)
            db.session.add(run)
            db.session.commit()
            run_id = run.id

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _select_interaction("status_select", f"status:{run_id}", "555"))

        assert "Couldn't find that run" in calls["responded"][0]["data"]["content"]


class TestDeployCommandAndSelect:
    def test_lists_only_accessible_active_manifests_with_target_servers(self, app, client, deploy_linked_user, monkeypatch):
        with app.app_context():
            server = _make_deployment_server()
            _make_manifest("m1", [server])
            _make_manifest("m2", [])  # no target servers — excluded
            db.session.commit()

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _command_interaction("deploy", "600"))

        options = calls["responded"][0]["data"]["components"][0]["components"][0]["options"]
        assert [o["label"] for o in options] == ["m1"]

    def test_select_pick_enqueues_a_deploy_run_and_logs_activity(self, app, client, deploy_linked_user, monkeypatch):
        with app.app_context():
            server = _make_deployment_server()
            manifest = _make_manifest("m1", [server])
            db.session.commit()
            manifest_id = manifest.id

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _select_interaction("deploy_select", f"deploy:{manifest_id}", "600"))

        assert "queued" in calls["responded"][0]["data"]["content"]
        with app.app_context():
            assert DeploymentRun.query.filter_by(action="deploy").count() == 1

    def test_rejects_a_disabled_manifest(self, app, client, deploy_linked_user, monkeypatch):
        with app.app_context():
            server = _make_deployment_server()
            manifest = _make_manifest("m1", [server], is_active=False)
            db.session.commit()
            manifest_id = manifest.id

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _select_interaction("deploy_select", f"deploy:{manifest_id}", "600"))

        assert "disabled manifest" in calls["responded"][0]["data"]["content"]

    def test_stop_on_a_disabled_but_still_live_manifest_still_works(self, app, client, deploy_linked_user, monkeypatch):
        with app.app_context():
            server = _make_deployment_server()
            manifest = _make_manifest("m1", [server], is_active=False)
            _mark_currently_deployed(manifest, server)
            db.session.commit()
            manifest_id = manifest.id

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _select_interaction("stop_select", f"stop:{manifest_id}", "600"))

        assert "queued" in calls["responded"][0]["data"]["content"]

    def test_group_action_resolves_and_enqueues_every_member(self, app, client, deploy_linked_user, monkeypatch):
        with app.app_context():
            server = _make_deployment_server()
            _make_manifest("m1", [server], group_name="grp", order=0)
            _make_manifest("m2", [server], group_name="grp", order=1)
            db.session.commit()
            group_hash = _group_hash("grp")

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _button_interaction(f"gdeploy:{group_hash}", "600"))

        assert "2 execution" in calls["responded"][0]["data"]["content"]


class TestReviewCommandAndSelect:
    def _make_step_run(self, app, base_entities, monkeypatch, triggered_by):
        import app.services.workflow.worker as workflow_worker_module
        from app.services.workflow.worker import _tick as workflow_tick
        from app.services.workflow.worker import enqueue_workflow_run

        with app.app_context():
            workflow = Workflow(name="Reviewed Workflow", is_active=True)
            db.session.add(workflow)
            db.session.flush()
            builder = _make_builder(base_entities)
            step = WorkflowStep(
                workflow_id=workflow.id, order=0, step_type="build", on_failure="stop",
                auto_generate_build_metadata=True, require_review_before_build=True,
            )
            db.session.add(step)
            db.session.flush()
            step.selected_builders = [builder]
            db.session.commit()
            change_type_id = base_entities["change_type_id"]

            monkeypatch.setattr(
                workflow_worker_module, "compute_build_prefill",
                lambda builder_branches, additional_description=None: {
                    "bump_type": "patch", "matched_objects": [], "new_object_names": ["api"],
                    "change_type_id": change_type_id, "description": "Draft.", "commit_count": 1,
                },
            )

            run = enqueue_workflow_run(workflow, triggered_by=triggered_by)
            workflow_tick(app)
            step_run = WorkflowStepRun.query.filter_by(workflow_run_id=run.id).one()
            return step_run.id

    def test_approve_review_enqueues_a_batch_and_logs_activity(self, app, client, linked_user, base_entities, monkeypatch):
        step_run_id = self._make_step_run(app, base_entities, monkeypatch, triggered_by=linked_user)

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _button_interaction(f"approve_review:{step_run_id}", "555"))

        assert "Approved" in calls["responded"][0]["data"]["content"]
        with app.app_context():
            from app.models import BuildBatch

            step_run = WorkflowStepRun.query.get(step_run_id)
            assert step_run.batch_id is not None
            assert BuildBatch.query.get(step_run.batch_id) is not None

    def test_reject_review_fails_the_step(self, app, client, linked_user, base_entities, monkeypatch):
        step_run_id = self._make_step_run(app, base_entities, monkeypatch, triggered_by=linked_user)

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _button_interaction(f"reject_review:{step_run_id}", "555"))

        assert "rejected" in calls["responded"][0]["data"]["content"]
        with app.app_context():
            assert WorkflowStepRun.query.get(step_run_id).status == "failed"


class TestBuildDeferredFlow:
    """The 4 build-prefill interactions must ack (deferred) BEFORE calling
    the AI-backed compute_build_prefill, and PATCH the followup AFTER — see
    app/services/discord/worker.py's module docstring for why. _build_
    executor.submit is monkeypatched to run synchronously so this ordering
    is assertable without any real threading/timing.
    """

    def _confident_prefill(self, change_type_id):
        return {
            "bump_type": "minor", "matched_objects": [], "new_object_names": ["api"],
            "change_type_id": change_type_id, "description": "Draft.", "commit_count": 3,
        }

    def _unconfident_prefill(self):
        return {"bump_type": None, "matched_objects": [], "new_object_names": [], "change_type_id": None, "description": "", "commit_count": 0}

    def test_build_select_acks_before_prefill_and_follows_up_after(self, app, client, build_linked_user, base_entities, monkeypatch):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        call_order = []
        monkeypatch.setattr(
            DiscordClient, "respond_to_interaction",
            lambda self, *a, **k: call_order.append("ack"),
        )
        monkeypatch.setattr(
            bot_shared_module, "compute_build_prefill",
            lambda *a, **k: call_order.append("prefill") or self._confident_prefill(base_entities["change_type_id"]),
        )
        monkeypatch.setattr(
            DiscordClient, "edit_original_response",
            lambda self, *a, **k: call_order.append("followup"),
        )
        _run_build_executor_synchronously(monkeypatch)

        _dispatch(app, client, _select_interaction("build_select", f"build:{builder_id}", "650"))

        assert call_order == ["ack", "prefill", "followup"]

    def test_build_select_shows_confirm_buttons_when_confident(self, app, client, build_linked_user, base_entities, monkeypatch):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        calls = _patch_client(monkeypatch)
        monkeypatch.setattr(bot_shared_module, "compute_build_prefill", lambda *a, **k: self._confident_prefill(base_entities["change_type_id"]))
        _run_build_executor_synchronously(monkeypatch)

        _dispatch(app, client, _select_interaction("build_select", f"build:{builder_id}", "650"))

        followup = calls["followups"][0]
        assert "minor" in followup["content"]
        buttons = followup["components"][0]["components"]
        assert buttons[0]["custom_id"] == f"bconfirm:{builder_id}"
        assert buttons[1]["custom_id"] == f"bcancel:{builder_id}"

    def test_build_select_points_to_web_ui_when_not_confident(self, app, client, build_linked_user, base_entities, monkeypatch):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        calls = _patch_client(monkeypatch)
        monkeypatch.setattr(bot_shared_module, "compute_build_prefill", lambda *a, **k: self._unconfident_prefill())
        _run_build_executor_synchronously(monkeypatch)

        _dispatch(app, client, _select_interaction("build_select", f"build:{builder_id}", "650"))

        assert "web UI" in calls["followups"][0]["content"]

    def test_build_confirm_enqueues_a_batch_and_logs_activity(self, app, client, build_linked_user, base_entities, monkeypatch):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        calls = _patch_client(monkeypatch)
        monkeypatch.setattr(bot_shared_module, "compute_build_prefill", lambda *a, **k: self._confident_prefill(base_entities["change_type_id"]))
        _run_build_executor_synchronously(monkeypatch)

        _dispatch(app, client, _button_interaction(f"bconfirm:{builder_id}", "650"))

        assert "Build queued" in calls["followups"][0]["content"]
        with app.app_context():
            from app.models import BuildBatch

            assert BuildBatch.query.filter_by(requested_by=build_linked_user).count() == 1

    def test_build_cancel_is_immediate_not_deferred(self, app, client, build_linked_user, base_entities, monkeypatch):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        calls = _patch_client(monkeypatch)
        _dispatch(app, client, _button_interaction(f"bcancel:{builder_id}", "650"))

        assert "cancelled" in calls["responded"][0]["data"]["content"]
        assert calls["followups"] == []

    def test_group_build_confirm_enqueues_one_batch_covering_every_member(self, app, client, build_linked_user, base_entities, monkeypatch):
        with app.app_context():
            b1 = _make_builder(base_entities, name="b1")
            b2 = _make_builder(base_entities, name="b2")
            b1.group_name = "grp"
            b2.group_name = "grp"
            db.session.commit()
            group_hash = _group_hash("grp")

        calls = _patch_client(monkeypatch)
        monkeypatch.setattr(bot_shared_module, "compute_build_prefill", lambda *a, **k: self._confident_prefill(base_entities["change_type_id"]))
        _run_build_executor_synchronously(monkeypatch)

        _dispatch(app, client, _button_interaction(f"gbconfirm:{group_hash}", "650"))

        assert "2 builder" in calls["followups"][0]["content"]
        with app.app_context():
            from app.models import BuildBatch

            batch = BuildBatch.query.filter_by(requested_by=build_linked_user).one()
            assert batch.image_builds.count() == 2
