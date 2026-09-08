"""Unit tests for KubernetesProvider's wait_timeout_seconds handling on
apply()/restart() — after a successful `kubectl apply`, polling `kubectl
rollout status` for every Deployment/StatefulSet/DaemonSet in the manifest
before the result counts as a success (see kubernetes_provider.py's
apply()/_wait_for_rollout()/_rollout_targets()/_run_rollout_status_poll()),
and aborting immediately — instead of waiting out the full timeout — the
moment a pod is seen in a FAIL_FAST_WAITING_REASONS state such as
CrashLoopBackOff or ImagePullBackOff (see _pod_fail_fast_reason()).

`kubectl apply` and the new `kubectl get pods` fail-fast probe both still go
through the blocking `_run_kubectl`, so they're mocked via `subprocess.run`
same as test_kubernetes_provider_restart.py. `kubectl rollout status` is now
run via `subprocess.Popen` (polled rather than blocked-on), so it's mocked
via the `_FakePopen`/`_popen_factory` helpers below instead.
"""
import json
import subprocess
import time

import pytest

import app.services.deployment.kubernetes_provider as kp_module
from app.services.deployment.custom_api_provider import CustomAPIProvider
from app.services.deployment.kubernetes_provider import KubernetesProvider

CONFIGMAP_ONLY_YAML = '{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "x"}}'

DEPLOYMENT_WITH_NAMESPACE_YAML = (
    '{"apiVersion": "apps/v1", "kind": "Deployment", '
    '"metadata": {"name": "app-abc", "namespace": "prod"}}'
)

DEPLOYMENT_NO_NAMESPACE_YAML = '{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "app-abc"}}'

DEPLOYMENT_WITH_SELECTOR_YAML = (
    '{"apiVersion": "apps/v1", "kind": "Deployment", '
    '"metadata": {"name": "app-abc", "namespace": "prod"}, '
    '"spec": {"selector": {"matchLabels": {"app": "demo"}}}}'
)

MULTI_KIND_YAML = "\n".join(
    [
        '{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "cfg"}}',
        "---",
        '{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "dep-a", "namespace": "ns1"}}',
        "---",
        '{"apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": {"name": "sts-a", "namespace": "ns1"}}',
        "---",
        '{"apiVersion": "apps/v1", "kind": "DaemonSet", "metadata": {"name": "ds-a", "namespace": "ns1"}}',
    ]
)

TWO_DEPLOYMENTS_YAML = "\n".join(
    [
        '{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "dep-a", "namespace": "ns1"}}',
        "---",
        '{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "dep-b", "namespace": "ns1"}}',
    ]
)

THREE_DEPLOYMENTS_WITH_SELECTORS_YAML = "\n".join(
    [
        '{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "dep-a", "namespace": "ns1"}, '
        '"spec": {"selector": {"matchLabels": {"app": "demo-a"}}}}',
        "---",
        '{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "dep-b", "namespace": "ns1"}, '
        '"spec": {"selector": {"matchLabels": {"app": "demo-b"}}}}',
        "---",
        '{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "dep-c", "namespace": "ns1"}, '
        '"spec": {"selector": {"matchLabels": {"app": "demo-c"}}}}',
    ]
)


@pytest.fixture
def provider():
    return KubernetesProvider(kubeconfig="fake-kubeconfig-yaml")


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _pods_json(reason=None, in_container=True):
    """A `kubectl get pods -o json` payload with a single pod, whose one
    container (or init container, when in_container=False) is either
    healthy (reason=None) or waiting with the given reason.
    """
    container_status = {"state": {"running": {}}} if reason is None else {"state": {"waiting": {"reason": reason}}}
    key = "containerStatuses" if in_container else "initContainerStatuses"
    other_key = "initContainerStatuses" if in_container else "containerStatuses"
    return json.dumps(
        {"items": [{"metadata": {"name": "pod-1"}, "status": {key: [container_status], other_key: []}}]}
    )


class _FakePopen:
    """Stand-in for the subprocess.Popen driving `kubectl rollout status`.
    `poll_sequence` is consumed in order by successive .poll() calls — None
    means "still running", an int means "exited with this code". Once
    exhausted, .poll() keeps returning the last value seen (so a manifest
    that "never exits on its own" just needs poll_sequence=[None]).
    """

    def __init__(self, poll_sequence=(0,), stdout="", stderr=""):
        self._remaining_polls = list(poll_sequence)
        self._last = None
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.args = None

    def poll(self):
        if self._remaining_polls:
            self._last = self._remaining_polls.pop(0)
        self.returncode = self._last
        return self._last

    def communicate(self):
        return self._stdout, self._stderr

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


