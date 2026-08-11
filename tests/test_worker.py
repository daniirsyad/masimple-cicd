import threading
from datetime import datetime, timedelta

from app.extensions import db
from app.models import (
    Builder,
    BuildBatch,
    ErrorLog,
    GitSource,
    ImageBuild,
    PromptTemplate,
    RegistryTarget,
    Repository,
    Version,
    VersionDocumentation,
    VersionType,
)
from app.services.build import worker as build_worker
from app.services.build.worker import (
    HEARTBEAT_STALE_SECONDS,
    _claim_next_job,
    _derive_image_name,
    _heartbeat_tick,
    _reap_stale_running_job,
    _run_build,
    _update_batch_status,
    enqueue_build_batch,
    get_batch_progress,
    get_engine_status,
    get_queue_position,
)


def _make_entities(app):
    """Repository + RegistryTarget + Version + Builder — the chain every
    ImageBuild now hangs off of (batch_id + builder_id, no repo_url/etc. of
    its own anymore).
    """
    with app.app_context():
        git_source = GitSource(name="conn", provider_type="github", encrypted_token="x")
        db.session.add(git_source)
        db.session.flush()
        repository = Repository(
            git_source_id=git_source.id,
            full_name="owner/myapp",
            local_path="/tmp/myapp",
            status="ready",
            default_branch="main",
        )
        db.session.add(repository)
        registry_target = RegistryTarget(name="reg", provider_type="dockerhub", username="myuser")
        db.session.add(registry_target)
        version_type = VersionType(name="DEV")
        db.session.add(version_type)
        db.session.flush()
        version = Version(name="svc", version_type_id=version_type.id)
        db.session.add(version)
        db.session.flush()
        builder = Builder(
            name="b1",
            version_id=version.id,
            repository_id=repository.id,
            default_branch="main",
            dockerfile_path="Dockerfile",
            registry_target_id=registry_target.id,
        )
        db.session.add(builder)
        db.session.commit()
        return {"version_id": version.id, "builder_id": builder.id}


def _make_queued_build(app, entities, branch="main", bump_type="patch"):
    with app.app_context():
        version = Version.query.get(entities["version_id"])
        builder = Builder.query.get(entities["builder_id"])
        batch = enqueue_build_batch(
            version=version,
            bump_type=bump_type,
            builder_branches=[(builder, branch)],
            object_=None,
            additional_description=None,
            requested_by=None,
        )
        return ImageBuild.query.filter_by(batch_id=batch.id).first().id


def _claim(build_id):
    """Claim `build_id` (must be next in the global FIFO queue) and assert it
    won — the realistic way to reach "running" now that the version bump
    happens at claim-time, not enqueue-time (manually setting
    build.status = "running" would skip that bump entirely).
    """
    claimed_id = _claim_next_job()
    assert claimed_id == build_id
    return ImageBuild.query.get(build_id)


class TestEnqueueBuildBatch:
    def test_does_not_bump_the_version_or_assign_a_version_string(self, app):
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            major_before = version.major
            builder = Builder.query.get(entities["builder_id"])
            batch = enqueue_build_batch(
                version=version,
                bump_type="patch",
                builder_branches=[(builder, "main")],
                object_=None,
                additional_description=None,
                requested_by=None,
            )
            assert batch.full_version_string is None
            assert batch.status == "queued"
            assert Version.query.get(entities["version_id"]).major == major_before

    def test_does_not_create_a_documentation_row(self, app):
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            builder = Builder.query.get(entities["builder_id"])
            batch = enqueue_build_batch(
                version=version,
                bump_type="patch",
                builder_branches=[(builder, "main")],
                object_=None,
                additional_description=None,
                requested_by=None,
            )
            assert VersionDocumentation.query.filter_by(batch_id=batch.id).first() is None

    def test_stages_object_change_type_and_additional_description_on_the_batch(self, app):
        entities = _make_entities(app)
        with app.app_context():
            from app.models import ChangeType

            change_type = ChangeType(name="Bug Fix")
            db.session.add(change_type)
            db.session.flush()

            version = Version.query.get(entities["version_id"])
            builder = Builder.query.get(entities["builder_id"])
            batch = enqueue_build_batch(
                version=version,
                bump_type="patch",
                builder_branches=[(builder, "main")],
                object_="checkout-flow",
                additional_description="manual notes",
                requested_by=None,
                change_type_id=change_type.id,
            )
            assert batch.object == "checkout-flow"
            assert batch.additional_description == "manual notes"
            assert batch.change_type_id == change_type.id


