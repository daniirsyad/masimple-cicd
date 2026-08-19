"""Tests for the Telegram bot command worker (app/services/telegram/worker.py).

Same white-box style as tests/test_workflow_worker.py: _tick() is called
directly with TelegramNotifier's outbound methods monkeypatched, rather than
spinning a real thread or hitting the real Telegram API.
"""
import uuid

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    ActivityLog,
    BuildBatch,
    ChangeType,
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
from app.services.telegram.client import TelegramNotifier
from app.services.telegram.worker import _tick
from app.utils.crypto import encrypt
from app.utils.system_config import get_system_config


def _message_update(update_id, chat_id, text):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


def _callback_update(update_id, chat_id, data, callback_id="cbq-1"):
    return {
        "update_id": update_id,
        "callback_query": {"id": callback_id, "message": {"chat": {"id": chat_id}}, "data": data},
    }


def _enable_bot(app):
    with app.app_context():
        config = get_system_config()
        config.telegram_bot_commands_enabled = True
        config.encrypted_telegram_bot_token = encrypt("bot-token")
        db.session.commit()


def _patch_notifier(monkeypatch):
    """Returns a dict of lists capturing every outbound call the worker makes."""
    calls = {"sent": [], "answered": [], "commands_set": []}

    monkeypatch.setattr(
        TelegramNotifier,
        "send_message",
        lambda self, chat_id, text, reply_markup=None: calls["sent"].append(
            {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
        ),
    )
    monkeypatch.setattr(
        TelegramNotifier,
        "answer_callback_query",
        lambda self, callback_query_id, text=None: calls["answered"].append(
            {"callback_query_id": callback_query_id, "text": text}
        ),
    )
    monkeypatch.setattr(
        TelegramNotifier, "set_my_commands", lambda self, commands: calls["commands_set"].append(commands)
    )
    return calls


def _patch_updates(monkeypatch, updates):
    monkeypatch.setattr(TelegramNotifier, "get_updates", lambda self, offset=None, timeout=25: updates)


@pytest.fixture
def base_entities(app):
    """Same shape as tests/test_workflow_worker.py's own fixture — the
    minimum real Builder/Version/ChangeType setup needed for
    approve_awaiting_step() to actually resolve builders and enqueue a
    BuildBatch, not just manipulate WorkflowStepRun rows in isolation.
    """
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


def _make_awaiting_review_step_run(app, base_entities, monkeypatch, triggered_by):
    """Drives a real workflow (via the orchestrator's own _tick, same as
    tests/test_workflow_worker.py) up to a WorkflowStepRun sitting in
    status="awaiting_review", so the Telegram review callbacks below have
    something real to approve/reject.
    """
    import app.services.workflow.worker as workflow_worker_module
    from app.models import Builder
    from app.services.workflow.worker import _tick as workflow_tick
    from app.services.workflow.worker import enqueue_workflow_run

    with app.app_context():
        workflow = Workflow(name="Reviewed Workflow", is_active=True)
        db.session.add(workflow)
        db.session.flush()
        builder = Builder(
            name="b1",
            version_id=base_entities["version_id"],
            repository_id=base_entities["repository_id"],
            default_branch="main",
            registry_target_id=base_entities["registry_target_id"],
        )
        db.session.add(builder)
        db.session.flush()
        step = WorkflowStep(
            workflow_id=workflow.id,
            order=0,
            step_type="build",
            on_failure="stop",
            auto_generate_build_metadata=True,
            require_review_before_build=True,
        )
        db.session.add(step)
        db.session.flush()
        step.selected_builders = [builder]
        db.session.commit()
        change_type_id = base_entities["change_type_id"]

        monkeypatch.setattr(
            workflow_worker_module,
            "compute_build_prefill",
            lambda builder_branches, additional_description=None: {
                "bump_type": "patch",
                "matched_objects": [],
                "new_object_names": ["api"],
                "change_type_id": change_type_id,
                "description": "Draft.",
                "commit_count": 1,
            },
        )

        run = enqueue_workflow_run(workflow, triggered_by=triggered_by)
        workflow_tick(app)

        step_run = WorkflowStepRun.query.filter_by(workflow_run_id=run.id).one()
        return workflow.id, run.id, step_run.id


@pytest.fixture
def linked_user(app):
    """A user with a linked Telegram chat ID and workflow.run + workflow.manage
    (the latter so Workflow.is_accessible_to short-circuits to True without
    needing allowed_roles set up)."""
    with app.app_context():
        permissions = [Permission(code="workflow.run", description="run"), Permission(code="workflow.manage", description="manage")]
        db.session.add_all(permissions)
        role = Role(name="TelegramWorkerRole", description="test")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="telegram_worker_user",
            password_hash=generate_password_hash("x"),
            is_active=True,
            role_id=role.id,
            telegram_chat_id="555",
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def unprivileged_linked_user(app):
    """A linked user with no permissions at all."""
    with app.app_context():
        role = Role(name="NoPermTelegramRole", description="test")
        db.session.add(role)
        db.session.flush()
        user = User(
            username="telegram_noperm_user",
            password_hash=generate_password_hash("x"),
            is_active=True,
            role_id=role.id,
            telegram_chat_id="777",
        )
        db.session.add(user)
        db.session.commit()
        return user.id


class TestTickGating:
    def test_returns_false_and_makes_no_api_calls_when_disabled(self, app, monkeypatch):
        monkeypatch.setattr(
            TelegramNotifier, "get_updates", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not poll"))
        )
        with app.app_context():
            config = get_system_config()
            config.telegram_bot_commands_enabled = False
            db.session.commit()

        assert _tick(app) is False

    def test_returns_false_when_no_bot_token_configured(self, app, monkeypatch):
        monkeypatch.setattr(
            TelegramNotifier, "get_updates", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not poll"))
        )
        with app.app_context():
            config = get_system_config()
            config.telegram_bot_commands_enabled = True
            config.encrypted_telegram_bot_token = None
            db.session.commit()

        assert _tick(app) is False

    def test_registers_commands_once_when_enabled(self, app, monkeypatch):
        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [])

        assert _tick(app) is True
        assert _tick(app) is True
        assert len(calls["commands_set"]) == 1


