import uuid

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.builders import builders_bp
from app.blueprints.builders.forms import BuilderForm
from app.extensions import db
from app.models import (
    Builder,
    ChangeType,
    Dockerfile,
    ImageBuild,
    Object,
    RegistryTarget,
    Repository,
    Role,
    Version,
    WorkflowStep,
)
from app.services.build.prefill import compute_build_prefill
from app.services.build.versioning import BUMP_TYPES
from app.services.build.worker import enqueue_build_batch, get_batch_progress, get_engine_status
from app.services.git.helpers import provider_for_git_source
from app.utils.decorators import permission_required
from app.utils.error_logger import log_error
from app.utils.logger import log_activity

CREATE_PREFIX = "create-builder-"
LOG_TAIL_LINES = 100


def _edit_prefix(builder_id):
    return f"builder-{builder_id}-"


def _parse_uuid(value):
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _active_choices_with_fallback(query, include_id, extra_lookup):
    """Active-only choices, defensively including `include_id` (a Builder's
    own already-assigned Version/Dockerfile/RegistryTarget) even if it's
    since been disabled — same "keep the current value selectable in its
    own edit form" reasoning as _branch_choices above: without this, editing
    an otherwise-unrelated field on a Builder whose Version/Dockerfile/
    Registry got disabled would silently drop that assignment (or fail
    WTForms' choice validation outright), rather than just leaving it as-is.
    """
    choices = [(str(row.id), row.name) for row in query.all()]
    if include_id and str(include_id) not in {choice_id for choice_id, _ in choices}:
        extra = extra_lookup(include_id)
        if extra is not None:
            choices.append((str(extra.id), extra.name))
    return choices


def _version_choices(include_id=None):
    return _active_choices_with_fallback(
        Version.query.filter_by(is_active=True).order_by(Version.name), include_id, Version.query.get
    )


def _repository_choices():
    return [
        (str(r.id), r.full_name)
        for r in Repository.query.filter_by(status="ready").order_by(Repository.full_name).all()
    ]


def _registry_choices(include_id=None):
    return _active_choices_with_fallback(
        RegistryTarget.query.filter_by(is_active=True).order_by(RegistryTarget.name),
        include_id,
        RegistryTarget.query.get,
    )


def _dockerfile_choices(include_id=None):
    return [("", "— Select —")] + _active_choices_with_fallback(
        Dockerfile.query.filter_by(is_active=True).order_by(Dockerfile.name), include_id, Dockerfile.query.get
    )


def _role_choices():
    return [(str(r.id), r.name) for r in Role.query.order_by(Role.name).all()]


def _apply_allowed_roles(builder, form):
    selected_ids = {uuid.UUID(rid) for rid in form.allowed_role_ids.data}
    builder.allowed_roles = Role.query.filter(Role.id.in_(selected_ids)).all() if selected_ids else []


def _apply_dockerfile_source(builder, form):
    """Validates + applies dockerfile_source/dockerfile_path/
    managed_dockerfile_id from a submitted BuilderForm — only one of
    dockerfile_path/managed_dockerfile_id actually applies depending on
    dockerfile_source, so it's checked here rather than via unconditional
    WTForms validators on both fields (see BuilderForm's own docstring).
    Returns an error message string on failure (nothing applied), or None
    on success.
    """
    if form.dockerfile_source.data == "managed":
        dockerfile_id = _parse_uuid(form.managed_dockerfile_id.data)
        managed_dockerfile = Dockerfile.query.get(dockerfile_id) if dockerfile_id else None
        # A disabled Dockerfile can't be newly selected, but re-submitting a
        # Builder's own already-assigned one (unrelated field edit) must
        # still succeed even if it's since been disabled.
        already_assigned = managed_dockerfile is not None and managed_dockerfile.id == builder.managed_dockerfile_id
        if managed_dockerfile is None or not (managed_dockerfile.is_active or already_assigned):
            return "Select a Managed Dockerfile."
        builder.dockerfile_source = "managed"
        builder.managed_dockerfile_id = managed_dockerfile.id
        return None

    path = (form.dockerfile_path.data or "").strip()
    if not path:
        return "Dockerfile Path is required."
    builder.dockerfile_source = "repo"
    builder.dockerfile_path = path
    builder.managed_dockerfile_id = None
    return None