class TestDeriveImageName:
    def test_derives_from_full_name(self):
        assert _derive_image_name("owner/myapp") == "myapp"

    def test_full_name_with_extra_path_segments(self):
        assert _derive_image_name("org/team/myapp") == "myapp"


class TestQueuePositionAndStatus:
    def test_first_queued_build_is_position_one(self, app):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            assert get_queue_position(build_id) == 1

    def test_second_queued_build_is_position_two(self, app):
        entities = _make_entities(app)
        first_id = _make_queued_build(app, entities)
        second_id = _make_queued_build(app, entities)
        with app.app_context():
            assert get_queue_position(first_id) == 1
            assert get_queue_position(second_id) == 2

    def test_none_for_a_build_that_is_not_queued(self, app):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            _claim(build_id)
            assert get_queue_position(build_id) is None

    def test_engine_status_reflects_running_and_queued_builds(self, app):
        entities = _make_entities(app)
        running_id = _make_queued_build(app, entities)
        _make_queued_build(app, entities)
        _make_queued_build(app, entities)

        with app.app_context():
            _claim(running_id)

            status = get_engine_status()
            assert status["busy"] is True
            assert status["running"].id == running_id
            assert len(status["queued"]) == 2


class TestBatchProgress:
    def test_counts_finished_and_succeeded_across_the_batch(self, app):
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            builder = Builder.query.get(entities["builder_id"])
            batch = enqueue_build_batch(
                version=version,
                bump_type="patch",
                builder_branches=[(builder, "main"), (builder, "develop")],
                object_=None,
                additional_description=None,
                requested_by=None,
            )
            builds = ImageBuild.query.filter_by(batch_id=batch.id).all()
            builds[0].status = "success"
            builds[1].status = "queued"
            db.session.commit()

            progress = get_batch_progress(batch.id)
            assert progress == {"total": 2, "finished": 1, "succeeded": 1}