class TestRunCommand:
    def test_unlinked_chat_is_told_to_link_account(self, app, monkeypatch):
        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_message_update(1, "99999", "/run")])

        _tick(app)

        assert len(calls["sent"]) == 1
        assert "isn't linked" in calls["sent"][0]["text"]

    def test_denies_a_user_without_workflow_run_permission(self, app, unprivileged_linked_user, monkeypatch):
        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_message_update(1, "777", "/run")])

        _tick(app)

        assert len(calls["sent"]) == 1
        assert "don't have permission" in calls["sent"][0]["text"]

    def test_lists_only_active_workflows_as_inline_buttons(self, app, linked_user, monkeypatch):
        with app.app_context():
            db.session.add(Workflow(name="Active WF", is_active=True))
            db.session.add(Workflow(name="Archived WF", is_active=False))
            db.session.commit()

        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_message_update(1, "555", "/run")])

        _tick(app)

        assert len(calls["sent"]) == 1
        buttons = calls["sent"][0]["reply_markup"]["inline_keyboard"]
        labels = [row[0]["text"] for row in buttons]
        assert labels == ["Active WF"]


class TestRunCallback:
    def test_enqueues_a_run_and_logs_activity_attributed_to_the_telegram_user(self, app, linked_user, monkeypatch):
        with app.app_context():
            workflow = Workflow(name="Deploy Everything", is_active=True)
            db.session.add(workflow)
            db.session.commit()
            workflow_id = workflow.id

        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_callback_update(1, "555", f"run:{workflow_id}")])

        _tick(app)

        with app.app_context():
            run = WorkflowRun.query.filter_by(workflow_id=workflow_id).one()
            assert run.triggered_by == linked_user
            assert run.status == "queued"

            entry = ActivityLog.query.filter_by(action="RUN_WORKFLOW", target_id=str(run.id)).one()
            assert entry.user_id == linked_user

        assert any("Run queued" in a["text"] for a in calls["answered"] if a["text"])
        assert any("Queued a run" in s["text"] for s in calls["sent"])

    def test_rejects_a_run_callback_for_a_disabled_workflow(self, app, linked_user, monkeypatch):
        with app.app_context():
            workflow = Workflow(name="Disabled WF", is_active=False)
            db.session.add(workflow)
            db.session.commit()
            workflow_id = workflow.id

        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_callback_update(1, "555", f"run:{workflow_id}")])

        _tick(app)

        with app.app_context():
            assert WorkflowRun.query.filter_by(workflow_id=workflow_id).count() == 0
        assert any("no longer available" in a["text"] for a in calls["answered"] if a["text"])


