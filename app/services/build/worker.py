import os
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import BuildBatch, ImageBuild, VersionDocumentation
from app.services.ai.context import gather_batch_ai_context
from app.services.ai.factory import default_provider_type, get_ai_provider
from app.services.ai.prompt import render_default_prompt
from app.services.build.factory import get_build_engine
from app.services.build.versioning import bump_version
from app.services.git.helpers import provider_for_git_source
from app.services.registry.factory import get_registry_provider
from app.utils.crypto import decrypt
from app.utils.error_logger import log_error

POLL_INTERVAL_SECONDS = 2
LOG_FLUSH_EVERY_N_LINES = 20

# Heartbeat cadence for detecting a worker process that died mid-build (see
# _heartbeat_loop/_reap_stale_running_job). Deliberately decoupled from
# build log activity (flush_log/on_log_line) so a legitimately slow, quiet
# build (large layer pull, slow git clone) never false-positives — only the
# owning thread/process itself dying stops the heartbeat. 4x the interval
# gives margin against scheduler jitter under load.
HEARTBEAT_INTERVAL_SECONDS = 15
HEARTBEAT_STALE_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 4

_worker_started = False
_worker_lock = threading.Lock()

# Which ImageBuild id (if any) *this process* currently owns — at most one
# of the fleet's processes will ever have this set, since only one
# ImageBuild can be 'running' system-wide (ix_image_builds_single_running).
_current_build_id = None
_current_build_lock = threading.Lock()


def enqueue_build_batch(
    version,
    bump_type,
    builder_branches,
    object_,
    additional_description,
    requested_by,
    change_type_id=None,
):
    """Creates one BuildBatch + one ImageBuild per (builder, branch) pair.

    `builder_branches` is a list of (Builder, branch_used) tuples; every
    Builder in it must already share `version` (the caller validates this,
    since the constraint is UI/business logic, not something this function
    can enforce from the tuples alone).

    Deliberately does NOT bump `version` or create a VersionDocumentation row
    here — a batch that's still queued (or that ends up failing outright)
    must never leave the Version's numbers bumped, and only a fully
    successful batch ever gets documented. Both happen later, in the worker
    (see `_claim_next_job` and `_update_batch_status`), once there's an
    actual outcome to act on. `object_`/`change_type_id`/
    `additional_description` are just staged on the batch for now — they get
    copied into the real VersionDocumentation if/when it's created.
    """
    batch = BuildBatch(
        version_id=version.id,
        full_version_string=None,
        bump_type=bump_type,
        requested_by=requested_by,
        status="queued",
        object=object_ or None,
        change_type_id=change_type_id,
        additional_description=additional_description or None,
    )
    db.session.add(batch)
    db.session.flush()  # assign batch.id so the rows below can reference it

    for builder, branch_used in builder_branches:
        db.session.add(
            ImageBuild(
                batch_id=batch.id,
                builder_id=builder.id,
                branch_used=branch_used,
                status="queued",
            )
        )

    db.session.commit()
    return batch


def get_queue_position(image_build_id):
    """1-indexed position in the queue, or None if this build isn't (or no longer) queued."""
    image_build = ImageBuild.query.get(image_build_id)
    if image_build is None or image_build.status != "queued":
        return None
    ahead = ImageBuild.query.filter(
        ImageBuild.status == "queued", ImageBuild.created_at < image_build.created_at
    ).count()
    return ahead + 1


def get_engine_status():
    """Snapshot of what the build engine is doing right now."""
    running = ImageBuild.query.filter_by(status="running").first()
    queued = ImageBuild.query.filter_by(status="queued").order_by(ImageBuild.created_at.asc()).all()
    return {"busy": running is not None, "running": running, "queued": queued}


def get_batch_progress(batch_id):
    """'X of Y built' — a batch-level aggregate over its ImageBuilds' statuses."""
    builds = ImageBuild.query.filter_by(batch_id=batch_id).all()
    total = len(builds)
    finished = sum(1 for build in builds if build.status in ("success", "failed"))
    succeeded = sum(1 for build in builds if build.status == "success")
    return {"total": total, "finished": finished, "succeeded": succeeded}