class TestUpdateBatchStatus:
    def test_all_success_marks_batch_success(self, app):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            build = _claim(build_id)
            build.status = "success"
            db.session.commit()
            _update_batch_status(build.batch_id)
            assert BuildBatch.query.get(build.batch_id).status == "success"

    def test_success_auto_creates_documentation_from_staged_fields(self, app):
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            builder = Builder.query.get(entities["builder_id"])
            batch = enqueue_build_batch(
                version=version,
                bump_type="patch",
                builder_branches=[(builder, "main")],
                object_="checkout-flow",
                additional_description="manual notes",
                requested_by=None,
            )
            build = ImageBuild.query.filter_by(batch_id=batch.id).first()
            _claim(build.id)
            build = ImageBuild.query.get(build.id)
            build.status = "success"
            db.session.commit()

            _update_batch_status(batch.id)

            doc = VersionDocumentation.query.filter_by(batch_id=batch.id).first()
            assert doc is not None
            assert doc.object == "checkout-flow"
            # No PromptTemplate is seeded in the test DB, so no AI text is
            # generated — the combined description falls back to just the
            # additional description typed in at trigger time.
            assert doc.description == "manual notes"
            assert doc.ai_description is None

    def test_ai_prompt_includes_the_additional_description(self, app, monkeypatch):
        """The AI shouldn't just have the typed additional description
        appended after its own draft — it should also see it while drafting,
        so a template referencing {{additional_description}} must actually
        receive the batch's staged additional_description.
        """
        entities = _make_entities(app)
        captured_prompts = []

        class FakeProvider:
            def generate_description(self, prompt):
                captured_prompts.append(prompt)
                return "AI summary incorporating the notes"

        monkeypatch.setattr("app.services.build.worker.get_ai_provider", lambda provider_type: FakeProvider())
        monkeypatch.setattr("app.services.build.worker.default_provider_type", lambda: "qwen")

        with app.app_context():
            db.session.add(
                PromptTemplate(
                    name="Default",
                    template_text="Notes: {{additional_description}}",
                    is_default=True,
                    is_active=True,
                )
            )
            version = Version.query.get(entities["version_id"])
            builder = Builder.query.get(entities["builder_id"])
            batch = enqueue_build_batch(
                version=version,
                bump_type="patch",
                builder_branches=[(builder, "main")],
                object_=None,
                additional_description="please mention the hotfix",
                requested_by=None,
            )
            build = ImageBuild.query.filter_by(batch_id=batch.id).first()
            _claim(build.id)
            build = ImageBuild.query.get(build.id)
            build.status = "success"
            db.session.commit()

            _update_batch_status(batch.id)

            doc = VersionDocumentation.query.filter_by(batch_id=batch.id).first()
            assert doc.ai_description == "AI summary incorporating the notes"

        assert captured_prompts == ["Notes: please mention the hotfix"]

    def test_success_is_idempotent_and_never_recreates_documentation(self, app):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            build = _claim(build_id)
            build.status = "success"
            db.session.commit()
            _update_batch_status(build.batch_id)
            first_doc_id = VersionDocumentation.query.filter_by(batch_id=build.batch_id).first().id

            _update_batch_status(build.batch_id)

            docs = VersionDocumentation.query.filter_by(batch_id=build.batch_id).all()
            assert len(docs) == 1
            assert docs[0].id == first_doc_id

    def test_mixed_success_and_failed_marks_partial_failure(self, app):
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            builder = Builder.query.get(entities["builder_id"])
            batch = enqueue_build_batch(
                version=version,
                bump_type="patch",
                builder_branches=[(builder, "main"), (builder, "develop")],
                object_=None,
                additional_description=None,
                requested_by=None,
            )
            builds = ImageBuild.query.filter_by(batch_id=batch.id).all()
            builds[0].status = "success"
            builds[1].status = "failed"
            db.session.commit()

            _update_batch_status(batch.id)
            assert BuildBatch.query.get(batch.id).status == "partial_failure"

    def test_partial_failure_does_not_create_documentation(self, app):
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            builder = Builder.query.get(entities["builder_id"])
            batch = enqueue_build_batch(
                version=version,
                bump_type="patch",
                builder_branches=[(builder, "main"), (builder, "develop")],
                object_=None,
                additional_description=None,
                requested_by=None,
            )
            builds = ImageBuild.query.filter_by(batch_id=batch.id).all()
            builds[0].status = "success"
            builds[1].status = "failed"
            db.session.commit()

            _update_batch_status(batch.id)
            assert VersionDocumentation.query.filter_by(batch_id=batch.id).first() is None

    def test_any_running_marks_batch_running(self, app):
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            builder = Builder.query.get(entities["builder_id"])
            batch = enqueue_build_batch(
                version=version,
                bump_type="patch",
                builder_branches=[(builder, "main"), (builder, "develop")],
                object_=None,
                additional_description=None,
                requested_by=None,
            )
            builds = ImageBuild.query.filter_by(batch_id=batch.id).all()
            builds[0].status = "running"
            builds[1].status = "queued"
            db.session.commit()

            _update_batch_status(batch.id)
            assert BuildBatch.query.get(batch.id).status == "running"

    def test_total_failure_rolls_back_the_version_bump(self, app):
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            version.major, version.minor, version.patch = 1, 2, 3
            db.session.commit()

        build_id = _make_queued_build(app, entities, bump_type="patch")
        with app.app_context():
            build = _claim(build_id)
            bumped = Version.query.get(entities["version_id"])
            assert (bumped.major, bumped.minor, bumped.patch) == (1, 2, 4)

            build.status = "failed"
            db.session.commit()
            _update_batch_status(build.batch_id)

            assert BuildBatch.query.get(build.batch_id).status == "failed"
            restored = Version.query.get(entities["version_id"])
            assert (restored.major, restored.minor, restored.patch) == (1, 2, 3)

    def test_partial_failure_keeps_the_version_bump(self, app):
        """A batch where at least one image succeeded must NOT roll back —
        that image is really pushed under the bumped number, so the version
        can't be given back without leaving a pushed image referencing a
        version that no longer exists.
        """
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            version.major, version.minor, version.patch = 1, 0, 0
            builder = Builder.query.get(entities["builder_id"])
            batch = enqueue_build_batch(
                version=version,
                bump_type="patch",
                builder_branches=[(builder, "main"), (builder, "develop")],
                object_=None,
                additional_description=None,
                requested_by=None,
            )
            db.session.commit()
            batch_id = batch.id

        with app.app_context():
            first_build = ImageBuild.query.get(_claim_next_job())
            first_build.status = "success"
            db.session.commit()
            _update_batch_status(batch_id)

            second_build = ImageBuild.query.get(_claim_next_job())
            second_build.status = "failed"
            db.session.commit()
            _update_batch_status(batch_id)

            assert BuildBatch.query.get(batch_id).status == "partial_failure"
            version = Version.query.get(entities["version_id"])
            assert (version.major, version.minor, version.patch) == (1, 0, 1)


