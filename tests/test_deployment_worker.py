import threading
from datetime import datetime, timedelta

from app.extensions import db
from app.models import (
    Builder,
    BuildBatch,
    DeploymentExecution,
    DeploymentManifest,
    DeploymentManifestVersionBinding,
    DeploymentRun,
    DeploymentServer,
    GitSource,
    ImageBuild,
    RegistryTarget,
    Repository,
    Version,
    VersionType,
)
from app.services.deployment import worker as deployment_worker
from app.services.deployment.base import DeployResult
from app.services.deployment.kubernetes_provider import KubernetesProvider
from app.services.deployment.worker import (
    HEARTBEAT_STALE_SECONDS,
    _claim_next_job,
    _heartbeat_tick,
    _live_check_candidates,
    _reap_stale_running_job,
    _refresh_live_status,
    _run_deployment,
    _update_run_status,
    enqueue_deployment_run,
    get_available_update,
    get_current_deployment,
    get_engine_status,
    get_run_progress,
    is_currently_deployed,
)
from app.utils.crypto import encrypt


def _make_entities(app, num_servers=1):
    with app.app_context():
        git_source = GitSource(name="gs", provider_type="github", encrypted_token="x")
        db.session.add(git_source)
        db.session.flush()
        repository = Repository(git_source_id=git_source.id, full_name="org/app", local_path="/tmp/app", status="ready")
        db.session.add(repository)
        registry_target = RegistryTarget(name="reg", provider_type="dockerhub", username="u")
        db.session.add(registry_target)
        version_type = VersionType(name="DEV")
        db.session.add(version_type)
        db.session.flush()
        version = Version(name="svc", version_type_id=version_type.id)
        db.session.add(version)
        db.session.flush()
        builder = Builder(
            name="b1", version_id=version.id, repository_id=repository.id, default_branch="main",
            registry_target_id=registry_target.id,
        )
        db.session.add(builder)
        db.session.flush()
        batch = BuildBatch(version_id=version.id, bump_type="patch", status="success", full_version_string="DEV.0.0.1.x")
        db.session.add(batch)
        db.session.flush()
        build = ImageBuild(batch_id=batch.id, builder_id=builder.id, branch_used="main", status="success", image_tag="u/app:DEV.0.0.1.x")
        db.session.add(build)

        manifest = DeploymentManifest(name="m1", yaml_content="image: {{SYS:VERSION}}")
        db.session.add(manifest)
        db.session.flush()
        db.session.add(DeploymentManifestVersionBinding(manifest_id=manifest.id, placeholder_key="default", builder_id=builder.id))

        servers = [
            DeploymentServer(name=f"srv{i}", connection_type="kube", encrypted_credentials=encrypt("fake-kubeconfig"))
            for i in range(num_servers)
        ]
        db.session.add_all(servers)
        db.session.flush()
        manifest.target_servers = servers
        db.session.commit()

        return {"manifest_id": manifest.id, "server_ids": [s.id for s in servers], "builder_id": builder.id}


class TestEnqueueDeploymentRun:
    def test_creates_one_execution_per_manifest_server_pair(self, app):
        entities = _make_entities(app, num_servers=2)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, group_name="grp")
            assert count == 2
            assert run.status == "queued"
            assert run.group_name == "grp"
            executions = DeploymentExecution.query.filter_by(run_id=run.id).all()
            assert len(executions) == 2
            assert {e.status for e in executions} == {"queued"}


class TestClaimNextJob:
    def test_claims_the_oldest_queued_execution(self, app):
        entities = _make_entities(app)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            execution_id = DeploymentExecution.query.filter_by(run_id=run.id).first().id

        with app.app_context():
            claimed_id = _claim_next_job()
            assert claimed_id == execution_id
            execution = DeploymentExecution.query.get(claimed_id)
            assert execution.status == "running"
            assert execution.started_at is not None

    def test_returns_none_when_nothing_queued(self, app):
        with app.app_context():
            assert _claim_next_job() is None