def _claim_next_job():
    """Atomically claim the oldest queued build, enforcing "only one build runs
    at a time" globally across every process.

    SELECT ... FOR UPDATE SKIP LOCKED stops two callers from claiming the same
    *row* — but two different queued rows could still both look claimable at
    once, and both get marked 'running' in parallel. The partial unique index
    on status='running' is what actually catches that: whichever UPDATE
    commits second raises IntegrityError, treated as "lost the race."

    Also where a batch's Version bump actually happens now (deferred from
    enqueue time) — the first ImageBuild of a batch to be claimed here bumps
    that batch's Version and assigns its full_version_string, in the same
    transaction as marking the image 'running'. That's safe to do
    unconditionally (no need to check whether some other batch beat it to
    the punch): only one build ever runs system-wide at a time, so no other
    batch targeting the same Version could be "in flight" concurrently — and
    if this claim itself loses the race below, the whole transaction
    (including the bump) rolls back with it, so a lost race never leaves a
    stray bump behind either.

    Returns the claimed ImageBuild's id, or None if there was nothing to claim
    or this attempt lost the race to another already-running build.
    """
    build = (
        ImageBuild.query.filter_by(status="queued")
        .order_by(ImageBuild.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if build is None:
        db.session.rollback()
        return None

    build.status = "running"
    build.started_at = datetime.utcnow()
    build.heartbeat_at = build.started_at

    # no_autoflush: build.batch/batch.version are lazy-loaded relationships,
    # and loading them would otherwise autoflush the pending build.status
    # change above right now — before the try/except below is in place to
    # catch a lost-race IntegrityError, letting it escape uncaught.
    with db.session.no_autoflush:
        batch = build.batch
        if batch.full_version_string is None:
            batch.bumped_from_major = batch.version.major
            batch.bumped_from_minor = batch.version.minor
            batch.bumped_from_patch = batch.version.patch
            batch.full_version_string = bump_version(batch.version, batch.bump_type)
            batch.status = "running"

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None
    return build.id


def _heartbeat_tick(app):
    """Bump heartbeat_at on the ImageBuild this process currently owns, if
    any. No-ops for the other (at most 2) processes in the fleet that aren't
    running anything right now — see _current_build_id's docstring.
    """
    with _current_build_lock:
        build_id = _current_build_id
    if build_id is None:
        return

    with app.app_context():
        ImageBuild.query.filter_by(id=build_id, status="running").update(
            {"heartbeat_at": datetime.utcnow()}, synchronize_session=False
        )
        db.session.commit()
        db.session.remove()


def _heartbeat_loop(app):
    while True:
        try:
            _heartbeat_tick(app)
        except Exception as exc:  # a bad tick must not kill the heartbeat thread
            with app.app_context():
                log_error(
                    source="worker.heartbeat",
                    exc=exc,
                    description=f"Build worker heartbeat tick failed: {exc}",
                )
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


def _reap_stale_running_job():
    """If the single 'running' ImageBuild's heartbeat has gone stale (or was
    never set — a pre-migration ghost row), the process that claimed it is
    assumed to have died mid-build. Fails it out so the single-flight slot
    (ix_image_builds_single_running) frees up for the next queued build,
    instead of staying wedged forever.

    Uses a WHERE-guarded UPDATE + rowcount check rather than relying on a
    unique-index collision like _claim_next_job does — there's no constraint
    protecting this particular transition — so a no-op rowcount is treated
    the same way a lost race is treated there: nothing to do. This also
    makes it safe against the job actually finishing (success/failed) in its
    owning process at almost the same moment: that commit already moved
    status off 'running', so this UPDATE's WHERE clause matches zero rows.
    """
    stale = ImageBuild.query.filter_by(status="running").first()
    if stale is None:
        db.session.rollback()
        return

    threshold = datetime.utcnow() - timedelta(seconds=HEARTBEAT_STALE_SECONDS)
    if stale.heartbeat_at is not None and stale.heartbeat_at >= threshold:
        db.session.rollback()
        return

    rowcount = (
        ImageBuild.query.filter(
            ImageBuild.id == stale.id,
            ImageBuild.status == "running",
        )
        .filter(db.or_(ImageBuild.heartbeat_at.is_(None), ImageBuild.heartbeat_at < threshold))
        .update({"status": "failed", "finished_at": datetime.utcnow()}, synchronize_session=False)
    )
    if rowcount == 0:
        db.session.rollback()
        return

    db.session.commit()

    build = ImageBuild.query.get(stale.id)
    build.build_log = (build.build_log or "") + (
        f"\n\n[reaper] No heartbeat for over {HEARTBEAT_STALE_SECONDS}s — assuming the "
        "worker process that claimed this build crashed. Marking failed.\n"
    )
    db.session.commit()
    log_error(
        source="worker.reap_stale_job",
        description=f"Reaped stale running build {build.id} (batch {build.batch_id}): no heartbeat.",
    )
    _update_batch_status(build.batch_id)


def _generate_ai_description(batch):
    """Best-effort AI draft for a just-succeeded batch — never raises; a
    failed/unconfigured provider just means no AI text, not a broken
    documentation row. Returns (ai_description, ai_provider_used), either
    possibly None.
    """
    try:
        provider_type = default_provider_type()
        prompt = render_default_prompt(
            gather_batch_ai_context(batch), batch.object, batch.additional_description
        )
        if not prompt:
            return None, None
        description = get_ai_provider(provider_type).generate_description(prompt)
        return description, provider_type
    except Exception as exc:
        log_error(
            source="worker.generate_documentation",
            exc=exc,
            description=f"AI description generation failed for batch {batch.id}: {exc}",
        )
        return None, None


def _create_documentation_for_successful_batch(batch):
    """The only place a VersionDocumentation row is ever created — per "only
    successful batches get documented," this runs exactly once, the moment
    `_update_batch_status` first sees every image in the batch as 'success'.

    Combines an automatically-generated AI description with whatever
    "additional description" was typed in at trigger time (see
    builders.routes.build()) into the doc's editable `description` — no
    manual "generate draft" step in the loop; the AI draft is also kept on
    its own (`ai_description`) so the documentation page can still show/redo
    it like any other batch's.

    The additional description is also handed to the AI itself as part of
    its prompt (see render_default_prompt), so it can be woven into the
    generated text — it's still appended verbatim afterward too, so nothing
    typed in is ever lost even if the AI ignores it or is unconfigured.
    """
    ai_description, ai_provider_used = _generate_ai_description(batch)

    combined = "\n\n".join(
        part for part in (ai_description, batch.additional_description) if part
    )

    db.session.add(
        VersionDocumentation(
            batch_id=batch.id,
            built_by=batch.requested_by,
            change_type_id=batch.change_type_id,
            object=batch.object,
            ai_description=ai_description,
            ai_provider_used=ai_provider_used,
            description=combined or None,
        )
    )


def _update_batch_status(batch_id):
    """Aggregates a BuildBatch's status from its ImageBuilds' current statuses,
    called after each image in the batch finishes.
    """
    batch = BuildBatch.query.get(batch_id)
    if batch is None:
        return

    statuses = {build.status for build in ImageBuild.query.filter_by(batch_id=batch_id).all()}
    if statuses & {"queued", "running"}:
        batch.status = "running" if "running" in statuses else "queued"
    elif statuses == {"success"}:
        batch.status = "success"
        if batch.documentation is None:
            _create_documentation_for_successful_batch(batch)
    elif statuses == {"failed"}:
        batch.status = "failed"
        # Nothing in this batch ever got built/pushed, so no real image
        # references this version number — safe to give it back. Only
        # correct because builds are strictly serialized system-wide (see
        # _claim_next_job's docstring): nothing else could have bumped past
        # this batch's number in the meantime.
        if batch.bumped_from_major is not None:
            batch.version.major = batch.bumped_from_major
            batch.version.minor = batch.bumped_from_minor
            batch.version.patch = batch.bumped_from_patch
    else:
        batch.status = "partial_failure"
    db.session.commit()


def _derive_image_name(full_name):
    """myapp from org/myapp — there's no separate "image name" field anywhere
    in the schema, so the built image is named after the repository itself.
    """
    return full_name.rsplit("/", 1)[-1]


def _run_build(app, build_id):
    """Runs the full sync -> build -> push pipeline for one claimed ImageBuild.

    Always wrapped in try/except so a bad build (bad branch, failing
    Dockerfile, registry auth failure, ...) marks that one ImageBuild as
    failed instead of killing the worker thread — the poll loop must keep
    running afterward, and the rest of the batch's other images still run.
    """
    with app.app_context():
        build = ImageBuild.query.get(build_id)
        if build is None:
            return

        log_buffer = []

        def flush_log():
            build.build_log = "".join(log_buffer)
            db.session.commit()

        def on_log_line(line):
            log_buffer.append(line)
            if len(log_buffer) % LOG_FLUSH_EVERY_N_LINES == 0:
                flush_log()

        try:
            builder = build.builder
            repository = builder.repository
            batch = build.batch

            log_buffer.append(f"Syncing {repository.full_name} @ {build.branch_used} ...\n")
            flush_log()

            git_provider = provider_for_git_source(repository.git_source)
            git_provider.sync_repo(repository.local_path, build.branch_used, repo_name=repository.full_name)
            repository.status = "ready"
            repository.last_synced_at = datetime.utcnow()
            db.session.commit()

            log_buffer.append("Sync complete. Starting build...\n")
            flush_log()

            image_name = builder.image_name or _derive_image_name(repository.full_name)

            registry_target = builder.registry_target
            registry_provider = get_registry_provider(
                registry_target.provider_type,
                username=registry_target.username,
                password=decrypt(registry_target.encrypted_token),
                registry_url=registry_target.registry_url,
            )
            # Store the fully-qualified pushed reference (e.g. with the Docker
            # Hub account name prepended), not just the local build tag, so
            # /images can link straight to the real registry location. Needed
            # up front (not just after a successful push) since a
            # self-pushing engine (Kaniko) requires it as its destination.
            full_repository = registry_provider.full_repository_name(image_name)

            # Build under the exact tag that will be pushed — DockerBuildEngine
            # only knows about the tags it's given, so if this were built as
            # bare "image_name:version" instead, there would be no local image
            # under "full_repository:version" for the later push to find.
            full_tag = f"{full_repository}:{batch.full_version_string}"

            engine = get_build_engine()
            result = engine.build_image(
                context_dir=repository.local_path,
                dockerfile_path=builder.dockerfile_path or "Dockerfile",
                tags=[full_tag],
                build_args=builder.default_build_args or {},
                on_log_line=on_log_line,
                registry_provider=registry_provider,
                push_repository=full_repository,
            )

            if not result.success:
                build.status = "failed"
                build.build_log = result.log
                log_buffer[:] = [result.log, f"\nBuild failed: {result.error}\n"]
                log_error(
                    source="worker.run_build",
                    description=f"Build {build.id} (batch {build.batch_id}) failed: {result.error}",
                    detail=result.log,
                )
                return

            build.image_size = result.image_size

            if result.pushed:
                log_buffer.append("\nBuild and push complete.\n")
            else:
                log_buffer.append("\nBuild succeeded. Pushing image...\n")
                flush_log()
                registry_provider.push_image(engine.client, image_name, batch.full_version_string)
                log_buffer.append("Push complete.\n")

            build.registry_name = registry_target.provider_type
            build.image_tag = f"{full_repository}:{batch.full_version_string}"
            build.status = "success"

        except Exception as exc:  # a bad build must not kill the worker thread
            log_buffer.append(f"\nERROR: {exc}\n")
            build.status = "failed"
            log_error(
                source="worker.run_build",
                exc=exc,
                description=f"Build {build.id} (batch {build.batch_id}) failed: {exc}",
            )
        finally:
            flush_log()
            build.finished_at = datetime.utcnow()
            db.session.commit()
            _update_batch_status(build.batch_id)
            db.session.remove()


def _poll_loop(app):
    global _current_build_id
    while True:
        with app.app_context():
            _reap_stale_running_job()
            build_id = _claim_next_job()
            db.session.remove()

        if build_id is not None:
            with _current_build_lock:
                _current_build_id = build_id
            try:
                _run_build(app, build_id)
            finally:
                with _current_build_lock:
                    _current_build_id = None
        else:
            time.sleep(POLL_INTERVAL_SECONDS)


def start_worker(app):
    """Start this process's background build-worker thread, once.

    Skipped entirely under TESTING — a live thread claiming and running real
    builds against the test DB would make the test suite non-deterministic.
    Also guarded against double-starting in the Flask debug reloader's parent
    watcher process (WERKZEUG_RUN_MAIN is only set in the actual serving child).
    """
    global _worker_started

    if app.config.get("TESTING"):
        return

    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    thread = threading.Thread(target=_poll_loop, args=(app,), daemon=True, name="image-builder-worker")
    thread.start()

    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, args=(app,), daemon=True, name="image-builder-heartbeat"
    )
    heartbeat_thread.start()
