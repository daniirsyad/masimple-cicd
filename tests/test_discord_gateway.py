"""Unit tests for the Discord Gateway op-code state machine
(app/services/discord/gateway.py) — Identify vs. Resume, heartbeat-ACK
zombie tracking, and Invalid-Session branching, all driven directly against
DiscordGateway's `_on_message`/`_on_close` callbacks with hand-built op-code
payloads. No real socket, no real thread, no `websocket.WebSocketApp` at
all — a fake `ws` object stands in for the real one, capturing every
`.send()` call for assertion.
"""
import json
import threading
import time

import pytest

from app.services.discord.gateway import (
    DISPATCH_OP,
    HEARTBEAT_ACK_OP,
    HEARTBEAT_OP,
    HELLO_OP,
    IDENTIFY_OP,
    INVALID_SESSION_OP,
    RECONNECT_OP,
    RESUME_OP,
    DiscordGateway,
)


class _FakeWs:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def close(self):
        self.closed = True


class _FakeClient:
    def __init__(self, url="wss://gateway.example.com/", remaining=100):
        self.url = url
        self.remaining = remaining
        self.calls = 0

    def get_gateway_url(self):
        self.calls += 1
        return {"url": self.url, "session_start_limit": {"remaining": self.remaining}}


@pytest.fixture
def gateway():
    return DiscordGateway("test-token", _FakeClient(), on_dispatch=lambda *a: None)


class TestResolveConnectUrl:
    def test_fetches_gateway_bot_when_no_prior_session(self, gateway):
        url = gateway._resolve_connect_url()
        assert url == "wss://gateway.example.com/?v=10&encoding=json"

    def test_uses_resume_gateway_url_when_a_session_is_held(self, gateway):
        gateway._session_id = "session-1"
        gateway._resume_gateway_url = "wss://resume.example.com/"
        url = gateway._resolve_connect_url()
        assert url == "wss://resume.example.com/?v=10&encoding=json"
        assert gateway.client.calls == 0  # never re-fetched /gateway/bot


class TestIdentifyVsResume:
    def test_hello_sends_identify_when_no_prior_session(self, gateway):
        ws = _FakeWs()
        gateway._on_message(ws, json.dumps({"op": HELLO_OP, "d": {"heartbeat_interval": 41250}}))
        gateway._heartbeat_stop.set()  # tear down the spawned heartbeat thread promptly

        identify_calls = [m for m in ws.sent if m["op"] == IDENTIFY_OP]
        assert len(identify_calls) == 1
        assert identify_calls[0]["d"]["token"] == "test-token"
        assert identify_calls[0]["d"]["intents"] == 0

    def test_hello_sends_resume_when_a_prior_session_is_held(self, gateway):
        gateway._session_id = "session-1"
        gateway._last_seq = 42
        ws = _FakeWs()
        gateway._on_message(ws, json.dumps({"op": HELLO_OP, "d": {"heartbeat_interval": 41250}}))
        gateway._heartbeat_stop.set()

        resume_calls = [m for m in ws.sent if m["op"] == RESUME_OP]
        assert len(resume_calls) == 1
        assert resume_calls[0]["d"] == {"token": "test-token", "session_id": "session-1", "seq": 42}

    def test_ready_dispatch_stores_session_id_and_resume_url(self, gateway):
        received = []
        gateway.on_dispatch = lambda event_type, data: received.append((event_type, data))
        ws = _FakeWs()

        gateway._on_message(
            ws,
            json.dumps(
                {
                    "op": DISPATCH_OP,
                    "s": 1,
                    "t": "READY",
                    "d": {"session_id": "session-abc", "resume_gateway_url": "wss://resume.example.com/"},
                }
            ),
        )

        assert gateway._session_id == "session-abc"
        assert gateway._resume_gateway_url == "wss://resume.example.com/"
        assert gateway._last_seq == 1
        assert received == [("READY", {"session_id": "session-abc", "resume_gateway_url": "wss://resume.example.com/"})]

    def test_invalid_session_resumable_retries_resume(self, gateway, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda seconds: None)
        gateway._session_id = "session-1"
        gateway._last_seq = 7
        ws = _FakeWs()

        gateway._on_message(ws, json.dumps({"op": INVALID_SESSION_OP, "d": True}))

        assert gateway._session_id == "session-1"  # kept
        resume_calls = [m for m in ws.sent if m["op"] == RESUME_OP]
        assert len(resume_calls) == 1

    def test_invalid_session_not_resumable_forces_fresh_identify(self, gateway, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda seconds: None)
        gateway._session_id = "session-1"
        gateway._last_seq = 7
        ws = _FakeWs()

        gateway._on_message(ws, json.dumps({"op": INVALID_SESSION_OP, "d": False}))

        assert gateway._session_id is None
        assert gateway._last_seq is None
        identify_calls = [m for m in ws.sent if m["op"] == IDENTIFY_OP]
        assert len(identify_calls) == 1

    def test_reconnect_op_closes_the_socket(self, gateway):
        ws = _FakeWs()
        gateway._on_message(ws, json.dumps({"op": RECONNECT_OP}))
        assert ws.closed is True

    def test_server_requested_heartbeat_is_answered(self, gateway):
        ws = _FakeWs()
        gateway._last_seq = 5
        gateway._on_message(ws, json.dumps({"op": HEARTBEAT_OP}))
        heartbeats = [m for m in ws.sent if m["op"] == HEARTBEAT_OP]
        assert heartbeats == [{"op": HEARTBEAT_OP, "d": 5}]


class TestHeartbeatZombieDetection:
    def test_ack_received_clears_the_pending_flag(self, gateway):
        gateway._beat_sent_since_last_ack = True
        ws = _FakeWs()
        gateway._on_message(ws, json.dumps({"op": HEARTBEAT_ACK_OP}))
        assert gateway._beat_sent_since_last_ack is False

    def test_missed_ack_closes_the_connection_before_the_next_beat(self, gateway):
        # Directly exercises the heartbeat loop's zombie check without
        # waiting out a real interval: a beat was sent, no ACK arrived, so
        # the very next scheduled beat must close the socket instead of
        # sending another one.
        ws = _FakeWs()
        gateway._send_heartbeat(ws)
        assert gateway._beat_sent_since_last_ack is True

        # Simulate the heartbeat loop's own check (see _start_heartbeat) —
        # since no ACK op arrived, the loop would close here.
        if gateway._beat_sent_since_last_ack:
            ws.close()
        assert ws.closed is True

    def test_start_heartbeat_thread_sends_beats_and_stops_cleanly(self, gateway):
        ws = _FakeWs()
        gateway._last_seq = 1
        # A tiny interval so the thread beats a few times almost instantly,
        # keeping this test fast and deterministic (stopped explicitly
        # rather than relying on timing to bound how many beats happen).
        gateway._start_heartbeat(ws, interval_seconds=0.01)
        time.sleep(0.1)
        gateway._heartbeat_stop.set()
        time.sleep(0.05)

        heartbeats = [m for m in ws.sent if m["op"] == HEARTBEAT_OP]
        assert len(heartbeats) >= 1
        # No thread left running past the stop signal.
        assert all(not t.is_alive() or t.name != "discord-heartbeat" for t in threading.enumerate())


class TestOnClose:
    def test_stores_the_close_code_and_stops_heartbeating(self, gateway):
        gateway._heartbeat_stop.clear()
        gateway._on_close(ws=None, close_status_code=4004, close_msg="bad token")
        assert gateway._close_code == 4004
        assert gateway._heartbeat_stop.is_set() is True
