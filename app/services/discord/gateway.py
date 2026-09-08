"""Discord Gateway WebSocket protocol handling — Identify/Resume/Heartbeat/
Dispatch/Reconnect, on top of the synchronous `websocket-client` library
(no asyncio anywhere in this app, same "plain threading.Thread" constraint
every other background worker follows — see
app/services/telegram/worker.py).

This module knows nothing about what a Dispatch event *means* — worker.py
owns "READY means (re)register slash commands" and "INTERACTION_CREATE
means dispatch a command/component" — this module only owns getting a
Dispatch payload delivered reliably across reconnects.
"""
import json
import random
import threading
import time

import websocket

from app.utils.error_logger import log_error

GATEWAY_VERSION_QS = "?v=10&encoding=json"

DISPATCH_OP = 0
HEARTBEAT_OP = 1
IDENTIFY_OP = 2
RESUME_OP = 6
RECONNECT_OP = 7
INVALID_SESSION_OP = 9
HELLO_OP = 10
HEARTBEAT_ACK_OP = 11

# https://discord.com/developers/docs/topics/opcodes-and-status-codes#gateway-gateway-close-event-codes
# — bad token / disallowed intents / sharding errors. Retrying these with
# the same credentials/session will never succeed, so don't try to Resume,
# and log loudly rather than hot-looping.
NON_RESUMABLE_CLOSE_CODES = {4004, 4010, 4011, 4012, 4013, 4014}

RECONNECT_BACKOFF_INITIAL_SECONDS = 1
RECONNECT_BACKOFF_MAX_SECONDS = 60
# A connection that stayed up at least this long is treated as "healthy" —
# the next disconnect starts backoff over from the initial value instead of
# wherever a string of earlier failures had left it.
RECONNECT_BACKOFF_RESET_AFTER_SECONDS = 60


