import requests

API_BASE = "https://discord.com/api/v10"
REQUEST_TIMEOUT_SECONDS = 10


class DiscordClient:
    """Thin wrapper around Discord's REST API — same shape as
    app/services/telegram/client.py's TelegramNotifier: plain `requests`
    calls with an explicit timeout, `bot_token` taken as a constructor kwarg
    rather than read from the DB/env directly (the caller decrypts the
    stored token first — see app/services/discord/helpers.py), raises
    RuntimeError on failure so a best-effort caller goes through helpers.py
    instead of calling this directly.

    Covers only the REST calls this integration needs: opening/using a DM
    channel for outbound notifications, and the Interactions
    ack/followup/command-registration endpoints for inbound slash commands
    and components — NOT the Gateway WebSocket connection itself (see
    app/services/discord/gateway.py for that).
    """

    def __init__(self, bot_token):
        if not bot_token:
            raise ValueError("DiscordClient requires a bot_token.")
        self.bot_token = bot_token

    def _headers(self):
        return {"Authorization": f"Bot {self.bot_token}", "Content-Type": "application/json"}

    def _request(self, method, path, json_body=None, auth=True, timeout=REQUEST_TIMEOUT_SECONDS):
        try:
            response = requests.request(
                method,
                f"{API_BASE}{path}",
                json=json_body,
                headers=self._headers() if auth else {"Content-Type": "application/json"},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Could not reach Discord: {exc}") from exc

        if response.status_code >= 300:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise RuntimeError(f"Discord API error ({response.status_code}): {detail}")

        if not response.content:
            return None
        return response.json()

    def get_gateway_url(self):
        """GET /gateway/bot — returns {"url", "shards", "session_start_limit"}.
        Fetched fresh on every connection attempt (not cached across the
        process's life) rather than hardcoding wss://gateway.discord.gg, so
        `session_start_limit.remaining` can be inspected before Identifying
        (see gateway.py) and a domain change on Discord's side is never a
        problem.
        """
        return self._request("GET", "/gateway/bot")

    def create_dm_channel(self, discord_user_id):
        """POST /users/@me/channels — idempotent: returns the existing DM
        channel if one is already open with this user, opens a new one
        otherwise. Returns the channel id.
        """
        result = self._request("POST", "/users/@me/channels", json_body={"recipient_id": discord_user_id})
        return result["id"]

    def send_dm_message(self, discord_user_id, content, components=None):
        """Resolves (or opens) this user's DM channel, then posts a message
        into it. `components`, when given, is Discord's ActionRow/Button/
        SelectMenu component tree — see app/services/discord/worker.py's
        *_components builders.
        """
        channel_id = self.create_dm_channel(discord_user_id)
        body = {"content": content}
        if components is not None:
            body["components"] = components
        self._request("POST", f"/channels/{channel_id}/messages", json_body=body)

    def bulk_register_commands(self, application_id, commands):
        """PUT /applications/{id}/commands — a bulk overwrite, replacing the
        entire global command set with exactly `commands`. Idempotent, so
        safe to call on every Gateway READY rather than needing a "register
        once" flag the way Telegram's set_my_commands does.
        """
        self._request("PUT", f"/applications/{application_id}/commands", json_body=commands)

    def respond_to_interaction(self, interaction_id, interaction_token, response_type, data=None):
        """POST /interactions/{id}/{token}/callback — must reach Discord
        within 3 seconds of the interaction being received. `response_type`
        is one of Discord's InteractionCallbackType ints (4 immediate
        message, 5/6 deferred). Unauthenticated (no bot token needed) — the
        interaction token itself is the credential.
        """
        body = {"type": response_type}
        if data is not None:
            body["data"] = data
        self._request("POST", f"/interactions/{interaction_id}/{interaction_token}/callback", json_body=body, auth=False)

    def edit_original_response(self, application_id, interaction_token, content, components=None):
        """PATCH /webhooks/{application_id}/{interaction_token}/messages/@original
        — the followup to a deferred response (type 5/6 above), valid for
        15 minutes after the original interaction. Unauthenticated, same as
        respond_to_interaction.
        """
        body = {"content": content}
        if components is not None:
            body["components"] = components
        self._request(
            "PATCH",
            f"/webhooks/{application_id}/{interaction_token}/messages/@original",
            json_body=body,
            auth=False,
        )