class TestClaimNextJob:
    def test_claims_oldest_queued_job_and_marks_it_running(self, app):
        entities = _make_entities(app)
        first_id = _make_queued_build(app, entities)
        _make_queued_build(app, entities)

        with app.app_context():
            claimed_id = _claim_next_job()
            assert claimed_id == first_id

            build = ImageBuild.query.get(claimed_id)
            assert build.status == "running"
            assert build.started_at is not None

    def test_bumps_the_version_and_assigns_full_version_string_on_first_claim(self, app):
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            version.major, version.minor, version.patch = 0, 1, 0
            db.session.commit()

        build_id = _make_queued_build(app, entities, bump_type="patch")
        with app.app_context():
            batch_id = ImageBuild.query.get(build_id).batch_id
            assert BuildBatch.query.get(batch_id).full_version_string is None

            claimed_id = _claim_next_job()
            assert claimed_id == build_id

            batch = BuildBatch.query.get(batch_id)
            assert batch.full_version_string is not None
            assert batch.status == "running"
            assert (batch.bumped_from_major, batch.bumped_from_minor, batch.bumped_from_patch) == (0, 1, 0)

            version = Version.query.get(entities["version_id"])
            assert (version.major, version.minor, version.patch) == (0, 1, 1)

    def test_second_image_in_the_same_batch_does_not_bump_again(self, app):
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            builder = Builder.query.get(entities["builder_id"])
            batch = enqueue_build_batch(
                version=version,
                bump_type="patch",
                builder_branches=[(builder, "main"), (builder, "develop")],
                object_=None,
                additional_description=None,
                requested_by=None,
            )
            db.session.commit()
            batch_id = batch.id

        with app.app_context():
            first_id = _claim_next_job()
            build = ImageBuild.query.get(first_id)
            build.status = "success"
            db.session.commit()
            version_string_after_first = BuildBatch.query.get(batch_id).full_version_string

            second_id = _claim_next_job()
            assert second_id is not None
            assert BuildBatch.query.get(batch_id).full_version_string == version_string_after_first

    def test_returns_none_when_queue_is_empty(self, app):
        with app.app_context():
            assert _claim_next_job() is None

    def test_does_not_reclaim_an_already_running_job(self, app):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            first_claim = _claim_next_job()
            assert first_claim == build_id

            second_claim = _claim_next_job()
            assert second_claim is None

    def test_concurrent_claims_only_one_thread_wins_the_race(self, app):
        """The core concurrency guarantee: with N builds queued and multiple
        threads (standing in for multiple gunicorn worker processes) racing to
        claim a job at once, at most one may end up 'running' — SKIP LOCKED
        alone only stops two threads claiming the *same* row; the partial
        unique index (and this function backing off on IntegrityError) is
        what stops two *different* rows from both going 'running' at once.
        """
        entities = _make_entities(app)
        build_ids = [_make_queued_build(app, entities) for _ in range(6)]

        claimed = []
        claimed_lock = threading.Lock()

        def attempt_claim():
            with app.app_context():
                result = _claim_next_job()
                db.session.remove()
            if result is not None:
                with claimed_lock:
                    claimed.append(result)

        threads = [threading.Thread(target=attempt_claim) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(claimed) == 1, f"expected exactly one winner, got {claimed}"
        assert claimed[0] in build_ids

        with app.app_context():
            assert ImageBuild.query.filter_by(status="running").count() == 1
            assert ImageBuild.query.filter_by(status="queued").count() == 5

    def test_next_job_becomes_claimable_only_after_the_running_one_finishes(self, app):
        entities = _make_entities(app)
        first_id = _make_queued_build(app, entities)
        second_id = _make_queued_build(app, entities)

        with app.app_context():
            assert _claim_next_job() == first_id

            # first is still 'running' - second must not be claimable yet
            assert _claim_next_job() is None

            build = ImageBuild.query.get(first_id)
            build.status = "success"
            build.finished_at = datetime.utcnow()
            db.session.commit()

            assert _claim_next_job() == second_id


class TestReapStaleRunningJob:
    def test_recent_heartbeat_is_not_reaped(self, app):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            build = _claim(build_id)
            build.heartbeat_at = datetime.utcnow()
            db.session.commit()

            _reap_stale_running_job()
            assert ImageBuild.query.get(build_id).status == "running"

    def test_stale_heartbeat_is_reaped_and_batch_status_updates(self, app):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            build = _claim(build_id)
            batch_id = build.batch_id
            build.heartbeat_at = datetime.utcnow() - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 1)
            db.session.commit()

            _reap_stale_running_job()

            build = ImageBuild.query.get(build_id)
            assert build.status == "failed"
            assert build.finished_at is not None
            assert "[reaper]" in build.build_log
            assert BuildBatch.query.get(batch_id).status == "failed"

    def test_null_heartbeat_is_treated_as_stale(self, app):
        """A legacy pre-migration ghost row: heartbeat_at was never set."""
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            build = _claim(build_id)
            build.heartbeat_at = None
            db.session.commit()

            _reap_stale_running_job()
            assert ImageBuild.query.get(build_id).status == "failed"

    def test_no_running_job_is_a_noop(self, app):
        with app.app_context():
            _reap_stale_running_job()  # must not raise

    def test_reaping_frees_the_slot_for_the_next_queued_build(self, app):
        entities = _make_entities(app)
        first_id = _make_queued_build(app, entities)
        second_id = _make_queued_build(app, entities)
        with app.app_context():
            build = _claim(first_id)
            build.heartbeat_at = datetime.utcnow() - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 1)
            db.session.commit()

            _reap_stale_running_job()
            assert _claim_next_job() == second_id

    def test_concurrent_reap_attempts_only_apply_once(self, app):
        """Mirrors test_concurrent_claims_only_one_thread_wins_the_race — two
        processes' reap checks racing on the same stale row must not both
        apply the transition (which would double-append the reaper log line
        and double-run the downstream batch-status update).
        """
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            build = _claim(build_id)
            build.heartbeat_at = datetime.utcnow() - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 1)
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
            build = ImageBuild.query.get(build_id)
            assert build.status == "failed"
            assert build.build_log.count("[reaper]") == 1