def _popen_factory(*fakes):
    """monkeypatch target for subprocess.Popen — hands out each fake in
    order for successive Popen(...) calls (one per rollout target),
    recording the argv each was constructed with onto `.args`.
    """
    queue = list(fakes)

    def _fake_popen(args, stdout=None, stderr=None, text=None, env=None):
        fake = queue.pop(0)
        fake.args = args
        return fake

    return _fake_popen


class TestApplySkipsWait:
    def test_no_timeout_skips_rollout_wait_entirely(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            return _completed(returncode=0, stdout="applied\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=None)

        assert result.success is True
        assert len(calls) == 1
        assert calls[0] == ["kubectl", "apply", "-f", "-"]

    def test_zero_timeout_skips_rollout_wait(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            return _completed(returncode=0, stdout="applied\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=0)

        assert result.success is True
        assert len(calls) == 1

    def test_configmap_only_manifest_skips_wait_even_with_timeout_set(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            return _completed(returncode=0, stdout="applied\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(CONFIGMAP_ONLY_YAML, wait_timeout_seconds=300)

        assert result.success is True
        assert len(calls) == 1

    def test_apply_failure_never_calls_rollout_status(self, provider, monkeypatch):
        calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            calls.append(args)
            return _completed(returncode=1, stderr="apply failed")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = provider.apply(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=300)

        assert result.success is False
        assert len(calls) == 1


class TestApplyWaitsForRollout:
    def test_runs_rollout_status_for_deployment_with_namespace(self, provider, monkeypatch):
        apply_calls = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            apply_calls.append(args)
            return _completed(returncode=0, stdout="applied\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        fake_process = _FakePopen(poll_sequence=[0], stdout='deployment "app-abc" successfully rolled out\n')
        monkeypatch.setattr(subprocess, "Popen", _popen_factory(fake_process))

        result = provider.apply(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=300)

        assert result.success is True
        assert len(apply_calls) == 1
        assert fake_process.args == [
            "kubectl",
            "rollout",
            "status",
            "deployment/app-abc",
            "--timeout=300s",
            "-n",
            "prod",
        ]
        assert "applied" in result.log
        assert "successfully rolled out" in result.log

    def test_omits_namespace_flag_when_manifest_has_none(self, provider, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=0))
        fake_process = _FakePopen(poll_sequence=[0])
        monkeypatch.setattr(subprocess, "Popen", _popen_factory(fake_process))

        result = provider.apply(DEPLOYMENT_NO_NAMESPACE_YAML, wait_timeout_seconds=300)

        assert result.success is True
        assert fake_process.args == ["kubectl", "rollout", "status", "deployment/app-abc", "--timeout=300s"]

    def test_rollout_status_failure_marks_result_failed(self, provider, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=0))
        fake_process = _FakePopen(poll_sequence=[1], stderr="error: timed out waiting for the condition\n")
        monkeypatch.setattr(subprocess, "Popen", _popen_factory(fake_process))

        result = provider.apply(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=300)

        assert result.success is False
        assert "deployment/app-abc" in result.error
        assert "300s" in result.error

    def test_waits_for_every_workload_kind_and_ignores_configmap(self, provider, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=0))
        fakes = [_FakePopen(poll_sequence=[0]) for _ in range(3)]
        monkeypatch.setattr(subprocess, "Popen", _popen_factory(*fakes))

        result = provider.apply(MULTI_KIND_YAML, wait_timeout_seconds=120)

        assert result.success is True
        resource_refs = {fake.args[3] for fake in fakes}
        assert resource_refs == {"deployment/dep-a", "statefulset/sts-a", "daemonset/ds-a"}

    def test_one_failing_resource_among_several_fails_the_whole_result(self, provider, monkeypatch):
        # Deliberately exercising the *non*-fail-fast, ordinary-failure path:
        # neither Deployment here has a spec.selector.matchLabels, so no
        # fail-fast probe ever runs — both targets are still attempted, no
        # short-circuit. Don't confuse this with TestFailFastAbortsRollout's
        # abort-on-fail-fast tests below, which use selector-bearing
        # manifests specifically to exercise the new short-circuit path.
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=0))
        fake_a = _FakePopen(poll_sequence=[0])
        fake_b = _FakePopen(poll_sequence=[1], stderr="failed")
        monkeypatch.setattr(subprocess, "Popen", _popen_factory(fake_a, fake_b))

        result = provider.apply(TWO_DEPLOYMENTS_YAML, wait_timeout_seconds=120)

        assert result.success is False
        assert fake_a.args[3] == "deployment/dep-a"
        assert fake_b.args[3] == "deployment/dep-b"
        assert "dep-b" in result.error

    def test_shared_deadline_is_not_reissued_per_resource(self, provider, monkeypatch):
        # First call to time.monotonic() establishes the deadline; simulate
        # a lot of time passing before the second resource's turn so its
        # own --timeout= reflects the shrunk remaining budget, not the full
        # wait_timeout_seconds again. The extra time.monotonic() calls the
        # new hard-deadline check makes are harmless here (both fake
        # processes exit on their very first poll(), before ever reaching
        # that check) and fall back to the iterator's trailing default.
        monotonic_values = iter([1000.0, 1000.0, 1080.0, 1080.0])

        def _fake_monotonic():
            return next(monotonic_values, 1080.0)

        monkeypatch.setattr(kp_module.time, "monotonic", _fake_monotonic)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=0))
        fake_a = _FakePopen(poll_sequence=[0])
        fake_b = _FakePopen(poll_sequence=[0])
        monkeypatch.setattr(subprocess, "Popen", _popen_factory(fake_a, fake_b))

        result = provider.apply(TWO_DEPLOYMENTS_YAML, wait_timeout_seconds=100)

        assert result.success is True
        first_timeout = next(a for a in fake_a.args if a.startswith("--timeout="))
        second_timeout = next(a for a in fake_b.args if a.startswith("--timeout="))
        assert first_timeout == "--timeout=100s"
        # Deadline = 1000 + 100 = 1100; second call starts at monotonic()=1080 -> remaining ~20s.
        assert second_timeout == "--timeout=20s"