class DiscordGateway:
    """Owns one logical Gateway connection's full lifecycle. `run()` blocks
    (connect, heartbeat, dispatch, reconnect-with-backoff on drop) until
    `stop()` is called from another thread — same "dedicated thread runs a
    blocking call" shape as telegram/worker.py's own poll loop, just
    wrapping a persistent connection instead of a one-shot long-poll.

    `on_dispatch(event_type, data)` is called for every Gateway Dispatch
    (op:0) event, synchronously on this connection's own read thread —
    callers that might block for a while (e.g. an AI-backed prefill call)
    MUST hand off to their own worker thread rather than blocking here, or
    they'll starve heartbeating and get the connection killed as a zombie
    (see app/services/discord/worker.py's deferred-interaction handling).
    """

    def __init__(self, bot_token, client, on_dispatch):
        self.bot_token = bot_token
        self.client = client
        self.on_dispatch = on_dispatch

        self._stop_event = threading.Event()
        self._send_lock = threading.Lock()
        self._heartbeat_stop = threading.Event()

        self._last_seq = None
        self._session_id = None
        self._resume_gateway_url = None
        self._beat_sent_since_last_ack = False
        self._close_code = None

    def stop(self):
        self._stop_event.set()
        self._heartbeat_stop.set()

    def run(self):
        backoff = RECONNECT_BACKOFF_INITIAL_SECONDS
        while not self._stop_event.is_set():
            connected_at = time.monotonic()
            self._close_code = None
            try:
                self._connect_and_run()
            except Exception as exc:
                log_error(
                    source="discord.gateway.run",
                    exc=exc,
                    description=f"Discord Gateway connection attempt failed: {exc}",
                )

            if self._stop_event.is_set():
                return

            if self._close_code in NON_RESUMABLE_CLOSE_CODES:
                log_error(
                    source="discord.gateway.run",
                    exc=None,
                    description=(
                        f"Discord Gateway closed with non-resumable code {self._close_code} — "
                        "check the configured bot token/application settings."
                    ),
                )
                self._session_id = None
                self._last_seq = None

            if time.monotonic() - connected_at > RECONNECT_BACKOFF_RESET_AFTER_SECONDS:
                backoff = RECONNECT_BACKOFF_INITIAL_SECONDS

            time.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_SECONDS)

    def _connect_and_run(self):
        url = self._resolve_connect_url()
        app = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_close=self._on_close,
            on_error=self._on_error,
        )
        app.run_forever()

    def _resolve_connect_url(self):
        # A held session_id + resume_gateway_url means the last connection
        # was cleanly Hello'd at least once — reconnect straight to that
        # URL and attempt Resume rather than re-fetching /gateway/bot.
        if self._session_id and self._resume_gateway_url:
            return f"{self._resume_gateway_url}{GATEWAY_VERSION_QS}"

        gateway = self.client.get_gateway_url()
        remaining = (gateway.get("session_start_limit") or {}).get("remaining")
        if remaining is not None and remaining < 5:
            log_error(
                source="discord.gateway.resolve_connect_url",
                exc=None,
                description=f"Discord Gateway session_start_limit.remaining is low ({remaining}).",
            )
        return f"{gateway['url']}{GATEWAY_VERSION_QS}"

    def _send(self, ws, payload):
        with self._send_lock:
            ws.send(json.dumps(payload))

    def _on_open(self, ws):
        pass  # Identify/Resume is sent after Hello (op:10), not on open.

    def _on_close(self, ws, close_status_code, close_msg):
        self._close_code = close_status_code
        self._heartbeat_stop.set()

    def _on_error(self, ws, error):
        log_error(
            source="discord.gateway.on_error",
            exc=error if isinstance(error, Exception) else None,
            description=f"Discord Gateway socket error: {error}",
        )

    def _on_message(self, ws, raw_message):
        try:
            payload = json.loads(raw_message)
        except (TypeError, ValueError):
            return

        op = payload.get("op")

        if op == DISPATCH_OP:
            if payload.get("s") is not None:
                self._last_seq = payload["s"]
            event_type = payload.get("t")
            data = payload.get("d") or {}
            if event_type == "READY":
                self._session_id = data.get("session_id")
                self._resume_gateway_url = data.get("resume_gateway_url")
            self.on_dispatch(event_type, data)
        elif op == HELLO_OP:
            interval_seconds = payload["d"]["heartbeat_interval"] / 1000
            self._start_heartbeat(ws, interval_seconds)
            self._identify_or_resume(ws)
        elif op == HEARTBEAT_ACK_OP:
            self._beat_sent_since_last_ack = False
        elif op == HEARTBEAT_OP:
            # Discord may ask for an immediate out-of-cycle heartbeat.
            self._send_heartbeat(ws)
        elif op == RECONNECT_OP:
            ws.close()
        elif op == INVALID_SESSION_OP:
            resumable = bool(payload.get("d"))
            time.sleep(1 + random.random() * 4)
            if not resumable:
                self._session_id = None
                self._last_seq = None
            self._identify_or_resume(ws)

    def _identify_or_resume(self, ws):
        if self._session_id and self._last_seq is not None:
            payload = {
                "op": RESUME_OP,
                "d": {"token": self.bot_token, "session_id": self._session_id, "seq": self._last_seq},
            }
        else:
            payload = {
                "op": IDENTIFY_OP,
                "d": {
                    "token": self.bot_token,
                    # 0: no Gateway Intent is needed for this integration —
                    # INTERACTION_CREATE (slash commands, buttons, selects)
                    # is delivered regardless of intents, and no message
                    # content or guild state is ever read.
                    "intents": 0,
                    "properties": {"os": "linux", "browser": "masimple-cicd", "device": "masimple-cicd"},
                },
            }
        self._send(ws, payload)

    def _start_heartbeat(self, ws, interval_seconds):
        self._heartbeat_stop.clear()
        self._beat_sent_since_last_ack = False

        def _loop():
            # Discord's documented jitter: wait interval * random() before
            # the first beat, to avoid every reconnecting bot's heartbeats
            # landing on the same tick after a mass disconnect.
            if self._heartbeat_stop.wait(timeout=interval_seconds * random.random()):
                return
            while not self._heartbeat_stop.is_set():
                if self._beat_sent_since_last_ack:
                    # No ACK arrived for the previous beat — zombied
                    # connection. Close locally; the outer run() loop
                    # reconnects (attempting Resume first).
                    ws.close()
                    return
                self._send_heartbeat(ws)
                if self._heartbeat_stop.wait(timeout=interval_seconds):
                    return

        threading.Thread(target=_loop, daemon=True, name="discord-heartbeat").start()

    def _send_heartbeat(self, ws):
        try:
            self._send(ws, {"op": HEARTBEAT_OP, "d": self._last_seq})
            self._beat_sent_since_last_ack = True
        except Exception:
            pass  # on_close/on_error handles a genuinely dead socket.