def _repo_info(repository, cache):
    """Best-effort branches/dockerfiles for a repo, cached per request so
    several Builders sharing one repo don't each trigger their own git calls.
    """
    if repository.id not in cache:
        try:
            provider = provider_for_git_source(repository.git_source)
            cache[repository.id] = {
                "branches": provider.list_branches(repository.local_path),
                "dockerfiles": provider.list_files(repository.local_path),
            }
        except Exception as exc:
            log_error(
                source="builders.repo_info",
                exc=exc,
                description=f"Failed to read branches/Dockerfiles for repository '{repository.full_name}': {exc}",
            )
            cache[repository.id] = {"branches": [], "dockerfiles": []}
    return cache[repository.id]


def _branch_choices(info, saved_branch):
    """Branch choices for a Builder's edit form, defensively including
    `saved_branch` even if the live picker (`_repo_info`) came back empty —
    e.g. the repo's local clone is temporarily missing. WTForms only ever
    renders options from `.choices`, so without this a Builder whose config
    is completely fine would have its own already-saved branch silently
    vanish from its dropdown, reading as "something is broken" when nothing
    is.
    """
    branches = list(info["branches"])
    if saved_branch and saved_branch not in branches:
        branches.append(saved_branch)
    return [(b, b) for b in branches]


def _parse_build_args():
    keys = request.form.getlist("build_arg_key")
    values = request.form.getlist("build_arg_value")
    return {key.strip(): value for key, value in zip(keys, values) if key.strip()}


def _builder_groups(builders):
    """Split builders into named groups (by Builder.group_name, assigned on
    create/edit — purely organizational) plus an "ungrouped" leftover list.

    A group's members aren't required to share a Version — /builders/build
    still enforces that when a group is actually built, so a mixed-Version
    group just surfaces that validation error rather than being prevented
    from existing in the first place.
    """
    groups = {}
    ungrouped = []
    for builder in builders:
        if builder.group_name:
            groups.setdefault(builder.group_name, []).append(builder)
        else:
            ungrouped.append(builder)
    sorted_groups = [
        {"name": name, "builders": groups[name]} for name in sorted(groups, key=str.lower)
    ]
    return sorted_groups, ungrouped


def _group_name_suggestions():
    return [
        row[0]
        for row in Builder.query.with_entities(Builder.group_name)
        .filter(Builder.group_name.isnot(None))
        .distinct()
        .order_by(Builder.group_name)
    ]


def _last_build_by_builder():
    """builder_id -> its most recent ImageBuild, via Builder -> ImageBuild ->
    BuildBatch (Builder has no direct FK to BuildBatch, only through its
    ImageBuilds) — unlike /versions, which can join BuildBatch directly.
    """
    latest = {}
    for image_build in ImageBuild.query.order_by(ImageBuild.created_at.desc()).all():
        latest.setdefault(image_build.builder_id, image_build)
    return latest


def _change_type_choices():
    # No blank/"None" entry here — Change Type is mandatory at build-trigger
    # time (unlike the documentation page's own change-type field, which
    # stays optional/editable afterward — see documentation.routes's own
    # separate _change_type_choices()).
    return [
        (str(ct.id), ct.name)
        for ct in ChangeType.query.filter_by(is_active=True).order_by(ChangeType.name).all()
    ]


def _object_suggestions():
    return [
        {"id": str(obj.id), "name": obj.name}
        for obj in Object.query.filter_by(is_active=True).order_by(Object.name).all()
    ]


def _log_tail(log_text, max_lines=LOG_TAIL_LINES):
    if not log_text:
        return ""
    return "\n".join(log_text.splitlines()[-max_lines:])