class TestFailFastAbortsRollout:
    def test_fail_fast_reason_aborts_before_the_timeout(self, provider, monkeypatch):
        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            if args[1] == "apply":
                return _completed(returncode=0)
            if args[1] == "get":
                return _completed(returncode=0, stdout=_pods_json(reason="CrashLoopBackOff"))
            raise AssertionError(f"unexpected subprocess.run call: {args}")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        sleep_calls = []
        monkeypatch.setattr(time, "sleep", lambda seconds: sleep_calls.append(seconds))
        # Never exits on its own — proves the abort came from the fail-fast
        # probe, not from the rollout eventually finishing.
        fake_process = _FakePopen(poll_sequence=[None])
        monkeypatch.setattr(subprocess, "Popen", _popen_factory(fake_process))

        result = provider.apply(DEPLOYMENT_WITH_SELECTOR_YAML, wait_timeout_seconds=300)

        assert result.success is False
        assert "CrashLoopBackOff" in result.error
        assert fake_process.terminated is True
        # Detected on the very first check, before ever sleeping — nowhere
        # near what waiting out a 300s timeout would require.
        assert len(sleep_calls) == 0

    def test_fail_fast_on_one_target_aborts_remaining_targets(self, provider, monkeypatch):
        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            if args[1] == "apply":
                return _completed(returncode=0)
            if args[1] == "get":
                selector = args[4]
                reason = "ImagePullBackOff" if "demo-b" in selector else None
                return _completed(returncode=0, stdout=_pods_json(reason=reason))
            raise AssertionError(f"unexpected subprocess.run call: {args}")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        monkeypatch.setattr(time, "sleep", lambda seconds: None)

        fake_a = _FakePopen(poll_sequence=[0])  # dep-a: healthy, exits immediately
        fake_b = _FakePopen(poll_sequence=[None])  # dep-b: never exits on its own
        popen_calls = []

        def _fake_popen(args, stdout=None, stderr=None, text=None, env=None):
            popen_calls.append(args)
            if args[3] == "deployment/dep-a":
                fake_a.args = args
                return fake_a
            if args[3] == "deployment/dep-b":
                fake_b.args = args
                return fake_b
            raise AssertionError("dep-c should never be started once dep-b fails fast")

        monkeypatch.setattr(subprocess, "Popen", _fake_popen)

        result = provider.apply(THREE_DEPLOYMENTS_WITH_SELECTORS_YAML, wait_timeout_seconds=300)

        assert result.success is False
        assert "ImagePullBackOff" in result.error
        assert len(popen_calls) == 2  # dep-c never started
        assert fake_b.terminated is True

    def test_no_selector_falls_back_to_timeout_only_behavior(self, provider, monkeypatch):
        get_pods_called = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            if args[1] == "apply":
                return _completed(returncode=0)
            if args[1] == "get":
                get_pods_called.append(args)
                return _completed(returncode=0, stdout=_pods_json(reason="CrashLoopBackOff"))
            raise AssertionError(f"unexpected subprocess.run call: {args}")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        clock = {"now": 1000.0}
        monkeypatch.setattr(kp_module.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(kp_module.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))

        # No spec.selector.matchLabels on this manifest at all, so
        # match_labels is empty and the fail-fast probe must never run —
        # confirmed below by get_pods_called staying empty even though the
        # fake probe above would happily report CrashLoopBackOff if called.
        fake_process = _FakePopen(poll_sequence=[None] * 20)
        monkeypatch.setattr(subprocess, "Popen", _popen_factory(fake_process))

        result = provider.apply(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=10)

        assert result.success is False
        assert "CrashLoopBackOff" not in result.error
        assert get_pods_called == []
        assert fake_process.terminated is True

    def test_pod_probe_error_falls_back_to_timeout_only(self, provider, monkeypatch):
        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            if args[1] == "apply":
                return _completed(returncode=0)
            if args[1] == "get":
                return _completed(returncode=1, stderr="unexpected error")
            raise AssertionError(f"unexpected subprocess.run call: {args}")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        clock = {"now": 1000.0}
        monkeypatch.setattr(kp_module.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(kp_module.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))

        fake_process = _FakePopen(poll_sequence=[None, None, 0])
        monkeypatch.setattr(subprocess, "Popen", _popen_factory(fake_process))

        result = provider.apply(DEPLOYMENT_WITH_SELECTOR_YAML, wait_timeout_seconds=300)

        assert result.success is True

    def test_pod_probe_timeout_expired_falls_back_to_timeout_only(self, provider, monkeypatch):
        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            if args[1] == "apply":
                return _completed(returncode=0)
            if args[1] == "get":
                raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)
            raise AssertionError(f"unexpected subprocess.run call: {args}")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        clock = {"now": 1000.0}
        monkeypatch.setattr(kp_module.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(kp_module.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))

        fake_process = _FakePopen(poll_sequence=[None, 0])
        monkeypatch.setattr(subprocess, "Popen", _popen_factory(fake_process))

        result = provider.apply(DEPLOYMENT_WITH_SELECTOR_YAML, wait_timeout_seconds=300)

        assert result.success is True

    def test_fail_fast_never_checked_after_process_already_exited(self, provider, monkeypatch):
        get_pods_called = []

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            if args[1] == "apply":
                return _completed(returncode=0)
            if args[1] == "get":
                get_pods_called.append(args)
                return _completed(returncode=0, stdout=_pods_json(reason="CrashLoopBackOff"))
            raise AssertionError(f"unexpected subprocess.run call: {args}")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        # Exits successfully on the very first poll() — no iteration where a
        # fail-fast probe would ever get a chance to run.
        fake_process = _FakePopen(poll_sequence=[0])
        monkeypatch.setattr(subprocess, "Popen", _popen_factory(fake_process))

        result = provider.apply(DEPLOYMENT_WITH_SELECTOR_YAML, wait_timeout_seconds=300)

        assert result.success is True
        assert get_pods_called == []