class TestHeartbeatTick:
    def test_ticks_heartbeat_for_the_currently_owned_build(self, app):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            build = _claim(build_id)
            old_heartbeat = build.heartbeat_at
            db.session.commit()

        build_worker._current_build_id = build_id
        try:
            _heartbeat_tick(app)
        finally:
            build_worker._current_build_id = None

        with app.app_context():
            assert ImageBuild.query.get(build_id).heartbeat_at > old_heartbeat

    def test_noop_when_no_build_is_currently_owned(self, app):
        build_worker._current_build_id = None
        _heartbeat_tick(app)  # must not raise


class _FakeGitProvider:
    def __init__(self):
        self.synced = None
        self.synced_repo_name = None

    def sync_repo(self, local_path, branch, repo_name=None):
        self.synced = (local_path, branch)
        self.synced_repo_name = repo_name


class _FakeBuildResult:
    def __init__(self, success, log="log output\n", error=None, image_size=42, pushed=False):
        self.success = success
        self.log = log
        self.error = error
        self.image_size = image_size
        self.pushed = pushed


class _FakeBuildEngine:
    def __init__(self, result):
        self._result = result
        self.client = object()
        self.build_calls = []

    def build_image(
        self,
        context_dir,
        dockerfile_path,
        tags,
        build_args=None,
        on_log_line=None,
        registry_provider=None,
        push_repository=None,
    ):
        self.build_calls.append((context_dir, dockerfile_path, tags, build_args))
        if on_log_line:
            on_log_line("building...\n")
        return self._result


class _FakeRegistryProvider:
    def __init__(self, username="myuser"):
        self.username = username
        self.pushed = None

    def push_image(self, docker_client, repository, tag):
        self.pushed = (repository, tag)

    def full_repository_name(self, repository):
        return repository if "/" in repository else f"{self.username}/{repository}"


