import pytest
import requests
from werkzeug.security import generate_password_hash

from app.extensions import db
from flask import url_for

from app.models import BuildBatch, ChangeType, DeploymentRun, Role, User, Version, VersionType, Workflow, WorkflowRun, WorkflowStep, WorkflowStepRun
from app.services.discord.client import DiscordClient
from app.services.discord.helpers import (
    notify_awaiting_review,
    notify_build_finished,
    notify_deploy_finished,
    notify_run_finished,
    notify_security_contact,
    notify_user,
)
from app.utils.crypto import encrypt
from app.utils.system_config import get_system_config


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", content=b"{}"):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text
        self.content = content

    def json(self):
        return self._json_data


class TestDiscordClient:
    def test_create_dm_channel_posts_to_users_me_channels(self, monkeypatch):
        captured = {}

        def fake_request(method, url, json=None, headers=None, timeout=None):
            captured.update(method=method, url=url, json=json, headers=headers, timeout=timeout)
            return _FakeResponse(status_code=200, json_data={"id": "chan-1"})

        monkeypatch.setattr(requests, "request", fake_request)

        channel_id = DiscordClient("test-token").create_dm_channel("999")

        assert channel_id == "chan-1"
        assert captured["method"] == "POST"
        assert captured["url"] == "https://discord.com/api/v10/users/@me/channels"
        assert captured["json"] == {"recipient_id": "999"}
        assert captured["headers"]["Authorization"] == "Bot test-token"

    def test_send_dm_message_opens_a_channel_then_posts_a_message(self, monkeypatch):
        calls = []

        def fake_request(method, url, json=None, headers=None, timeout=None):
            calls.append((method, url, json))
            if url.endswith("/users/@me/channels"):
                return _FakeResponse(status_code=200, json_data={"id": "chan-1"})
            return _FakeResponse(status_code=200)

        monkeypatch.setattr(requests, "request", fake_request)

        DiscordClient("test-token").send_dm_message("999", "hello", components=[{"type": 1}])

        assert calls[0] == ("POST", "https://discord.com/api/v10/users/@me/channels", {"recipient_id": "999"})
        assert calls[1] == (
            "POST",
            "https://discord.com/api/v10/channels/chan-1/messages",
            {"content": "hello", "components": [{"type": 1}]},
        )

    def test_raises_on_non_2xx_response(self, monkeypatch):
        monkeypatch.setattr(
            requests, "request", lambda *a, **k: _FakeResponse(status_code=400, json_data={"message": "bad request"})
        )
        with pytest.raises(RuntimeError, match="bad request"):
            DiscordClient("test-token").create_dm_channel("999")

    def test_raises_on_network_error(self, monkeypatch):
        def raise_it(*args, **kwargs):
            raise requests.RequestException("boom")

        monkeypatch.setattr(requests, "request", raise_it)
        with pytest.raises(RuntimeError, match="Could not reach Discord"):
            DiscordClient("test-token").create_dm_channel("999")

    def test_requires_a_bot_token(self):
        with pytest.raises(ValueError):
            DiscordClient(None)


@pytest.fixture
def discord_user(app):
    with app.app_context():
        role = Role(name="DiscordTestRole", description="test")
        db.session.add(role)
        db.session.flush()

        user = User(
            username="discord_test",
            password_hash=generate_password_hash("DiscordPass123!"),
            is_active=True,
            role_id=role.id,
            discord_user_id="123456789012345678",
        )
        db.session.add(user)
        db.session.commit()
        return user.id