def _render_index(create_form=None, open_modal=None, invalid_edit=None, selected_version_id=None):
    if create_form is None:
        create_form = BuilderForm(prefix=CREATE_PREFIX)
    create_form.version_id.choices = _version_choices()
    create_form.repository_id.choices = _repository_choices()
    create_form.registry_target_id.choices = _registry_choices()
    create_form.managed_dockerfile_id.choices = _dockerfile_choices()
    create_form.allowed_role_ids.choices = _role_choices()

    query = Builder.query.filter_by(is_active=True)
    if selected_version_id:
        query = query.filter_by(version_id=selected_version_id)
    builders = [b for b in query.order_by(Builder.name).all() if b.is_accessible_to(current_user)]

    repo_cache = {}
    repo_info_by_builder = {}
    invalid_id, invalid_form = invalid_edit or (None, None)
    edit_forms = {}
    for builder in builders:
        info = _repo_info(builder.repository, repo_cache)
        repo_info_by_builder[builder.id] = info

        if builder.id == invalid_id:
            invalid_form.version_id.choices = _version_choices(include_id=builder.version_id)
            invalid_form.repository_id.choices = _repository_choices()
            invalid_form.registry_target_id.choices = _registry_choices(include_id=builder.registry_target_id)
            invalid_form.managed_dockerfile_id.choices = _dockerfile_choices(include_id=builder.managed_dockerfile_id)
            invalid_form.allowed_role_ids.choices = _role_choices()
            invalid_form.default_branch.choices = _branch_choices(info, invalid_form.default_branch.data)
            edit_forms[builder.id] = invalid_form
        else:
            form = BuilderForm(obj=builder, prefix=_edit_prefix(builder.id))
            form.version_id.choices = _version_choices(include_id=builder.version_id)
            form.repository_id.choices = _repository_choices()
            form.registry_target_id.choices = _registry_choices(include_id=builder.registry_target_id)
            form.managed_dockerfile_id.choices = _dockerfile_choices(include_id=builder.managed_dockerfile_id)
            form.allowed_role_ids.choices = _role_choices()
            form.version_id.data = str(builder.version_id)
            form.repository_id.data = str(builder.repository_id)
            form.registry_target_id.data = str(builder.registry_target_id)
            form.managed_dockerfile_id.data = str(builder.managed_dockerfile_id) if builder.managed_dockerfile_id else ""
            form.allowed_role_ids.data = [str(role.id) for role in builder.allowed_roles]
            form.default_branch.choices = _branch_choices(info, builder.default_branch)
            form.default_branch.data = builder.default_branch
            edit_forms[builder.id] = form

    groups, ungrouped = _builder_groups(builders)

    # Same guards delete_builder() itself checks before rejecting the
    # request — computed here so the button can be disabled up front.
    delete_reasons = {}
    for builder in builders:
        build_count = ImageBuild.query.filter_by(builder_id=builder.id).count()
        referencing_steps = WorkflowStep.query.filter(WorkflowStep.selected_builders.any(id=builder.id)).count()
        if build_count:
            delete_reasons[builder.id] = f"Has {build_count} recorded build(s)."
        elif referencing_steps:
            delete_reasons[builder.id] = f"Individually selected in {referencing_steps} workflow step(s)."
        else:
            delete_reasons[builder.id] = None

    return render_template(
        "builders/index.html",
        builders=builders,
        delete_reasons=delete_reasons,
        groups=groups,
        ungrouped=ungrouped,
        versions=Version.query.order_by(Version.name).all(),
        selected_version_id=str(selected_version_id) if selected_version_id else "",
        create_form=create_form,
        edit_forms=edit_forms,
        repo_info_by_builder=repo_info_by_builder,
        last_builds=_last_build_by_builder(),
        object_suggestions=_object_suggestions(),
        group_name_suggestions=_group_name_suggestions(),
        bump_types=BUMP_TYPES,
        change_type_choices=_change_type_choices(),
        open_modal=open_modal,
    )


@builders_bp.route("/")
@permission_required("builder.view")
def index():
    selected_version_id = _parse_uuid(request.args.get("version_id"))
    return _render_index(selected_version_id=selected_version_id)


@builders_bp.route("/api/repo-info/<uuid:repository_id>")
@permission_required("builder.manage")
def repo_info(repository_id):
    repository = Repository.query.get_or_404(repository_id)
    try:
        provider = provider_for_git_source(repository.git_source)
        branches = provider.list_branches(repository.local_path)
        dockerfiles = provider.list_files(repository.local_path)
    except Exception as exc:
        log_error(
            source="builders.repo_info",
            exc=exc,
            description=f"Failed to read branches/Dockerfiles for repository '{repository.full_name}': {exc}",
        )
        return jsonify({"error": str(exc), "branches": [], "dockerfiles": []})
    return jsonify(
        {"branches": branches, "dockerfiles": dockerfiles, "default_branch": repository.default_branch}
    )