def _enqueue_and_claim(app, entities):
    """Enqueue a single-execution deploy run and claim it — the realistic
    way to reach "running" for the reap/heartbeat tests below.
    """
    with app.app_context():
        manifest = DeploymentManifest.query.get(entities["manifest_id"])
        enqueue_deployment_run(manifests=[manifest], triggered_by=None)
        claimed_id = _claim_next_job()
        return claimed_id


class TestReapStaleRunningJob:
    def test_recent_heartbeat_is_not_reaped(self, app):
        entities = _make_entities(app)
        execution_id = _enqueue_and_claim(app, entities)
        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            execution.heartbeat_at = datetime.utcnow()
            db.session.commit()

            _reap_stale_running_job()
            assert DeploymentExecution.query.get(execution_id).status == "running"

    def test_stale_heartbeat_is_reaped_and_run_status_updates(self, app):
        entities = _make_entities(app)
        execution_id = _enqueue_and_claim(app, entities)
        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            run_id = execution.run_id
            execution.heartbeat_at = datetime.utcnow() - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 1)
            db.session.commit()

            _reap_stale_running_job()

            execution = DeploymentExecution.query.get(execution_id)
            assert execution.status == "failed"
            assert execution.finished_at is not None
            assert "[reaper]" in execution.log
            assert DeploymentRun.query.get(run_id).status == "failed"

    def test_null_heartbeat_is_treated_as_stale(self, app):
        """A legacy pre-migration ghost row: heartbeat_at was never set."""
        entities = _make_entities(app)
        execution_id = _enqueue_and_claim(app, entities)
        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            execution.heartbeat_at = None
            db.session.commit()

            _reap_stale_running_job()
            assert DeploymentExecution.query.get(execution_id).status == "failed"

    def test_no_running_job_is_a_noop(self, app):
        with app.app_context():
            _reap_stale_running_job()  # must not raise

    def test_reaping_frees_the_slot_for_the_next_queued_execution(self, app):
        entities = _make_entities(app)
        first_id = _enqueue_and_claim(app, entities)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            second_id = (
                DeploymentExecution.query.filter(DeploymentExecution.status == "queued").first().id
            )

            execution = DeploymentExecution.query.get(first_id)
            execution.heartbeat_at = datetime.utcnow() - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 1)
            db.session.commit()

            _reap_stale_running_job()
            assert _claim_next_job() == second_id

    def test_concurrent_reap_attempts_only_apply_once(self, app):
        """Mirrors build.worker's own concurrent-reap test — two processes'
        reap checks racing on the same stale row must not both apply the
        transition."""
        entities = _make_entities(app)
        execution_id = _enqueue_and_claim(app, entities)
        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            execution.heartbeat_at = datetime.utcnow() - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 1)
            db.session.commit()

        def attempt_reap():
            with app.app_context():
                _reap_stale_running_job()
                db.session.remove()

        threads = [threading.Thread(target=attempt_reap) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            assert execution.status == "failed"
            assert execution.log.count("[reaper]") == 1


class TestHeartbeatTick:
    def test_ticks_heartbeat_for_the_currently_owned_execution(self, app):
        entities = _make_entities(app)
        execution_id = _enqueue_and_claim(app, entities)
        with app.app_context():
            old_heartbeat = DeploymentExecution.query.get(execution_id).heartbeat_at

        deployment_worker._current_execution_id = execution_id
        try:
            _heartbeat_tick(app)
        finally:
            deployment_worker._current_execution_id = None

        with app.app_context():
            assert DeploymentExecution.query.get(execution_id).heartbeat_at > old_heartbeat

    def test_noop_when_no_execution_is_currently_owned(self, app):
        deployment_worker._current_execution_id = None
        _heartbeat_tick(app)  # must not raise


class TestRunDeployment:
    def test_successful_apply_marks_execution_success(self, app, monkeypatch):
        monkeypatch.setattr(KubernetesProvider, "apply", lambda self, yaml: DeployResult(success=True, log="applied ok"))
        entities = _make_entities(app)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            execution_id = DeploymentExecution.query.filter_by(run_id=run.id).first().id

        _run_deployment(app, execution_id)

        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            assert execution.status == "success"
            assert execution.resolved_version_string == "default=u/app:DEV.0.0.1.x"
            assert "u/app:DEV.0.0.1.x" in execution.rendered_yaml
            assert execution.finished_at is not None
            run = DeploymentRun.query.get(run.id)
            assert run.status == "success"

    def test_provider_failure_marks_execution_failed(self, app, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider, "apply", lambda self, yaml: DeployResult(success=False, log="", error="apply rejected")
        )
        entities = _make_entities(app)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            execution_id = DeploymentExecution.query.filter_by(run_id=run.id).first().id

        _run_deployment(app, execution_id)

        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            assert execution.status == "failed"
            assert "apply rejected" in execution.log

    def test_unresolvable_placeholder_marks_execution_failed_without_raising(self, app):
        with app.app_context():
            server = DeploymentServer(name="srv", connection_type="kube")
            manifest = DeploymentManifest(name="m1", yaml_content="image: {{SYS:VERSION:unbound}}")
            manifest.target_servers = [server]
            db.session.add_all([server, manifest])
            db.session.commit()
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            execution_id = DeploymentExecution.query.filter_by(run_id=run.id).first().id

        _run_deployment(app, execution_id)  # must not raise

        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            assert execution.status == "failed"
            assert "unbound" in execution.log


class TestUpdateRunStatusAbortOnFailure:
    def test_all_success_yields_run_success(self, app):
        entities = _make_entities(app, num_servers=2)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            for execution in DeploymentExecution.query.filter_by(run_id=run.id).all():
                execution.status = "success"
            db.session.commit()

            _update_run_status(run.id)
            assert DeploymentRun.query.get(run.id).status == "success"

    def test_one_failure_marks_remaining_queued_as_skipped_and_run_partial_failure(self, app):
        entities = _make_entities(app, num_servers=3)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            executions = DeploymentExecution.query.filter_by(run_id=run.id).all()
            executions[0].status = "success"
            executions[1].status = "failed"
            # executions[2] stays "queued"
            db.session.commit()

            _update_run_status(run.id)

            executions = DeploymentExecution.query.filter_by(run_id=run.id).all()
            statuses = {e.status for e in executions}
            assert statuses == {"success", "failed", "skipped"}
            assert DeploymentRun.query.get(run.id).status == "partial_failure"

    def test_all_failed_yields_run_failed(self, app):
        entities = _make_entities(app, num_servers=2)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            executions = DeploymentExecution.query.filter_by(run_id=run.id).all()
            executions[0].status = "failed"
            # executions[1] stays "queued" -> should become "skipped"
            db.session.commit()

            _update_run_status(run.id)

            assert DeploymentRun.query.get(run.id).status == "failed"
            statuses = {e.status for e in DeploymentExecution.query.filter_by(run_id=run.id).all()}
            assert statuses == {"failed", "skipped"}

    def test_a_still_running_execution_keeps_run_running_even_after_a_sibling_failure(self, app):
        entities = _make_entities(app, num_servers=3)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            executions = DeploymentExecution.query.filter_by(run_id=run.id).all()
            executions[0].status = "running"
            executions[1].status = "failed"
            db.session.commit()

            _update_run_status(run.id)

            assert DeploymentRun.query.get(run.id).status == "running"
            executions = DeploymentExecution.query.filter_by(run_id=run.id).all()
            # The third (still-queued) execution is skipped even though the
            # run itself isn't done yet — no new work starts once a failure
            # has occurred, regardless of what's still in flight.
            remaining = [e for e in executions if e.status not in ("running", "failed")]
            assert all(e.status == "skipped" for e in remaining)


class TestEngineStatusAndProgress:
    def test_get_engine_status_reports_busy_and_queue(self, app):
        entities = _make_entities(app, num_servers=2)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            _claim_next_job()

            status = get_engine_status()
            assert status["busy"] is True
            assert status["running"] is not None
            assert len(status["queued"]) == 1

    def test_get_run_progress_counts_finished_and_succeeded(self, app):
        entities = _make_entities(app, num_servers=2)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            executions = DeploymentExecution.query.filter_by(run_id=run.id).all()
            executions[0].status = "success"
            db.session.commit()

            progress = get_run_progress(run.id)
            assert progress == {"total": 2, "finished": 1, "succeeded": 1}


class TestCurrentDeployment:
    def test_never_deployed_pair_is_none_and_not_currently_deployed(self, app):
        entities = _make_entities(app)
        with app.app_context():
            manifest_id, server_id = entities["manifest_id"], entities["server_ids"][0]
            assert get_current_deployment(manifest_id, server_id) is None
            assert is_currently_deployed(manifest_id, server_id) is False

    def test_successful_deploy_makes_pair_currently_deployed(self, app):
        entities = _make_entities(app)
        with app.app_context():
            manifest_id, server_id = entities["manifest_id"], entities["server_ids"][0]
            manifest = DeploymentManifest.query.get(manifest_id)
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            execution = DeploymentExecution.query.filter_by(run_id=run.id).first()
            execution.status = "success"
            db.session.commit()

            assert is_currently_deployed(manifest_id, server_id) is True
            assert get_current_deployment(manifest_id, server_id).id == execution.id

    def test_failed_attempt_does_not_count_as_current(self, app):
        entities = _make_entities(app)
        with app.app_context():
            manifest_id, server_id = entities["manifest_id"], entities["server_ids"][0]
            manifest = DeploymentManifest.query.get(manifest_id)
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            execution = DeploymentExecution.query.filter_by(run_id=run.id).first()
            execution.status = "failed"
            db.session.commit()

            assert is_currently_deployed(manifest_id, server_id) is False
            assert get_current_deployment(manifest_id, server_id) is None

    def test_a_failed_stop_leaves_the_prior_successful_deploy_as_current(self, app):
        """A stop execution that itself fails (delete errored out) shouldn't
        flip "currently deployed" to false — the resources presumably are
        still there since the delete never actually succeeded.
        """
        entities = _make_entities(app)
        with app.app_context():
            manifest_id, server_id = entities["manifest_id"], entities["server_ids"][0]
            manifest = DeploymentManifest.query.get(manifest_id)

            deploy_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            deploy_execution = DeploymentExecution.query.filter_by(run_id=deploy_run.id).first()
            deploy_execution.status = "success"
            db.session.commit()

            stop_run, stop_count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="stop")
            assert stop_count == 1
            stop_execution = DeploymentExecution.query.filter_by(run_id=stop_run.id).first()
            assert stop_execution.source_execution_id == deploy_execution.id
            stop_execution.status = "failed"
            db.session.commit()

            assert is_currently_deployed(manifest_id, server_id) is True
            assert get_current_deployment(manifest_id, server_id).id == deploy_execution.id

    def test_successful_stop_flips_pair_to_not_currently_deployed(self, app):
        entities = _make_entities(app)
        with app.app_context():
            manifest_id, server_id = entities["manifest_id"], entities["server_ids"][0]
            manifest = DeploymentManifest.query.get(manifest_id)

            deploy_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            deploy_execution = DeploymentExecution.query.filter_by(run_id=deploy_run.id).first()
            deploy_execution.status = "success"
            db.session.commit()

            stop_run, stop_count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="stop")
            assert stop_count == 1
            stop_execution = DeploymentExecution.query.filter_by(run_id=stop_run.id).first()
            stop_execution.status = "success"
            db.session.commit()

            assert is_currently_deployed(manifest_id, server_id) is False


