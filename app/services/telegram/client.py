import requests

API_BASE = "https://api.telegram.org"
SEND_TIMEOUT_SECONDS = 10


class TelegramNotifier:
    """Thin wrapper around the Telegram Bot API's sendMessage endpoint —
    same shape as this app's other outbound-HTTP providers (e.g.
    app/services/registry/ghcr.py): plain `requests` calls with an explicit
    timeout, config (`bot_token`) taken as a constructor kwarg rather than
    read from the DB/env directly, so the caller is responsible for
    decrypting the stored token first (see app/services/telegram/helpers.py).
    """

    def __init__(self, bot_token):
        if not bot_token:
            raise ValueError("TelegramNotifier requires a bot_token.")
        self.bot_token = bot_token

    def send_message(self, chat_id, text):
        """Raises RuntimeError on failure — callers that want a best-effort,
        never-fails-the-caller send should go through
        app.services.telegram.helpers.notify_user() instead of calling this
        directly.
        """
        try:
            response = requests.post(
                f"{API_BASE}/bot{self.bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Could not reach Telegram: {exc}") from exc

        if response.status_code != 200:
            try:
                detail = response.json().get("description")
            except ValueError:
                detail = response.text
            raise RuntimeError(f"Telegram API error ({response.status_code}): {detail}")