class TestRunBuild:
    def test_successful_pipeline_marks_build_success_and_records_details(self, app, monkeypatch):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            _claim(build_id)

        fake_git = _FakeGitProvider()
        fake_engine = _FakeBuildEngine(_FakeBuildResult(success=True))
        fake_registry = _FakeRegistryProvider()

        monkeypatch.setattr(
            "app.services.build.worker.provider_for_git_source", lambda source: fake_git
        )
        monkeypatch.setattr("app.services.build.worker.get_build_engine", lambda: fake_engine)
        monkeypatch.setattr(
            "app.services.build.worker.get_registry_provider",
            lambda provider_type, username, password: fake_registry,
        )

        _run_build(app, build_id)

        with app.app_context():
            build = ImageBuild.query.get(build_id)
            assert build.status == "success"
            expected_tag = f"myuser/myapp:{build.batch.full_version_string}"
            assert build.image_tag == expected_tag
            assert build.image_size == 42
            assert build.finished_at is not None
            assert "building..." in build.build_log
            full_version_string = build.batch.full_version_string

        assert fake_git.synced == ("/tmp/myapp", "main")
        assert fake_git.synced_repo_name == "owner/myapp"
        assert fake_registry.pushed == ("myapp", full_version_string)

    def test_builds_under_the_full_repository_tag_not_just_the_bare_image_name(self, app, monkeypatch):
        """Regression test: the engine must build under the exact tag that
        gets pushed (`full_repository:version`), not the bare image name —
        otherwise the later push has no matching local image to find, and
        (before DockerHubProvider.push_image() checked for error events)
        that failure was silently swallowed.
        """
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            build = _claim(build_id)
            full_version_string = build.batch.full_version_string

        fake_engine = _FakeBuildEngine(_FakeBuildResult(success=True))
        monkeypatch.setattr(
            "app.services.build.worker.provider_for_git_source", lambda source: _FakeGitProvider()
        )
        monkeypatch.setattr("app.services.build.worker.get_build_engine", lambda: fake_engine)
        monkeypatch.setattr(
            "app.services.build.worker.get_registry_provider",
            lambda provider_type, username, password: _FakeRegistryProvider(),
        )

        _run_build(app, build_id)

        assert len(fake_engine.build_calls) == 1
        _, _, tags, _ = fake_engine.build_calls[0]
        assert tags == [f"myuser/myapp:{full_version_string}"]

    def test_custom_image_name_overrides_the_derived_repo_name(self, app, monkeypatch):
        entities = _make_entities(app)
        with app.app_context():
            builder = Builder.query.get(entities["builder_id"])
            builder.image_name = "custom-image"
            db.session.commit()

        build_id = _make_queued_build(app, entities)
        with app.app_context():
            build = _claim(build_id)
            full_version_string = build.batch.full_version_string

        fake_engine = _FakeBuildEngine(_FakeBuildResult(success=True))
        fake_registry = _FakeRegistryProvider()
        monkeypatch.setattr(
            "app.services.build.worker.provider_for_git_source", lambda source: _FakeGitProvider()
        )
        monkeypatch.setattr("app.services.build.worker.get_build_engine", lambda: fake_engine)
        monkeypatch.setattr(
            "app.services.build.worker.get_registry_provider",
            lambda provider_type, username, password: fake_registry,
        )

        _run_build(app, build_id)

        _, _, tags, _ = fake_engine.build_calls[0]
        assert tags == [f"myuser/custom-image:{full_version_string}"]
        assert fake_registry.pushed == ("custom-image", full_version_string)

        with app.app_context():
            build = ImageBuild.query.get(build_id)
            assert build.image_tag == f"myuser/custom-image:{full_version_string}"

    def test_updates_the_batch_status_after_finishing(self, app, monkeypatch):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            build = _claim(build_id)
            batch_id = build.batch_id

        monkeypatch.setattr(
            "app.services.build.worker.provider_for_git_source", lambda source: _FakeGitProvider()
        )
        monkeypatch.setattr(
            "app.services.build.worker.get_build_engine",
            lambda: _FakeBuildEngine(_FakeBuildResult(success=True)),
        )
        monkeypatch.setattr(
            "app.services.build.worker.get_registry_provider",
            lambda provider_type, username, password: _FakeRegistryProvider(),
        )

        _run_build(app, build_id)

        with app.app_context():
            assert BuildBatch.query.get(batch_id).status == "success"

    def test_self_pushing_engine_skips_the_separate_push_step(self, app, monkeypatch):
        """A BuildResult with pushed=True (Kaniko's shape) must not trigger a
        second, separate RegistryProvider.push_image() call — the worker
        should just record the tag/registry details and stop.
        """
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            _claim(build_id)

        fake_registry = _FakeRegistryProvider()
        monkeypatch.setattr(
            "app.services.build.worker.provider_for_git_source", lambda source: _FakeGitProvider()
        )
        monkeypatch.setattr(
            "app.services.build.worker.get_build_engine",
            lambda: _FakeBuildEngine(_FakeBuildResult(success=True, pushed=True)),
        )
        monkeypatch.setattr(
            "app.services.build.worker.get_registry_provider",
            lambda provider_type, username, password: fake_registry,
        )

        _run_build(app, build_id)

        with app.app_context():
            build = ImageBuild.query.get(build_id)
            assert build.status == "success"
            expected_tag = f"myuser/myapp:{build.batch.full_version_string}"
            assert build.image_tag == expected_tag

        assert fake_registry.pushed is None  # never called — the engine already pushed