class TestEnqueueStopRun:
    def test_skips_pairs_with_nothing_currently_deployed(self, app):
        entities = _make_entities(app, num_servers=2)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="stop")
            assert count == 0
            assert DeploymentExecution.query.filter_by(run_id=run.id).count() == 0
            assert run.action == "stop"


class TestRunDeploymentStopAction:
    def test_successful_delete_marks_stop_execution_success(self, app, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider, "delete", lambda self, yaml: DeployResult(success=True, log="deleted ok")
        )
        entities = _make_entities(app)
        with app.app_context():
            manifest_id, server_id = entities["manifest_id"], entities["server_ids"][0]
            manifest = DeploymentManifest.query.get(manifest_id)

            deploy_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            deploy_execution = DeploymentExecution.query.filter_by(run_id=deploy_run.id).first()
            deploy_execution.status = "success"
            deploy_execution.rendered_yaml = "image: u/app:DEV.0.0.1.x"
            db.session.commit()

            stop_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="stop")
            stop_execution_id = DeploymentExecution.query.filter_by(run_id=stop_run.id).first().id

        _run_deployment(app, stop_execution_id)

        with app.app_context():
            stop_execution = DeploymentExecution.query.get(stop_execution_id)
            assert stop_execution.status == "success"
            assert "deleted ok" in stop_execution.log
            assert is_currently_deployed(manifest_id, server_id) is False

    def test_delete_failure_marks_stop_execution_failed(self, app, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider, "delete", lambda self, yaml: DeployResult(success=False, log="", error="delete rejected")
        )
        entities = _make_entities(app)
        with app.app_context():
            manifest_id, server_id = entities["manifest_id"], entities["server_ids"][0]
            manifest = DeploymentManifest.query.get(manifest_id)

            deploy_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            deploy_execution = DeploymentExecution.query.filter_by(run_id=deploy_run.id).first()
            deploy_execution.status = "success"
            deploy_execution.rendered_yaml = "image: u/app:DEV.0.0.1.x"
            db.session.commit()

            stop_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="stop")
            stop_execution_id = DeploymentExecution.query.filter_by(run_id=stop_run.id).first().id

        _run_deployment(app, stop_execution_id)

        with app.app_context():
            stop_execution = DeploymentExecution.query.get(stop_execution_id)
            assert stop_execution.status == "failed"
            assert "delete rejected" in stop_execution.log
            # The delete failed — the original deploy is still the "current" one.
            assert is_currently_deployed(manifest_id, server_id) is True