class TestStatusCommandAndCallback:
    def test_status_command_lists_only_the_users_own_runs(self, app, linked_user, monkeypatch):
        with app.app_context():
            workflow = Workflow(name="WF1", is_active=True)
            db.session.add(workflow)
            db.session.flush()
            run = WorkflowRun(workflow_id=workflow.id, status="running", triggered_by=linked_user)
            db.session.add(run)
            db.session.commit()
            run_id = run.id

        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_message_update(1, "555", "/status")])

        _tick(app)

        assert len(calls["sent"]) == 1
        buttons = calls["sent"][0]["reply_markup"]["inline_keyboard"]
        assert buttons[0][0]["callback_data"] == f"status:{run_id}"

    def test_status_callback_reports_the_run_and_rejects_someone_elses_run(
        self, app, linked_user, unprivileged_linked_user, monkeypatch
    ):
        with app.app_context():
            workflow = Workflow(name="WF1", is_active=True)
            db.session.add(workflow)
            db.session.flush()
            run = WorkflowRun(workflow_id=workflow.id, status="success", triggered_by=linked_user)
            db.session.add(run)
            db.session.commit()
            run_id = run.id

        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_callback_update(1, "555", f"status:{run_id}")])
        _tick(app)
        assert any("WF1" in s["text"] and "success" in s["text"] for s in calls["sent"])

        # A different linked user hitting the same run's callback_data (e.g. a
        # stale/forwarded message) must not see someone else's run.
        calls["sent"].clear()
        calls["answered"].clear()
        _patch_updates(monkeypatch, [_callback_update(2, "777", f"status:{run_id}")])
        _tick(app)
        assert calls["sent"] == []
        assert any("not found" in (a["text"] or "").lower() for a in calls["answered"])