class TestPodFailFastReason:
    @pytest.mark.parametrize("reason", sorted(kp_module.FAIL_FAST_WAITING_REASONS))
    def test_each_fail_fast_reason_is_detected(self, provider, monkeypatch, reason):
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout=_pods_json(reason=reason))
        )

        detected_reason, pod_name = provider._pod_fail_fast_reason(namespace="prod", match_labels={"app": "demo"})

        assert detected_reason == reason
        assert pod_name == "pod-1"

    def test_healthy_pod_is_not_flagged(self, provider, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout=_pods_json(reason=None)))

        reason, pod_name = provider._pod_fail_fast_reason(namespace="prod", match_labels={"app": "demo"})

        assert reason is None
        assert pod_name is None

    def test_init_container_fail_fast_is_detected(self, provider, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: _completed(
                returncode=0, stdout=_pods_json(reason="CreateContainerConfigError", in_container=False)
            ),
        )

        reason, pod_name = provider._pod_fail_fast_reason(namespace=None, match_labels={"app": "demo"})

        assert reason == "CreateContainerConfigError"
        assert pod_name == "pod-1"


class TestRestartThreadsWaitTimeoutIntoApply:
    def test_restart_passes_wait_timeout_seconds_through_to_apply(self, provider, monkeypatch):
        captured = {}

        def _fake_apply(self, manifest_yaml, wait_timeout_seconds=None):
            captured["wait_timeout_seconds"] = wait_timeout_seconds
            from app.services.deployment.base import DeployResult

            return DeployResult(success=True, log="applied\n")

        def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
            return _completed(returncode=0, stdout="deleted\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        monkeypatch.setattr(KubernetesProvider, "apply", _fake_apply)

        result = provider.restart(DEPLOYMENT_WITH_NAMESPACE_YAML, wait_timeout_seconds=42)

        assert result.success is True
        assert captured["wait_timeout_seconds"] == 42


class TestCustomAPIProviderIgnoresWaitTimeout:
    def test_apply_accepts_and_ignores_wait_timeout_seconds(self, monkeypatch):
        import requests

        provider = CustomAPIProvider(api_url="https://agent.example.com/apply")

        class _FakeResponse:
            status_code = 200
            text = "ok"

        def _fake_post(url, data=None, headers=None, timeout=None):
            return _FakeResponse()

        monkeypatch.setattr(requests, "post", _fake_post)
        result = provider.apply(CONFIGMAP_ONLY_YAML, wait_timeout_seconds=300)

        assert result.success is True