class TestLiveStatusPoller:
    def test_candidates_only_include_currently_deployed_pairs(self, app):
        entities = _make_entities(app, num_servers=2)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            executions = DeploymentExecution.query.filter_by(run_id=run.id).all()
            executions[0].status = "success"
            executions[1].status = "failed"
            db.session.commit()

            candidates = _live_check_candidates()
            assert [c.id for c in candidates] == [executions[0].id]

    def test_refresh_updates_live_status_from_provider(self, app, monkeypatch):
        monkeypatch.setattr(KubernetesProvider, "get_live_status", lambda self, yaml: "live")
        entities = _make_entities(app)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            execution = DeploymentExecution.query.filter_by(run_id=run.id).first()
            execution.status = "success"
            execution.rendered_yaml = "image: nginx"
            db.session.commit()
            execution_id = execution.id

        _refresh_live_status(app)

        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            assert execution.live_status == "live"
            assert execution.live_checked_at is not None

    def test_refresh_skips_a_provider_that_cant_tell(self, app, monkeypatch):
        def _raise_not_implemented(self, yaml):
            raise NotImplementedError

        monkeypatch.setattr(KubernetesProvider, "get_live_status", _raise_not_implemented)
        entities = _make_entities(app)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            execution = DeploymentExecution.query.filter_by(run_id=run.id).first()
            execution.status = "success"
            execution.rendered_yaml = "image: nginx"
            db.session.commit()
            execution_id = execution.id

        _refresh_live_status(app)  # must not raise

        with app.app_context():
            execution = DeploymentExecution.query.get(execution_id)
            assert execution.live_status is None
            assert execution.live_checked_at is None


