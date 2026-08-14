import pytest
import requests

from app.extensions import db
from app.models import Role, User
from app.services.telegram.client import TelegramNotifier
from app.services.telegram.helpers import notify_security_contact, notify_user
from app.utils.crypto import encrypt
from app.utils.system_config import get_system_config
from werkzeug.security import generate_password_hash


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


class TestTelegramNotifier:
    def test_send_message_posts_to_the_bot_api(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured.update(url=url, json=json, timeout=timeout)
            return _FakeResponse(status_code=200)

        monkeypatch.setattr(requests, "post", fake_post)

        TelegramNotifier("test-token").send_message("12345", "hello")

        assert captured["url"] == "https://api.telegram.org/bottest-token/sendMessage"
        assert captured["json"] == {"chat_id": "12345", "text": "hello"}
        assert captured["timeout"] == 10

    def test_send_message_raises_on_non_200(self, monkeypatch):
        monkeypatch.setattr(
            requests, "post", lambda *a, **k: _FakeResponse(status_code=400, json_data={"description": "bad chat"})
        )
        with pytest.raises(RuntimeError, match="bad chat"):
            TelegramNotifier("test-token").send_message("12345", "hello")

    def test_send_message_raises_on_network_error(self, monkeypatch):
        def raise_it(*args, **kwargs):
            raise requests.RequestException("boom")

        monkeypatch.setattr(requests, "post", raise_it)
        with pytest.raises(RuntimeError, match="Could not reach Telegram"):
            TelegramNotifier("test-token").send_message("12345", "hello")

    def test_requires_a_bot_token(self):
        with pytest.raises(ValueError):
            TelegramNotifier(None)


@pytest.fixture
def telegram_user(app):
    with app.app_context():
        role = Role(name="TelegramTestRole", description="test")
        db.session.add(role)
        db.session.flush()

        user = User(
            username="telegram_test",
            password_hash=generate_password_hash("TelegramPass123!"),
            is_active=True,
            role_id=role.id,
            telegram_chat_id="98765",
        )
        db.session.add(user)
        db.session.commit()
        return user.id


class TestNotifyUser:
    def test_returns_false_and_sends_nothing_when_user_has_no_chat_id(self, app, monkeypatch):
        called = {"count": 0}
        monkeypatch.setattr(TelegramNotifier, "send_message", lambda self, *a: called.update(count=called["count"] + 1))

        with app.app_context():
            role = Role(name="NoChatRole")
            db.session.add(role)
            db.session.flush()
            user = User(
                username="no_chat", password_hash=generate_password_hash("x"), is_active=True, role_id=role.id
            )
            db.session.add(user)
            db.session.commit()

            assert notify_user(user, "hi") is False
        assert called["count"] == 0

    def test_returns_false_when_notifications_are_disabled(self, app, telegram_user):
        with app.app_context():
            config = get_system_config()
            config.telegram_notifications_enabled = False
            config.encrypted_telegram_bot_token = encrypt("bot-token")
            db.session.commit()

            user = User.query.get(telegram_user)
            assert notify_user(user, "hi") is False

    def test_returns_false_when_no_bot_token_configured(self, app, telegram_user):
        with app.app_context():
            config = get_system_config()
            config.telegram_notifications_enabled = True
            config.encrypted_telegram_bot_token = None
            db.session.commit()

            user = User.query.get(telegram_user)
            assert notify_user(user, "hi") is False

    def test_sends_and_returns_true_when_fully_configured(self, app, telegram_user, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            TelegramNotifier, "send_message", lambda self, chat_id, text: sent.update(chat_id=chat_id, text=text)
        )

        with app.app_context():
            config = get_system_config()
            config.telegram_notifications_enabled = True
            config.encrypted_telegram_bot_token = encrypt("bot-token")
            db.session.commit()

            user = User.query.get(telegram_user)
            assert notify_user(user, "hello there") is True

        assert sent == {"chat_id": "98765", "text": "hello there"}

    def test_a_telegram_failure_is_swallowed_not_raised(self, app, telegram_user, monkeypatch):
        def raise_it(self, chat_id, text):
            raise RuntimeError("Telegram is down")

        monkeypatch.setattr(TelegramNotifier, "send_message", raise_it)

        with app.app_context():
            config = get_system_config()
            config.telegram_notifications_enabled = True
            config.encrypted_telegram_bot_token = encrypt("bot-token")
            db.session.commit()

            user = User.query.get(telegram_user)
            # Must not raise.
            assert notify_user(user, "hello") is False

    def test_returns_false_for_none_user(self, app):
        with app.app_context():
            assert notify_user(None, "hi") is False


class TestNotifySecurityContact:
    def test_returns_false_when_no_recipient_configured(self, app):
        with app.app_context():
            config = get_system_config()
            config.security_notification_user_id = None
            config.telegram_notifications_enabled = True
            config.encrypted_telegram_bot_token = encrypt("bot-token")
            db.session.commit()

            assert notify_security_contact("hi") is False

    def test_sends_to_the_configured_recipients_own_chat_id(self, app, telegram_user, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            TelegramNotifier, "send_message", lambda self, chat_id, text: sent.update(chat_id=chat_id, text=text)
        )

        with app.app_context():
            config = get_system_config()
            config.telegram_notifications_enabled = True
            config.encrypted_telegram_bot_token = encrypt("bot-token")
            config.security_notification_user_id = telegram_user
            db.session.commit()

            assert notify_security_contact("someone else's account had a failed login") is True

        assert sent == {"chat_id": "98765", "text": "someone else's account had a failed login"}

    def test_returns_false_when_the_configured_recipient_has_no_chat_id_set(self, app):
        with app.app_context():
            role = Role(name="NoChatSecurityRole")
            db.session.add(role)
            db.session.flush()
            contact = User(
                username="security_no_chat",
                password_hash=generate_password_hash("x"),
                is_active=True,
                role_id=role.id,
            )
            db.session.add(contact)
            db.session.flush()

            config = get_system_config()
            config.telegram_notifications_enabled = True
            config.encrypted_telegram_bot_token = encrypt("bot-token")
            config.security_notification_user_id = contact.id
            db.session.commit()

            assert notify_security_contact("hi") is False