class TestNotifyUser:
    def test_returns_false_and_sends_nothing_when_user_has_no_discord_id(self, app, monkeypatch):
        called = {"count": 0}
        monkeypatch.setattr(
            DiscordClient, "send_dm_message", lambda self, *a, **k: called.update(count=called["count"] + 1)
        )

        with app.app_context():
            role = Role(name="NoDiscordRole")
            db.session.add(role)
            db.session.flush()
            user = User(username="no_discord", password_hash=generate_password_hash("x"), is_active=True, role_id=role.id)
            db.session.add(user)
            db.session.commit()

            assert notify_user(user, "hi") is False
        assert called["count"] == 0

    def test_returns_false_when_notifications_are_disabled(self, app, discord_user):
        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = False
            config.encrypted_discord_bot_token = encrypt("bot-token")
            db.session.commit()

            user = User.query.get(discord_user)
            assert notify_user(user, "hi") is False

    def test_returns_false_when_no_bot_token_configured(self, app, discord_user):
        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = None
            db.session.commit()

            user = User.query.get(discord_user)
            assert notify_user(user, "hi") is False

    def test_sends_and_returns_true_when_fully_configured(self, app, discord_user, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            DiscordClient,
            "send_dm_message",
            lambda self, discord_user_id, content, components=None: sent.update(
                discord_user_id=discord_user_id, content=content
            ),
        )

        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            db.session.commit()

            user = User.query.get(discord_user)
            assert notify_user(user, "hello there") is True

        assert sent == {"discord_user_id": "123456789012345678", "content": "hello there"}

    def test_a_discord_failure_is_swallowed_not_raised(self, app, discord_user, monkeypatch):
        def raise_it(self, discord_user_id, content, components=None):
            raise RuntimeError("Discord is down")

        monkeypatch.setattr(DiscordClient, "send_dm_message", raise_it)

        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            db.session.commit()

            user = User.query.get(discord_user)
            # Must not raise.
            assert notify_user(user, "hello") is False

    def test_returns_false_for_none_user(self, app):
        with app.app_context():
            assert notify_user(None, "hi") is False


class TestNotifyRunFinished:
    def test_notifies_the_triggering_users_own_dm(self, app, discord_user, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            DiscordClient,
            "send_dm_message",
            lambda self, discord_user_id, content, components=None: sent.update(
                discord_user_id=discord_user_id, content=content
            ),
        )

        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            db.session.commit()

            workflow = Workflow(name="Deploy Everything", is_active=True)
            db.session.add(workflow)
            db.session.flush()
            run = WorkflowRun(workflow_id=workflow.id, status="success", triggered_by=discord_user)
            db.session.add(run)
            db.session.commit()

            assert notify_run_finished(run) is True

        assert sent["discord_user_id"] == "123456789012345678"
        assert "Deploy Everything" in sent["content"]
        assert "succeeded" in sent["content"]

    def test_returns_false_when_run_has_no_triggering_user(self, app):
        with app.app_context():
            workflow = Workflow(name="No Trigger WF", is_active=True)
            db.session.add(workflow)
            db.session.flush()
            run = WorkflowRun(workflow_id=workflow.id, status="success", triggered_by=None)
            db.session.add(run)
            db.session.commit()

            assert notify_run_finished(run) is False

    def test_returns_false_when_notifications_are_disabled(self, app, discord_user):
        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = False
            config.encrypted_discord_bot_token = encrypt("bot-token")
            db.session.commit()

            workflow = Workflow(name="WF", is_active=True)
            db.session.add(workflow)
            db.session.flush()
            run = WorkflowRun(workflow_id=workflow.id, status="failed", triggered_by=discord_user)
            db.session.add(run)
            db.session.commit()

            assert notify_run_finished(run) is False


class TestNotifyAwaitingReview:
    def _make_step_run(self, triggered_by):
        workflow = Workflow(name="Deploy Everything", is_active=True)
        db.session.add(workflow)
        db.session.flush()
        step = WorkflowStep(workflow_id=workflow.id, order=0, step_type="build", on_failure="stop")
        db.session.add(step)
        db.session.flush()
        run = WorkflowRun(workflow_id=workflow.id, status="running", triggered_by=triggered_by)
        db.session.add(run)
        db.session.flush()
        change_type = ChangeType(name="Bug Fix")
        db.session.add(change_type)
        db.session.flush()
        step_run = WorkflowStepRun(
            workflow_run_id=run.id,
            workflow_step_id=step.id,
            step_order=0,
            step_type="build",
            status="awaiting_review",
            suggested_bump_type="minor",
            suggested_change_type_id=change_type.id,
            suggested_object_names="api, billing",
            suggested_description="Draft.",
        )
        db.session.add(step_run)
        db.session.commit()
        return step_run

    def test_notifies_the_triggering_user_with_approve_reject_buttons(self, app, discord_user, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            DiscordClient,
            "send_dm_message",
            lambda self, discord_user_id, content, components=None: sent.update(
                discord_user_id=discord_user_id, content=content, components=components
            ),
        )

        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            db.session.commit()

            step_run = self._make_step_run(triggered_by=discord_user)
            assert notify_awaiting_review(step_run) is True

        assert sent["discord_user_id"] == "123456789012345678"
        assert "Deploy Everything" in sent["content"]
        assert "minor" in sent["content"]
        assert "Bug Fix" in sent["content"]
        buttons = sent["components"][0]["components"]
        assert buttons[0]["custom_id"] == f"approve_review:{step_run.id}"
        assert buttons[1]["custom_id"] == f"reject_review:{step_run.id}"

    def test_returns_false_when_run_has_no_triggering_user(self, app):
        with app.app_context():
            step_run = self._make_step_run(triggered_by=None)
            assert notify_awaiting_review(step_run) is False