class TestRestartAction:
    def test_enqueue_skips_pairs_with_nothing_currently_deployed(self, app):
        entities = _make_entities(app)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="restart")
            assert count == 0
            assert run.action == "restart"

    def test_enqueue_chains_source_execution_to_current_deployment(self, app):
        entities = _make_entities(app)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            deploy_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            deploy_execution = DeploymentExecution.query.filter_by(run_id=deploy_run.id).first()
            deploy_execution.status = "success"
            deploy_execution.rendered_yaml = "image: u/app:DEV.0.0.1.x"
            db.session.commit()

            restart_run, count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="restart")
            assert count == 1
            restart_execution = DeploymentExecution.query.filter_by(run_id=restart_run.id).first()
            assert restart_execution.source_execution_id == deploy_execution.id

    def test_successful_restart_does_not_undeploy(self, app, monkeypatch):
        """A restart tears down and reapplies — the manifest must still read
        as currently deployed afterward, unlike a successful stop.
        """
        monkeypatch.setattr(
            KubernetesProvider, "restart", lambda self, yaml: DeployResult(success=True, log="restarted ok")
        )
        entities = _make_entities(app)
        with app.app_context():
            manifest_id, server_id = entities["manifest_id"], entities["server_ids"][0]
            manifest = DeploymentManifest.query.get(manifest_id)

            deploy_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            deploy_execution = DeploymentExecution.query.filter_by(run_id=deploy_run.id).first()
            deploy_execution.status = "success"
            deploy_execution.rendered_yaml = "image: u/app:DEV.0.0.1.x"
            deploy_execution.resolved_version_string = "default=u/app:DEV.0.0.1.x"
            db.session.commit()

            restart_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="restart")
            restart_execution_id = DeploymentExecution.query.filter_by(run_id=restart_run.id).first().id

        _run_deployment(app, restart_execution_id)

        with app.app_context():
            restart_execution = DeploymentExecution.query.get(restart_execution_id)
            assert restart_execution.status == "success"
            assert "restarted ok" in restart_execution.log
            # Freshly re-resolved (same value here since nothing changed
            # between deploy and restart) — see test_restart_reresolves_
            # instead_of_replaying_stale_content below for the case where
            # something *did* change.
            assert restart_execution.rendered_yaml == "image: u/app:DEV.0.0.1.x"
            assert restart_execution.resolved_version_string == "default=u/app:DEV.0.0.1.x"
            assert is_currently_deployed(manifest_id, server_id) is True
            assert get_current_deployment(manifest_id, server_id).id == restart_execution.id

    def test_restart_reresolves_instead_of_replaying_stale_content(self, app, monkeypatch):
        """A restart must re-render the manifest fresh (e.g. picking up an
        edited Secret in yaml_content, or a newer image tag), not blindly
        replay whatever was applied by the deploy it's restarting."""
        applied_yaml = []
        monkeypatch.setattr(
            KubernetesProvider,
            "restart",
            lambda self, yaml: applied_yaml.append(yaml) or DeployResult(success=True, log="restarted ok"),
        )
        entities = _make_entities(app)
        with app.app_context():
            manifest_id, server_id = entities["manifest_id"], entities["server_ids"][0]
            manifest = DeploymentManifest.query.get(manifest_id)

            deploy_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            deploy_execution = DeploymentExecution.query.filter_by(run_id=deploy_run.id).first()
            deploy_execution.status = "success"
            deploy_execution.rendered_yaml = "image: u/app:DEV.0.0.1.x"
            deploy_execution.resolved_version_string = "default=u/app:DEV.0.0.1.x"
            db.session.commit()

            # Simulate something changing after the deploy, the way editing
            # a manifest's own Secret content or landing a newer image
            # build would: edit the manifest's yaml_content directly.
            manifest = DeploymentManifest.query.get(manifest_id)
            manifest.yaml_content = "image: {{SYS:VERSION}}\nsecret: new-value"
            db.session.commit()

            restart_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="restart")
            restart_execution_id = DeploymentExecution.query.filter_by(run_id=restart_run.id).first().id

        _run_deployment(app, restart_execution_id)

        with app.app_context():
            restart_execution = DeploymentExecution.query.get(restart_execution_id)
            assert restart_execution.status == "success"
            assert "secret: new-value" in restart_execution.rendered_yaml
            assert "secret: new-value" in applied_yaml[0]

    def test_restart_failure_marks_execution_failed_and_still_currently_deployed(self, app, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider, "restart", lambda self, yaml: DeployResult(success=False, log="", error="restart rejected")
        )
        entities = _make_entities(app)
        with app.app_context():
            manifest_id, server_id = entities["manifest_id"], entities["server_ids"][0]
            manifest = DeploymentManifest.query.get(manifest_id)

            deploy_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            deploy_execution = DeploymentExecution.query.filter_by(run_id=deploy_run.id).first()
            deploy_execution.status = "success"
            deploy_execution.rendered_yaml = "image: u/app:DEV.0.0.1.x"
            db.session.commit()
            deploy_execution_id = deploy_execution.id

            restart_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="restart")
            restart_execution_id = DeploymentExecution.query.filter_by(run_id=restart_run.id).first().id

        _run_deployment(app, restart_execution_id)

        with app.app_context():
            restart_execution = DeploymentExecution.query.get(restart_execution_id)
            assert restart_execution.status == "failed"
            assert "restart rejected" in restart_execution.log
            assert is_currently_deployed(manifest_id, server_id) is True
            assert get_current_deployment(manifest_id, server_id).id == deploy_execution_id

    def test_can_restart_again_after_a_restart_via_chained_source(self, app, monkeypatch):
        """A second restart must still find something "currently deployed"
        to restart (enqueue_deployment_run's source_execution_id gate),
        even though the first restart's own rendered_yaml came from a fresh
        re-resolve rather than being carried forward from its source."""
        monkeypatch.setattr(
            KubernetesProvider, "restart", lambda self, yaml: DeployResult(success=True, log="restarted ok")
        )
        entities = _make_entities(app)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])

            deploy_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            deploy_execution = DeploymentExecution.query.filter_by(run_id=deploy_run.id).first()
            deploy_execution.status = "success"
            deploy_execution.rendered_yaml = "image: u/app:DEV.0.0.1.x"
            db.session.commit()

            first_restart_run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="restart")
            first_restart_id = DeploymentExecution.query.filter_by(run_id=first_restart_run.id).first().id

        _run_deployment(app, first_restart_id)

        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            second_restart_run, count = enqueue_deployment_run(manifests=[manifest], triggered_by=None, action="restart")
            assert count == 1
            second_restart = DeploymentExecution.query.filter_by(run_id=second_restart_run.id).first()
            assert second_restart.source_execution_id == first_restart_id

        _run_deployment(app, second_restart.id)

        with app.app_context():
            second_restart = DeploymentExecution.query.get(second_restart.id)
            assert second_restart.status == "success"