class TestReviewCommandAndCallback:
    def test_review_command_denies_a_user_without_workflow_run_permission(
        self, app, unprivileged_linked_user, monkeypatch
    ):
        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_message_update(1, "777", "/review")])

        _tick(app)

        assert len(calls["sent"]) == 1
        assert "don't have permission" in calls["sent"][0]["text"]

    def test_review_command_lists_nothing_when_no_step_is_awaiting_review(self, app, linked_user, monkeypatch):
        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_message_update(1, "555", "/review")])

        _tick(app)

        assert len(calls["sent"]) == 1
        assert "Nothing awaiting review" in calls["sent"][0]["text"]

    def test_review_command_lists_and_review_callback_shows_the_suggestion(
        self, app, linked_user, base_entities, monkeypatch
    ):
        workflow_id, run_id, step_run_id = _make_awaiting_review_step_run(app, base_entities, monkeypatch, linked_user)

        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_message_update(1, "555", "/review")])
        _tick(app)

        assert len(calls["sent"]) == 1
        buttons = calls["sent"][0]["reply_markup"]["inline_keyboard"]
        assert buttons[0][0]["callback_data"] == f"review:{step_run_id}"

        calls["sent"].clear()
        _patch_updates(monkeypatch, [_callback_update(2, "555", f"review:{step_run_id}")])
        _tick(app)

        assert len(calls["sent"]) == 1
        text = calls["sent"][0]["text"]
        assert "Reviewed Workflow" in text
        assert "patch" in text
        keyboard = calls["sent"][0]["reply_markup"]["inline_keyboard"]
        assert keyboard[0][0]["callback_data"] == f"approve_review:{step_run_id}"
        assert keyboard[0][1]["callback_data"] == f"reject_review:{step_run_id}"

    def test_approve_review_callback_enqueues_a_batch_and_logs_activity(
        self, app, linked_user, base_entities, monkeypatch
    ):
        workflow_id, run_id, step_run_id = _make_awaiting_review_step_run(app, base_entities, monkeypatch, linked_user)

        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_callback_update(1, "555", f"approve_review:{step_run_id}")])
        _tick(app)

        with app.app_context():
            step_run = WorkflowStepRun.query.get(step_run_id)
            assert step_run.status == "running"
            assert step_run.batch_id is not None
            batch = BuildBatch.query.get(step_run.batch_id)
            assert batch.bump_type == "patch"

            entry = ActivityLog.query.filter_by(action="APPROVE_WORKFLOW_BUILD_STEP", target_id=str(run_id)).one()
            assert entry.user_id == linked_user

        assert any("Approved" in (a["text"] or "") for a in calls["answered"])
        assert any("Approved and queued" in s["text"] for s in calls["sent"])

    def test_reject_review_callback_fails_the_step_and_stops_the_run(
        self, app, linked_user, base_entities, monkeypatch
    ):
        workflow_id, run_id, step_run_id = _make_awaiting_review_step_run(app, base_entities, monkeypatch, linked_user)

        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_callback_update(1, "555", f"reject_review:{step_run_id}")])
        _tick(app)

        with app.app_context():
            step_run = WorkflowStepRun.query.get(step_run_id)
            assert step_run.status == "failed"
            assert step_run.error == "Rejected by reviewer."
            run = WorkflowRun.query.get(run_id)
            assert run.status == "failed"

            entry = ActivityLog.query.filter_by(action="REJECT_WORKFLOW_BUILD_STEP", target_id=str(run_id)).one()
            assert entry.user_id == linked_user

        assert any("Rejected" in (a["text"] or "") for a in calls["answered"])

    def test_approve_review_callback_denies_a_user_without_workflow_run_permission(
        self, app, linked_user, unprivileged_linked_user, base_entities, monkeypatch
    ):
        workflow_id, run_id, step_run_id = _make_awaiting_review_step_run(app, base_entities, monkeypatch, linked_user)

        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        _patch_updates(monkeypatch, [_callback_update(1, "777", f"approve_review:{step_run_id}")])
        _tick(app)

        with app.app_context():
            step_run = WorkflowStepRun.query.get(step_run_id)
            assert step_run.status == "awaiting_review"
        assert any("don't have permission" in (a["text"] or "") for a in calls["answered"])

    def test_stale_review_callback_is_a_no_op_not_a_crash(self, app, linked_user, base_entities, monkeypatch):
        workflow_id, run_id, step_run_id = _make_awaiting_review_step_run(app, base_entities, monkeypatch, linked_user)

        _enable_bot(app)
        calls = _patch_notifier(monkeypatch)
        # Approve it once directly (simulating the web UI acting on it first).
        _patch_updates(monkeypatch, [_callback_update(1, "555", f"approve_review:{step_run_id}")])
        _tick(app)
        calls["answered"].clear()
        calls["sent"].clear()

        # Tapping the (now-stale) button again must not double-enqueue a batch.
        _patch_updates(monkeypatch, [_callback_update(2, "555", f"reject_review:{step_run_id}")])
        _tick(app)

        with app.app_context():
            step_run = WorkflowStepRun.query.get(step_run_id)
            assert step_run.status == "running"  # unchanged from the earlier approval
        assert any("no longer pending" in (a["text"] or "") for a in calls["answered"])


class TestOffsetPersistence:
    def test_last_update_id_advances_and_is_used_as_the_next_offset(self, app, monkeypatch):
        _enable_bot(app)
        _patch_notifier(monkeypatch)

        offsets_seen = []

        def fake_get_updates(self, offset=None, timeout=25):
            offsets_seen.append(offset)
            if offset is None or offset <= 1:
                return [_message_update(41, "99999", "/start")]
            return []

        monkeypatch.setattr(TelegramNotifier, "get_updates", fake_get_updates)

        _tick(app)
        with app.app_context():
            assert get_system_config().telegram_last_update_id == 41

        _tick(app)
        assert offsets_seen[-1] == 42