@builders_bp.route("/create", methods=["POST"])
@permission_required("builder.manage")
def create_builder():
    form = BuilderForm(prefix=CREATE_PREFIX)
    form.version_id.choices = _version_choices()
    form.repository_id.choices = _repository_choices()
    form.registry_target_id.choices = _registry_choices()
    form.managed_dockerfile_id.choices = _dockerfile_choices()
    form.allowed_role_ids.choices = _role_choices()

    if form.validate_on_submit():
        builder = Builder(
            name=form.name.data,
            version_id=uuid.UUID(form.version_id.data),
            repository_id=uuid.UUID(form.repository_id.data),
            default_branch=form.default_branch.data or None,
            group_name=(form.group_name.data or "").strip() or None,
            image_name=(form.image_name.data or "").strip() or None,
            registry_target_id=uuid.UUID(form.registry_target_id.data),
            default_build_args=_parse_build_args(),
        )
        dockerfile_error = _apply_dockerfile_source(builder, form)
        if dockerfile_error:
            flash(dockerfile_error, "error")
            return _render_index(create_form=form, open_modal="create-builder-modal")
        _apply_allowed_roles(builder, form)
        db.session.add(builder)
        db.session.commit()

        log_activity(
            action="CREATE_BUILDER",
            target_type="builder",
            target_id=str(builder.id),
            description=f"Created builder '{builder.name}'",
        )

        flash(f"Builder '{builder.name}' created.", "success")
        return redirect(url_for("builders.index"))

    return _render_index(create_form=form, open_modal="create-builder-modal")


@builders_bp.route("/<uuid:builder_id>/edit", methods=["POST"])
@permission_required("builder.manage")
def edit_builder(builder_id):
    builder = Builder.query.get_or_404(builder_id)
    form = BuilderForm(prefix=_edit_prefix(builder_id))
    form.version_id.choices = _version_choices(include_id=builder.version_id)
    form.repository_id.choices = _repository_choices()
    form.registry_target_id.choices = _registry_choices(include_id=builder.registry_target_id)
    form.managed_dockerfile_id.choices = _dockerfile_choices(include_id=builder.managed_dockerfile_id)
    form.allowed_role_ids.choices = _role_choices()

    if form.validate_on_submit():
        dockerfile_error = _apply_dockerfile_source(builder, form)
        if dockerfile_error:
            flash(dockerfile_error, "error")
            return _render_index(open_modal=f"edit-builder-modal-{builder_id}", invalid_edit=(builder_id, form))

        builder.name = form.name.data
        builder.version_id = uuid.UUID(form.version_id.data)
        builder.repository_id = uuid.UUID(form.repository_id.data)
        builder.default_branch = form.default_branch.data or None
        builder.group_name = (form.group_name.data or "").strip() or None
        builder.image_name = (form.image_name.data or "").strip() or None
        builder.registry_target_id = uuid.UUID(form.registry_target_id.data)
        builder.default_build_args = _parse_build_args()
        _apply_allowed_roles(builder, form)
        db.session.commit()

        log_activity(
            action="UPDATE_BUILDER",
            target_type="builder",
            target_id=str(builder.id),
            description=f"Updated builder '{builder.name}'",
        )

        flash(f"Builder '{builder.name}' updated.", "success")
        return redirect(url_for("builders.index"))

    return _render_index(open_modal=f"edit-builder-modal-{builder_id}", invalid_edit=(builder_id, form))