class TestGetAvailableUpdate:
    def test_none_when_not_currently_deployed(self, app):
        entities = _make_entities(app)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            server = DeploymentServer.query.get(entities["server_ids"][0])
            assert get_available_update(manifest, server) is None

    def test_none_when_already_on_latest(self, app):
        entities = _make_entities(app)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            server = DeploymentServer.query.get(entities["server_ids"][0])

            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            execution = DeploymentExecution.query.filter_by(run_id=run.id).first()
            execution.status = "success"
            execution.resolved_version_string = "default=u/app:DEV.0.0.1.x"  # matches _make_entities' only build
            db.session.commit()

            assert get_available_update(manifest, server) is None

    def test_returns_new_version_string_when_a_newer_build_exists(self, app):
        entities = _make_entities(app)
        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            server = DeploymentServer.query.get(entities["server_ids"][0])

            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            execution = DeploymentExecution.query.filter_by(run_id=run.id).first()
            execution.status = "success"
            execution.resolved_version_string = "default=u/app:DEV.0.0.1.x"
            db.session.commit()

            # A newer successful build lands after the deploy.
            db.session.add(
                ImageBuild(
                    batch_id=ImageBuild.query.filter_by(builder_id=entities["builder_id"]).first().batch_id,
                    builder_id=entities["builder_id"],
                    branch_used="main",
                    status="success",
                    image_tag="u/app:DEV.0.0.2.x",
                )
            )
            db.session.commit()

            assert get_available_update(manifest, server) == "default=u/app:DEV.0.0.2.x"

    def test_none_when_manifest_unresolvable(self, app):
        """A manifest whose binding can't currently resolve (e.g. its
        Builder has no successful build) reports no update rather than
        raising — the button just shouldn't show.
        """
        with app.app_context():
            server = DeploymentServer(name="srv", connection_type="kube")
            manifest = DeploymentManifest(name="m1", yaml_content="image: {{SYS:VERSION:unbound}}")
            manifest.target_servers = [server]
            db.session.add_all([server, manifest])
            db.session.commit()

            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
            execution = DeploymentExecution.query.filter_by(run_id=run.id).first()
            execution.status = "success"
            db.session.commit()

            assert get_available_update(manifest, server) is None