class TestRunBuildFailureModes:
    def test_failed_build_marks_status_failed_and_does_not_push(self, app, monkeypatch):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            _claim(build_id)

        fake_registry = _FakeRegistryProvider()
        monkeypatch.setattr(
            "app.services.build.worker.provider_for_git_source", lambda source: _FakeGitProvider()
        )
        monkeypatch.setattr(
            "app.services.build.worker.get_build_engine",
            lambda: _FakeBuildEngine(_FakeBuildResult(success=False, error="Dockerfile syntax error")),
        )
        monkeypatch.setattr(
            "app.services.build.worker.get_registry_provider",
            lambda provider_type, username, password: fake_registry,
        )

        _run_build(app, build_id)

        with app.app_context():
            build = ImageBuild.query.get(build_id)
            assert build.status == "failed"
            assert build.finished_at is not None
            assert build.image_tag is None

            error_log = ErrorLog.query.filter_by(source="worker.run_build").first()
            assert error_log is not None
            assert "Dockerfile syntax error" in error_log.message
            assert error_log.traceback == "log output\n"  # build log, standing in for a traceback

        assert fake_registry.pushed is None

    def test_failed_build_rolls_back_the_version(self, app, monkeypatch):
        entities = _make_entities(app)
        with app.app_context():
            version = Version.query.get(entities["version_id"])
            version.major, version.minor, version.patch = 2, 0, 0
            db.session.commit()

        build_id = _make_queued_build(app, entities)
        with app.app_context():
            _claim(build_id)
            bumped = Version.query.get(entities["version_id"])
            assert (bumped.major, bumped.minor, bumped.patch) == (2, 0, 1)

        monkeypatch.setattr(
            "app.services.build.worker.provider_for_git_source", lambda source: _FakeGitProvider()
        )
        monkeypatch.setattr(
            "app.services.build.worker.get_build_engine",
            lambda: _FakeBuildEngine(_FakeBuildResult(success=False, error="boom")),
        )
        monkeypatch.setattr(
            "app.services.build.worker.get_registry_provider",
            lambda provider_type, username, password: _FakeRegistryProvider(),
        )

        _run_build(app, build_id)

        with app.app_context():
            restored = Version.query.get(entities["version_id"])
            assert (restored.major, restored.minor, restored.patch) == (2, 0, 0)
            assert VersionDocumentation.query.filter_by(
                batch_id=ImageBuild.query.get(build_id).batch_id
            ).first() is None

    def test_unexpected_exception_marks_failed_instead_of_raising(self, app, monkeypatch):
        entities = _make_entities(app)
        build_id = _make_queued_build(app, entities)
        with app.app_context():
            _claim(build_id)

        def _raise_sync(local_path, branch, repo_name=None):
            raise RuntimeError("network is unreachable")

        broken_git = _FakeGitProvider()
        broken_git.sync_repo = _raise_sync
        monkeypatch.setattr(
            "app.services.build.worker.provider_for_git_source", lambda source: broken_git
        )

        _run_build(app, build_id)  # must not raise

        with app.app_context():
            build = ImageBuild.query.get(build_id)
            assert build.status == "failed"
            assert "network is unreachable" in build.build_log

            error_log = ErrorLog.query.filter_by(source="worker.run_build").first()
            assert error_log is not None
            assert "network is unreachable" in error_log.message
            assert error_log.traceback is not None
            assert "RuntimeError" in error_log.traceback