@builders_bp.route("/<uuid:builder_id>/delete", methods=["POST"])
@permission_required("builder.manage")
def delete_builder(builder_id):
    builder = Builder.query.get_or_404(builder_id)

    build_count = ImageBuild.query.filter_by(builder_id=builder.id).count()
    if build_count:
        flash(
            f"Cannot delete '{builder.name}' — it has {build_count} recorded build(s).", "error"
        )
        return redirect(url_for("builders.index"))

    # Blocks on a WorkflowStep's individual selection (a real FK, via
    # workflow_step_builders) — not on merely sharing a group_name with a
    # WorkflowStepGroup, which is a live/dynamic reference with no FK to
    # violate (see WorkflowStepGroup's docstring).
    referencing_steps = WorkflowStep.query.filter(WorkflowStep.selected_builders.any(id=builder.id)).count()
    if referencing_steps:
        flash(
            f"Cannot delete '{builder.name}' — it's individually selected in {referencing_steps} "
            "workflow step(s). Remove it from those steps first.",
            "error",
        )
        return redirect(url_for("builders.index"))

    name = builder.name
    builder_id_str = str(builder.id)
    db.session.delete(builder)
    db.session.commit()

    log_activity(
        action="DELETE_BUILDER",
        target_type="builder",
        target_id=builder_id_str,
        description=f"Deleted builder '{name}'",
    )

    flash(f"Builder '{name}' deleted.", "success")
    return redirect(url_for("builders.index"))


@builders_bp.route("/archived")
@permission_required("builder.view")
def archived():
    builders = [
        b for b in Builder.query.filter_by(is_active=False).order_by(Builder.name).all()
        if b.is_accessible_to(current_user)
    ]
    return render_template("builders/archived.html", builders=builders)


@builders_bp.route("/<uuid:builder_id>/disable", methods=["POST"])
@permission_required("builder.manage")
def disable_builder(builder_id):
    builder = Builder.query.get_or_404(builder_id)
    builder.is_active = False
    db.session.commit()

    log_activity(
        action="DISABLE_BUILDER",
        target_type="builder",
        target_id=str(builder.id),
        description=f"Disabled builder '{builder.name}'",
    )

    flash(f"'{builder.name}' disabled — moved to Archived.", "info")
    return redirect(url_for("builders.index"))


@builders_bp.route("/<uuid:builder_id>/enable", methods=["POST"])
@permission_required("builder.manage")
def enable_builder(builder_id):
    builder = Builder.query.get_or_404(builder_id)
    builder.is_active = True
    db.session.commit()

    log_activity(
        action="ENABLE_BUILDER",
        target_type="builder",
        target_id=str(builder.id),
        description=f"Re-enabled builder '{builder.name}'",
    )

    flash(f"'{builder.name}' re-enabled.", "success")
    return redirect(url_for("builders.archived"))


@builders_bp.route("/build", methods=["POST"])
@permission_required("builder.build")
def build():
    builder_ids = [_parse_uuid(raw) for raw in request.form.getlist("builder_ids")]
    builders = [Builder.query.get(bid) for bid in builder_ids if bid is not None]
    builders = [b for b in builders if b is not None]

    if not builders:
        flash("Select at least one Builder to build.", "error")
        return redirect(url_for("builders.index"))

    if any(not builder.is_accessible_to(current_user) for builder in builders):
        abort(403)

    # Defense in depth against a disabled Builder's id being POSTed
    # directly — the index page's checkboxes already stop offering one.
    if any(not builder.is_active for builder in builders):
        flash("One or more selected Builders are disabled and can't be built.", "error")
        return redirect(url_for("builders.index"))

    version_ids = {builder.version_id for builder in builders}
    if len(version_ids) > 1:
        flash("All selected Builders must share the same Version.", "error")
        return redirect(url_for("builders.index"))

    bump_type = request.form.get("bump_type")
    if bump_type not in BUMP_TYPES:
        flash("Bump type is required.", "error")
        return redirect(url_for("builders.index"))

    object_ids = request.form.getlist("object_ids")
    new_object_names = request.form.getlist("new_object_names")
    if not object_ids and not any((raw or "").strip() for raw in new_object_names):
        flash("At least one Object is required.", "error")
        return redirect(url_for("builders.index"))

    change_type_raw = (request.form.get("change_type_id") or "").strip()
    change_type_id = _parse_uuid(change_type_raw) if change_type_raw else None
    if change_type_id is None or ChangeType.query.get(change_type_id) is None:
        flash("Change type is required.", "error")
        return redirect(url_for("builders.index"))

    # The branch to build is always the Builder's own configured
    # default_branch — no per-build override. Change it via Edit Builder
    # if it needs to differ, not at build-trigger time.
    builder_branches = []
    for builder in builders:
        if not builder.default_branch:
            flash(f"No branch specified for builder '{builder.name}'.", "error")
            return redirect(url_for("builders.index"))
        builder_branches.append((builder, builder.default_branch))

    # Resolved last, right before the batch is actually created — get-or-
    # create writes new Object rows into the session, so every other
    # validation check runs first and can still bail out with a plain
    # redirect (an unflushed session is safely discarded on request
    # teardown either way, but this keeps intent explicit).
    objects = Object.resolve(object_ids, new_object_names)

    version = Version.query.get(version_ids.pop())
    additional_description = (request.form.get("additional_description") or "").strip()

    batch = enqueue_build_batch(
        version=version,
        bump_type=bump_type,
        builder_branches=builder_branches,
        objects=objects,
        additional_description=additional_description,
        requested_by=current_user.id,
        change_type_id=change_type_id,
    )

    log_activity(
        action="TRIGGER_BUILD_BATCH",
        target_type="build_batch",
        target_id=str(batch.id),
        description=f"Triggered a build batch ({len(builder_branches)} builder(s))",
    )

    flash(
        f"Batch queued ({len(builder_branches)} image(s)) — its version will be "
        "assigned once the build starts.",
        "success",
    )
    return redirect(url_for("images.list_images"))


