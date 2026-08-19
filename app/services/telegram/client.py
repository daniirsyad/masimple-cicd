import requests

API_BASE = "https://api.telegram.org"
SEND_TIMEOUT_SECONDS = 10
# getUpdates blocks server-side for up to this long waiting for a new
# update before returning empty — the client-side request timeout must
# exceed it with headroom, or every empty poll would raise a spurious
# ReadTimeout.
GET_UPDATES_TIMEOUT_SECONDS = 25
GET_UPDATES_REQUEST_TIMEOUT_SECONDS = GET_UPDATES_TIMEOUT_SECONDS + 10


class TelegramNotifier:
    """Thin wrapper around the Telegram Bot API — same shape as this app's
    other outbound-HTTP providers (e.g. app/services/registry/ghcr.py):
    plain `requests` calls with an explicit timeout, config (`bot_token`)
    taken as a constructor kwarg rather than read from the DB/env directly,
    so the caller is responsible for decrypting the stored token first (see
    app/services/telegram/helpers.py).
    """

    def __init__(self, bot_token):
        if not bot_token:
            raise ValueError("TelegramNotifier requires a bot_token.")
        self.bot_token = bot_token

    def _post(self, method, json_body, timeout):
        try:
            response = requests.post(
                f"{API_BASE}/bot{self.bot_token}/{method}",
                json=json_body,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Could not reach Telegram: {exc}") from exc

        if response.status_code != 200:
            try:
                detail = response.json().get("description")
            except ValueError:
                detail = response.text
            raise RuntimeError(f"Telegram API error ({response.status_code}): {detail}")

        return response.json()

    def send_message(self, chat_id, text, reply_markup=None):
        """Raises RuntimeError on failure — callers that want a best-effort,
        never-fails-the-caller send should go through
        app.services.telegram.helpers.notify_user() instead of calling this
        directly.

        `reply_markup`, when given, is Telegram's inline-keyboard shape
        (`{"inline_keyboard": [[{"text": ..., "callback_data": ...}], ...]}`)
        — see app/services/telegram/worker.py's /run and /status commands.
        """
        body = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        self._post("sendMessage", body, SEND_TIMEOUT_SECONDS)

    def get_updates(self, offset=None, timeout=GET_UPDATES_TIMEOUT_SECONDS):
        """Long-polls getUpdates: Telegram itself holds the connection open
        up to `timeout` seconds waiting for a new update before responding
        with an empty list, rather than us polling tightly in a loop.
        `offset` should be the last-processed update_id + 1 — Telegram
        treats passing a given offset as confirmation every earlier update
        was received, and won't redeliver them.
        """
        body = {"timeout": timeout}
        if offset is not None:
            body["offset"] = offset
        result = self._post("getUpdates", body, timeout + 10)
        return result.get("result", [])

    def answer_callback_query(self, callback_query_id, text=None):
        """Clears the loading spinner Telegram shows on the tapped inline
        button. Best-effort from the caller's perspective isn't needed here
        — worker.py already wraps each update's handling in a broad
        try/except, same as every other poll loop in this app.
        """
        body = {"callback_query_id": callback_query_id}
        if text is not None:
            body["text"] = text
        self._post("answerCallbackQuery", body, SEND_TIMEOUT_SECONDS)

    def set_my_commands(self, commands):
        """Registers the bot's native "/" command menu inside the Telegram
        chat UI. `commands` is a list of {"command": ..., "description": ...}.
        """
        self._post("setMyCommands", {"commands": commands}, SEND_TIMEOUT_SECONDS)