class TestNotifySecurityContact:
    def test_returns_false_when_no_recipient_configured(self, app):
        with app.app_context():
            config = get_system_config()
            config.security_notification_user_id = None
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            db.session.commit()

            assert notify_security_contact("hi") is False

    def test_sends_to_the_configured_recipients_own_dm(self, app, discord_user, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            DiscordClient,
            "send_dm_message",
            lambda self, discord_user_id, content, components=None: sent.update(
                discord_user_id=discord_user_id, content=content
            ),
        )

        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            config.security_notification_user_id = discord_user
            db.session.commit()

            assert notify_security_contact("someone else's account had a failed login") is True

        assert sent == {"discord_user_id": "123456789012345678", "content": "someone else's account had a failed login"}

    def test_returns_false_when_the_configured_recipient_has_no_discord_id_set(self, app):
        with app.app_context():
            role = Role(name="NoDiscordSecurityRole")
            db.session.add(role)
            db.session.flush()
            contact = User(
                username="security_no_discord",
                password_hash=generate_password_hash("x"),
                is_active=True,
                role_id=role.id,
            )
            db.session.add(contact)
            db.session.flush()

            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            config.security_notification_user_id = contact.id
            db.session.commit()

            assert notify_security_contact("hi") is False


class TestNotificationButtons:
    """The three "View Details"/"View in app" Link buttons need
    SystemConfig.app_base_url to build a real URL from inside a background
    worker thread (no active HTTP request) — see
    app/services/discord/helpers.py's _external_url(). notify_run_finished
    instead reuses the interactive Check Status button (no URL needed at
    all), covered by tests/test_discord_worker.py's own dedicated test.
    """

    def test_run_finished_includes_an_interactive_check_status_button(self, app, discord_user, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            DiscordClient,
            "send_dm_message",
            lambda self, discord_user_id, content, components=None: sent.update(components=components),
        )

        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            db.session.commit()

            workflow = Workflow(name="Deploy Everything", is_active=True)
            db.session.add(workflow)
            db.session.flush()
            run = WorkflowRun(workflow_id=workflow.id, status="success", triggered_by=discord_user)
            db.session.add(run)
            db.session.commit()

            notify_run_finished(run)

            button = sent["components"][0]["components"][0]
            assert button["custom_id"] == f"status:{run.id}"
            assert "url" not in button

    def test_deploy_finished_includes_a_link_button_when_app_base_url_set(self, app, discord_user, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            DiscordClient,
            "send_dm_message",
            lambda self, discord_user_id, content, components=None: sent.update(components=components),
        )

        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            config.app_base_url = "https://cicd.example.com"
            db.session.commit()

            run = DeploymentRun(action="deploy", status="success", triggered_by=discord_user)
            db.session.add(run)
            db.session.commit()

            notify_deploy_finished(run)

            with app.test_request_context(base_url="https://cicd.example.com"):
                expected_url = url_for("deployment_runs.detail", run_id=run.id, _external=True)

            button = sent["components"][0]["components"][0]
            assert button["style"] == 5  # Link style — opens client-side, no bot round-trip
            assert button["url"] == expected_url

    def test_deploy_finished_omits_the_button_when_app_base_url_unset(self, app, discord_user, monkeypatch):
        sent = {"components": "unset"}
        monkeypatch.setattr(
            DiscordClient,
            "send_dm_message",
            lambda self, discord_user_id, content, components=None: sent.update(components=components),
        )

        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            config.app_base_url = None
            db.session.commit()

            run = DeploymentRun(action="deploy", status="success", triggered_by=discord_user)
            db.session.add(run)
            db.session.commit()

            assert notify_deploy_finished(run) is True

        assert sent["components"] is None

    def test_build_finished_includes_a_link_button_to_images(self, app, discord_user, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            DiscordClient,
            "send_dm_message",
            lambda self, discord_user_id, content, components=None: sent.update(components=components),
        )

        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            config.app_base_url = "https://cicd.example.com"
            db.session.commit()

            version_type = VersionType(name="DEV")
            db.session.add(version_type)
            db.session.flush()
            version = Version(name="svc", version_type_id=version_type.id)
            db.session.add(version)
            db.session.flush()
            batch = BuildBatch(
                version_id=version.id, bump_type="patch", status="success",
                requested_by=discord_user, full_version_string="1.0.0",
            )
            db.session.add(batch)
            db.session.commit()

            notify_build_finished(batch)

            with app.test_request_context(base_url="https://cicd.example.com"):
                expected_url = url_for("images.list_images", _external=True)

            button = sent["components"][0]["components"][0]
            assert button["style"] == 5
            assert button["url"] == expected_url

    def test_awaiting_review_includes_a_view_in_app_button_alongside_approve_reject(self, app, discord_user, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            DiscordClient,
            "send_dm_message",
            lambda self, discord_user_id, content, components=None: sent.update(components=components),
        )

        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            config.app_base_url = "https://cicd.example.com"
            db.session.commit()

            workflow = Workflow(name="Deploy Everything", is_active=True)
            db.session.add(workflow)
            db.session.flush()
            step = WorkflowStep(workflow_id=workflow.id, order=0, step_type="build", on_failure="stop")
            db.session.add(step)
            db.session.flush()
            run = WorkflowRun(workflow_id=workflow.id, status="running", triggered_by=discord_user)
            db.session.add(run)
            db.session.flush()
            change_type = ChangeType(name="Bug Fix")
            db.session.add(change_type)
            db.session.flush()
            step_run = WorkflowStepRun(
                workflow_run_id=run.id, workflow_step_id=step.id, step_order=0, step_type="build",
                status="awaiting_review", suggested_bump_type="minor", suggested_change_type_id=change_type.id,
            )
            db.session.add(step_run)
            db.session.commit()

            notify_awaiting_review(step_run)

            with app.test_request_context(base_url="https://cicd.example.com"):
                expected_url = url_for("workflows.view_run", run_id=run.id, _external=True)

            rows = sent["components"]
            assert len(rows) == 2  # approve/reject row, then the new link row
            approve_reject_row, link_row = rows
            assert {b["custom_id"] for b in approve_reject_row["components"]} == {
                f"approve_review:{step_run.id}",
                f"reject_review:{step_run.id}",
            }
            link_button = link_row["components"][0]
            assert link_button["style"] == 5
            assert link_button["url"] == expected_url

    def test_awaiting_review_omits_the_link_row_when_app_base_url_unset(self, app, discord_user, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            DiscordClient,
            "send_dm_message",
            lambda self, discord_user_id, content, components=None: sent.update(components=components),
        )

        with app.app_context():
            config = get_system_config()
            config.discord_notifications_enabled = True
            config.encrypted_discord_bot_token = encrypt("bot-token")
            config.app_base_url = None
            db.session.commit()

            workflow = Workflow(name="Deploy Everything", is_active=True)
            db.session.add(workflow)
            db.session.flush()
            step = WorkflowStep(workflow_id=workflow.id, order=0, step_type="build", on_failure="stop")
            db.session.add(step)
            db.session.flush()
            run = WorkflowRun(workflow_id=workflow.id, status="running", triggered_by=discord_user)
            db.session.add(run)
            db.session.flush()
            change_type = ChangeType(name="Bug Fix")
            db.session.add(change_type)
            db.session.flush()
            step_run = WorkflowStepRun(
                workflow_run_id=run.id, workflow_step_id=step.id, step_order=0, step_type="build",
                status="awaiting_review", suggested_bump_type="minor", suggested_change_type_id=change_type.id,
            )
            db.session.add(step_run)
            db.session.commit()

            notify_awaiting_review(step_run)

            # Only the Approve/Reject row — no second row when there's no link to show.
            assert len(sent["components"]) == 1