@builders_bp.route("/build/preview", methods=["POST"])
@permission_required("builder.build")
def build_preview():
    """AJAX-only: syncs the selected Builders' repos and reads every commit
    since each one's last successful build, then returns a heuristic Bump
    Type guess (Conventional Commits) plus an AI-assisted Object/Change
    Type/Description draft (see build_prefill.suggest_metadata) for the
    trigger modal to pre-fill — never applied automatically, the user still
    reviews/edits every field before the real "Build" submit.

    Shares the single-build-at-a-time guard with the manual git Re-sync
    endpoint (git_sources.resync) so this preview's own sync_repo() calls
    never race the build worker syncing the same repo mid-build. Doesn't
    guard against two concurrent preview requests for the *same* builder
    racing each other, though — a narrow, rare edge case not worth a new
    locking mechanism for a best-effort preview.
    """
    builder_ids = [_parse_uuid(raw) for raw in request.form.getlist("builder_ids")]
    builders = [Builder.query.get(bid) for bid in builder_ids if bid is not None]
    builders = [b for b in builders if b is not None]

    if not builders:
        return jsonify({"error": "Select at least one Builder first."}), 400

    if any(not builder.is_accessible_to(current_user) for builder in builders):
        abort(403)

    if len({builder.version_id for builder in builders}) > 1:
        return jsonify({"error": "All selected Builders must share the same Version."}), 400

    if ImageBuild.query.filter_by(status="running").first() is not None:
        return jsonify({"error": "A build is currently running — try Preview again once it finishes."}), 409

    additional_description = (request.form.get("additional_description") or "").strip()
    prefill = compute_build_prefill(
        [(builder, builder.default_branch) for builder in builders],
        additional_description=additional_description,
    )

    return jsonify(
        {
            "bump_type": prefill["bump_type"],
            "matched_objects": [{"id": str(obj.id), "name": obj.name} for obj in prefill["matched_objects"]],
            "new_object_names": prefill["new_object_names"],
            "change_type_id": str(prefill["change_type_id"]) if prefill["change_type_id"] else None,
            "description": prefill["description"],
            "commit_count": prefill["commit_count"],
        }
    )


@builders_bp.route("/status")
@permission_required("builder.view")
def status():
    engine_status = get_engine_status()
    running = engine_status["running"]

    running_payload = None
    if running is not None:
        running_payload = {
            "id": str(running.id),
            "builder": running.builder.name,
            "batch_version": running.batch.full_version_string,
            "log_tail": _log_tail(running.build_log),
            "batch_progress": get_batch_progress(running.batch_id),
        }

    queue = [
        {
            "id": str(build.id),
            "builder": build.builder.name,
            # Not yet assigned — the Version bump (and this string) only
            # happens once the worker actually claims a batch's first image.
            "batch_version": build.batch.full_version_string or "pending",
            "position": index + 1,
        }
        for index, build in enumerate(engine_status["queued"])
    ]

    return jsonify({"busy": engine_status["busy"], "running": running_payload, "queue": queue})
