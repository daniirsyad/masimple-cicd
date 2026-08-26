import uuid

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from markupsafe import Markup

from app.blueprints.deployment_manifests import deployment_manifests_bp
from app.blueprints.deployment_manifests.forms import DeploymentManifestForm
from app.extensions import db
from app.models import (
    Builder,
    DeploymentExecution,
    DeploymentManifest,
    DeploymentManifestVersionBinding,
    DeploymentServer,
    ImageBuild,
    User,
    WorkflowStep,
)
from app.services.deployment.resolver import UnresolvedPlaceholderError, find_placeholder_keys, resolve_manifest
from app.services.deployment.worker import (
    enqueue_deployment_run,
    get_available_update,
    is_currently_deployed,
)
from app.utils.decorators import permission_required
from app.utils.logger import log_activity

CREATE_PREFIX = "create-manifest-"


def _edit_prefix(manifest_id):
    return f"manifest-{manifest_id}-"


def _parse_uuid(value):
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _server_choices(include_servers=None):
    """Active servers only — plus, if given, `include_servers` (a manifest's
    own currently-targeted servers) so re-submitting its edit form doesn't
    fail WTForms' choice validation, or silently drop a target, just because
    one of them has since been disabled.
    """
    choices = [(str(s.id), s.name) for s in DeploymentServer.query.filter_by(is_active=True).order_by(DeploymentServer.name).all()]
    if include_servers:
        existing_ids = {choice_id for choice_id, _ in choices}
        for server in include_servers:
            if str(server.id) not in existing_ids:
                choices.append((str(server.id), server.name))
    return choices


def _user_choices():
    return [
        (str(u.id), u.full_name or u.username)
        for u in User.query.filter_by(is_active=True).order_by(User.username).all()
    ]


def _builder_choices():
    return [(str(b.id), b.name) for b in Builder.query.filter_by(is_active=True).order_by(Builder.name).all()]


BUILDER_SUCCESSFUL_BUILDS_LIMIT = 20


def _builder_successful_builds():
    """{builder_id: [{id, tag, created_at}, ...]} — the last N successful
    ImageBuilds per Builder, most recent first, for the manifest form's
    per-binding "Pinned Build (optional)" picker (see deployment_manifests.js).
    """
    result = {}
    for builder in Builder.query.all():
        builds = (
            ImageBuild.query.filter_by(builder_id=builder.id, status="success")
            .order_by(ImageBuild.created_at.desc())
            .limit(BUILDER_SUCCESSFUL_BUILDS_LIMIT)
            .all()
        )
        result[str(builder.id)] = [
            {
                "id": str(build.id),
                "tag": build.image_tag,
                "created_at": build.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for build in builds
        ]
    return result


def _apply_target_servers(manifest, form):
    selected_ids = {uuid.UUID(sid) for sid in form.server_ids.data}
    manifest.target_servers = DeploymentServer.query.filter(DeploymentServer.id.in_(selected_ids)).all() if selected_ids else []


def _apply_allowed_users(manifest, form):
    selected_ids = {uuid.UUID(uid) for uid in form.allowed_user_ids.data}
    manifest.allowed_users = User.query.filter(User.id.in_(selected_ids)).all() if selected_ids else []


def _parse_version_bindings():
    """Parallel arrays (binding_key / binding_builder_id / binding_pinned_build_id)
    submitted by the manifest form's dynamic per-placeholder rows — mirrors
    builders.routes._parse_build_args. Rows missing a key or a builder are
    dropped (an incomplete row, e.g. a key with "— Select —" still chosen).
    """
    keys = request.form.getlist("binding_key")
    builder_ids = request.form.getlist("binding_builder_id")
    pinned_ids = request.form.getlist("binding_pinned_build_id")

    bindings = []
    for key, builder_id, pinned_id in zip(keys, builder_ids, pinned_ids):
        key = key.strip()
        builder_uuid = _parse_uuid(builder_id)
        if not key or builder_uuid is None:
            continue
        bindings.append(
            {
                "placeholder_key": key,
                "builder_id": builder_uuid,
                "pinned_image_build_id": _parse_uuid(pinned_id) if pinned_id else None,
            }
        )
    return bindings


def _apply_version_bindings(manifest, bindings):
    # Simple replace-all rather than diffing existing rows — no FK anywhere
    # else references a DeploymentManifestVersionBinding's own id (the
    # resolver looks bindings up fresh by placeholder_key on every deploy),
    # so there's nothing a delete-and-recreate could orphan.
    DeploymentManifestVersionBinding.query.filter_by(manifest_id=manifest.id).delete()
    for binding in bindings:
        db.session.add(DeploymentManifestVersionBinding(manifest_id=manifest.id, **binding))


def _group_name_suggestions():
    return [
        row[0]
        for row in DeploymentManifest.query.with_entities(DeploymentManifest.group_name)
        .filter(DeploymentManifest.group_name.isnot(None))
        .distinct()
        .order_by(DeploymentManifest.group_name)
    ]


def _manifest_live_server_ids(manifests):
    """{manifest_id_str: [server_id_str, ...]} of target servers each
    manifest is *currently deployed* on right now (same is_currently_deployed
    check the stop route itself uses to decide what actually gets torn
    down) — drives whether a row/group shows Deploy, Stop, or both. Restart
    reuses this same set (anything currently deployed can be restarted).
    """
    return {
        str(manifest.id): [
            str(server.id) for server in manifest.target_servers if is_currently_deployed(manifest.id, server.id)
        ]
        for manifest in manifests
    }


def _manifest_delete_reasons(manifests):
    """{manifest_id: reason_or_None} — the same three checks delete_manifest()
    itself runs, in the same order, before rejecting the request; computed
    here so the Delete button can be disabled up front instead of only
    failing after a click. The "still deployed" check intentionally mirrors
    delete_manifest()'s own candidate set (every server with ANY execution
    history for this manifest, not just its *current* target_servers, since
    those can drift apart after a manifest's targets are edited) rather than
    reusing manifest_live_servers/_manifest_live_server_ids above, which is
    scoped to current target_servers and would miss a manifest still live on
    a since-detached server.
    """
    reasons = {}
    for manifest in manifests:
        in_flight = DeploymentExecution.query.filter(
            DeploymentExecution.manifest_id == manifest.id, DeploymentExecution.status.in_(("queued", "running"))
        ).first()
        if in_flight is not None:
            reasons[manifest.id] = "A deploy/stop is currently in progress for it."
            continue

        server_ids = {
            row[0]
            for row in DeploymentExecution.query.with_entities(DeploymentExecution.server_id)
            .filter_by(manifest_id=manifest.id)
            .distinct()
        }
        live_servers = [
            server
            for server in DeploymentServer.query.filter(DeploymentServer.id.in_(server_ids)).all()
            if is_currently_deployed(manifest.id, server.id)
        ]
        if live_servers:
            names = ", ".join(server.name for server in live_servers)
            reasons[manifest.id] = f"Still deployed on: {names}."
            continue

        referencing_steps = WorkflowStep.query.filter(WorkflowStep.selected_manifests.any(id=manifest.id)).count()
        reasons[manifest.id] = (
            f"Individually selected in {referencing_steps} workflow step(s)." if referencing_steps else None
        )
    return reasons


def _manifest_update_availability(manifests, manifest_live_servers):
    """{manifest_id_str: [server_id_str, ...]} of servers where a manifest
    is currently deployed AND a newer resolvable version exists — drives
    the "Update" button. Only checked for servers already known to be
    live (nothing to update where nothing's deployed), keeping the
    resolve_manifest() calls this requires bounded to manifests that are
    actually live somewhere.
    """
    result = {}
    for manifest in manifests:
        live_ids = manifest_live_servers.get(str(manifest.id), [])
        if not live_ids:
            continue
        updatable = [
            str(server.id)
            for server in manifest.target_servers
            if str(server.id) in live_ids and get_available_update(manifest, server) is not None
        ]
        if updatable:
            result[str(manifest.id)] = updatable
    return result


def _enforce_trigger_access(manifests):
    """Abort 403 if the current user can't act on every selected manifest —
    both the manifest's own allowed_users gate and each of its target
    servers' allowed_roles gate must pass. Two separate checks with
    different empty-list defaults (see DeploymentManifest.is_accessible_to's
    docstring) — deliberately not merged into one.
    """
    if any(not manifest.is_accessible_to(current_user) for manifest in manifests):
        abort(403)
    servers = {server for manifest in manifests for server in manifest.target_servers}
    if any(not server.is_accessible_to(current_user) for server in servers):
        abort(403)


def _manifest_groups(manifests, manifest_live_servers, manifest_updates):
    groups = {}
    ungrouped = []
    for manifest in manifests:
        if manifest.group_name:
            groups.setdefault(manifest.group_name, []).append(manifest)
        else:
            ungrouped.append(manifest)
    # Deploy order within a group is user-configurable (drag-and-drop, see
    # reorder_manifests) — sort by that here rather than the outer
    # name-ordered query, so both the table's row order and a group
    # deploy/stop's execution order stay in sync with what was dragged.
    for manifests_in_group in groups.values():
        manifests_in_group.sort(key=lambda m: (m.order, m.name))

    sorted_groups = []
    for name in sorted(groups, key=str.lower):
        group_manifests = groups[name]
        # Deploy Group hides only once every manifest is live on every one
        # of its target servers; Stop/Restart Group shows as soon as
        # anything in the group is live anywhere (both share that same
        # signal — restarting only ever makes sense where something's
        # already deployed); Update Group shows only where a newer version
        # is actually resolvable. All can show at once mid-rollout.
        can_deploy = any(
            len(manifest_live_servers.get(str(m.id), [])) < len(m.target_servers) for m in group_manifests
        )
        can_stop = any(len(manifest_live_servers.get(str(m.id), [])) > 0 for m in group_manifests)
        has_update = any(len(manifest_updates.get(str(m.id), [])) > 0 for m in group_manifests)
        sorted_groups.append(
            {
                "name": name,
                "manifests": group_manifests,
                "can_deploy": can_deploy,
                "can_stop": can_stop,
                "can_restart": can_stop,
                "has_update": has_update,
            }
        )
    return sorted_groups, ungrouped


def _render_index(create_form=None, open_modal=None, invalid_edit=None):
    if create_form is None:
        create_form = DeploymentManifestForm(prefix=CREATE_PREFIX)
    create_form.server_ids.choices = _server_choices()
    create_form.allowed_user_ids.choices = _user_choices()

    manifests = DeploymentManifest.query.filter_by(is_active=True).order_by(DeploymentManifest.name).all()

    invalid_id, invalid_form = invalid_edit or (None, None)
    edit_forms = {}
    for manifest in manifests:
        if manifest.id == invalid_id:
            invalid_form.server_ids.choices = _server_choices(include_servers=manifest.target_servers)
            invalid_form.allowed_user_ids.choices = _user_choices()
            edit_forms[manifest.id] = invalid_form
        else:
            form = DeploymentManifestForm(obj=manifest, prefix=_edit_prefix(manifest.id))
            form.server_ids.choices = _server_choices(include_servers=manifest.target_servers)
            form.server_ids.data = [str(server.id) for server in manifest.target_servers]
            form.allowed_user_ids.choices = _user_choices()
            form.allowed_user_ids.data = [str(user.id) for user in manifest.allowed_users]
            edit_forms[manifest.id] = form

    manifest_live_servers = _manifest_live_server_ids(manifests)
    manifest_updates = _manifest_update_availability(manifests, manifest_live_servers)
    groups, ungrouped = _manifest_groups(manifests, manifest_live_servers, manifest_updates)
    delete_reasons = _manifest_delete_reasons(manifests)

    manifest_bindings = {
        str(manifest.id): [
            {
                "key": binding.placeholder_key,
                "builder_id": str(binding.builder_id),
                "pinned_image_build_id": str(binding.pinned_image_build_id) if binding.pinned_image_build_id else "",
            }
            for binding in manifest.version_bindings
        ]
        for manifest in manifests
    }

    return render_template(
        "deployment_manifests/index.html",
        manifests=manifests,
        groups=groups,
        ungrouped=ungrouped,
        manifest_live_servers=manifest_live_servers,
        manifest_updates=manifest_updates,
        delete_reasons=delete_reasons,
        create_form=create_form,
        edit_forms=edit_forms,
        builders=Builder.query.order_by(Builder.name).all(),
        group_name_suggestions=_group_name_suggestions(),
        manifest_bindings=manifest_bindings,
        builder_successful_builds=_builder_successful_builds(),
        open_modal=open_modal,
    )


@deployment_manifests_bp.route("/")
@permission_required("deployment_manifest.view")
def index():
    # One-shot hand-off from /yaml-generator's "Save as Manifest" action —
    # session.pop() means a page reload afterward won't re-prefill, and it
    # only ever activates when both the session key AND ?open=create are
    # present, so a normal index() call is unaffected. Only name/
    # yaml_content are prefilled; server_ids/allowed_user_ids are left for
    # the user to fill in on this same, otherwise-unmodified create form.
    prefill = session.pop("yaml_generator_prefill", None)
    create_form = None
    open_modal = None
    if prefill and request.args.get("open") == "create":
        create_form = DeploymentManifestForm(prefix=CREATE_PREFIX, data=prefill)
        open_modal = "create-manifest-modal"
    return _render_index(create_form=create_form, open_modal=open_modal)


@deployment_manifests_bp.route("/api/placeholder-keys", methods=["POST"])
@permission_required("deployment_manifest.manage")
def placeholder_keys():
    yaml_content = request.form.get("yaml_content") or ""
    return jsonify({"keys": find_placeholder_keys(yaml_content)})


@deployment_manifests_bp.route("/create", methods=["POST"])
@permission_required("deployment_manifest.manage")
def create_manifest():
    form = DeploymentManifestForm(prefix=CREATE_PREFIX)
    form.server_ids.choices = _server_choices()
    form.allowed_user_ids.choices = _user_choices()

    if form.validate_on_submit():
        manifest = DeploymentManifest(
            name=form.name.data,
            yaml_content=form.yaml_content.data,
            group_name=(form.group_name.data or "").strip() or None,
        )
        _apply_target_servers(manifest, form)
        _apply_allowed_users(manifest, form)
        db.session.add(manifest)
        db.session.flush()  # assign manifest.id so bindings below can reference it
        _apply_version_bindings(manifest, _parse_version_bindings())
        db.session.commit()

        log_activity(
            action="CREATE_DEPLOYMENT_MANIFEST",
            target_type="deployment_manifest",
            target_id=str(manifest.id),
            description=f"Created deployment manifest '{manifest.name}'",
        )

        flash(f"Manifest '{manifest.name}' created.", "success")
        return redirect(url_for("deployment_manifests.index"))

    return _render_index(create_form=form, open_modal="create-manifest-modal")


@deployment_manifests_bp.route("/<uuid:manifest_id>/edit", methods=["POST"])
@permission_required("deployment_manifest.manage")
def edit_manifest(manifest_id):
    manifest = DeploymentManifest.query.get_or_404(manifest_id)
    form = DeploymentManifestForm(prefix=_edit_prefix(manifest_id))
    form.server_ids.choices = _server_choices(include_servers=manifest.target_servers)
    form.allowed_user_ids.choices = _user_choices()

    if form.validate_on_submit():
        manifest.name = form.name.data
        manifest.yaml_content = form.yaml_content.data
        manifest.group_name = (form.group_name.data or "").strip() or None
        _apply_target_servers(manifest, form)
        _apply_allowed_users(manifest, form)
        _apply_version_bindings(manifest, _parse_version_bindings())
        db.session.commit()

        log_activity(
            action="UPDATE_DEPLOYMENT_MANIFEST",
            target_type="deployment_manifest",
            target_id=str(manifest.id),
            description=f"Updated deployment manifest '{manifest.name}'",
        )

        flash(f"Manifest '{manifest.name}' updated.", "success")
        return redirect(url_for("deployment_manifests.index"))

    return _render_index(open_modal=f"edit-manifest-modal-{manifest_id}", invalid_edit=(manifest_id, form))


@deployment_manifests_bp.route("/reorder", methods=["POST"])
@permission_required("deployment_manifest.manage")
def reorder_manifests():
    """Persists a group's drag-and-drop order — one flat list per group_name
    (unlike menus' top-level/children split, manifest groups aren't nested).
    A group deploy walks manifest.order ascending; a group stop walks it
    descending (see deploy()/stop() below and _manifest_groups above).
    """
    payload = request.get_json(silent=True) or {}
    group_name = (payload.get("group_name") or "").strip() or None
    manifest_ids = payload.get("manifest_ids", [])

    if not isinstance(manifest_ids, list):
        return jsonify({"error": "Invalid payload."}), 400

    try:
        ids = [uuid.UUID(mid) for mid in manifest_ids]
    except (ValueError, AttributeError, TypeError):
        return jsonify({"error": "Invalid manifest id."}), 400

    manifests = {m.id: m for m in DeploymentManifest.query.filter(DeploymentManifest.id.in_(ids)).all()}
    if len(manifests) != len(ids):
        return jsonify({"error": "One or more manifests no longer exist."}), 400

    if any(manifests[mid].group_name != group_name for mid in ids):
        return jsonify({"error": "All manifests being reordered must belong to the same group."}), 400

    for index, manifest_id in enumerate(ids):
        manifests[manifest_id].order = index

    db.session.commit()

    log_activity(
        action="REORDER_DEPLOYMENT_MANIFESTS",
        target_type="deployment_manifest",
        description=f"Reordered manifests in group '{group_name or '(ungrouped)'}'",
    )

    return jsonify({"status": "ok"})


@deployment_manifests_bp.route("/<uuid:manifest_id>/delete", methods=["POST"])
@permission_required("deployment_manifest.manage")
def delete_manifest(manifest_id):
    manifest = DeploymentManifest.query.get_or_404(manifest_id)

    in_flight = DeploymentExecution.query.filter(
        DeploymentExecution.manifest_id == manifest.id, DeploymentExecution.status.in_(("queued", "running"))
    ).first()
    if in_flight is not None:
        flash(f"Cannot delete '{manifest.name}' — a deploy/stop is currently in progress for it.", "error")
        return redirect(url_for("deployment_manifests.index"))

    # Block only while it's actually still deployed somewhere — not merely
    # because it has execution history. A manifest that's been fully
    # stopped (or never made it live) is safe to delete even with past
    # runs recorded; deploying-and-never-stopping is the case worth
    # blocking, since deleting it would orphan whatever's still running.
    server_ids = {
        row[0]
        for row in DeploymentExecution.query.with_entities(DeploymentExecution.server_id)
        .filter_by(manifest_id=manifest.id)
        .distinct()
    }
    live_servers = [
        server
        for server in DeploymentServer.query.filter(DeploymentServer.id.in_(server_ids)).all()
        if is_currently_deployed(manifest.id, server.id)
    ]
    if live_servers:
        names = ", ".join(server.name for server in live_servers)
        flash(f"Cannot delete '{manifest.name}' — still deployed on: {names}. Stop it first.", "error")
        return redirect(url_for("deployment_manifests.index"))

    # Blocks on a WorkflowStep's individual selection (a real FK, via
    # workflow_step_manifests) — not on merely sharing a group_name with a
    # WorkflowStepGroup, which is a live/dynamic reference with no FK to
    # violate (see WorkflowStepGroup's docstring).
    referencing_steps = WorkflowStep.query.filter(WorkflowStep.selected_manifests.any(id=manifest.id)).count()
    if referencing_steps:
        flash(
            f"Cannot delete '{manifest.name}' — it's individually selected in {referencing_steps} "
            "workflow step(s). Remove it from those steps first.",
            "error",
        )
        return redirect(url_for("deployment_manifests.index"))

    # Detach rather than cascade or block: version bindings have no
    # standalone meaning once the manifest is gone, so they're dropped: but
    # DeploymentExecution rows are deployment_runs' history — null out their
    # manifest_id (nullable for exactly this) so that history survives
    # instead of failing on the FK or being deleted along with it. Mirrors
    # users.routes hard-delete nulling ActivityLog.user_id.
    DeploymentManifestVersionBinding.query.filter_by(manifest_id=manifest.id).delete()
    DeploymentExecution.query.filter_by(manifest_id=manifest.id).update({"manifest_id": None})

    name = manifest.name
    manifest_id_str = str(manifest.id)
    db.session.delete(manifest)
    db.session.commit()

    log_activity(
        action="DELETE_DEPLOYMENT_MANIFEST",
        target_type="deployment_manifest",
        target_id=manifest_id_str,
        description=f"Deleted deployment manifest '{name}'",
    )

    flash(f"Manifest '{name}' deleted.", "success")
    return redirect(url_for("deployment_manifests.index"))


@deployment_manifests_bp.route("/archived")
@permission_required("deployment_manifest.manage")
def archived():
    manifests = DeploymentManifest.query.filter_by(is_active=False).order_by(DeploymentManifest.name).all()
    # No live-status/update computation needed here — the archived page has
    # no Deploy/Stop/Update buttons, just a Restore action, so the empty
    # dicts below are enough for _manifest_groups' grouping-only use.
    groups, ungrouped = _manifest_groups(manifests, {}, {})
    return render_template("deployment_manifests/archived.html", groups=groups, ungrouped=ungrouped)


@deployment_manifests_bp.route("/<uuid:manifest_id>/disable", methods=["POST"])
@permission_required("deployment_manifest.manage")
def disable_manifest(manifest_id):
    manifest = DeploymentManifest.query.get_or_404(manifest_id)
    manifest.is_active = False
    db.session.commit()

    log_activity(
        action="DISABLE_DEPLOYMENT_MANIFEST",
        target_type="deployment_manifest",
        target_id=str(manifest.id),
        description=f"Disabled deployment manifest '{manifest.name}'",
    )

    flash(f"'{manifest.name}' disabled — moved to Archived.", "info")
    return redirect(url_for("deployment_manifests.index"))


@deployment_manifests_bp.route("/<uuid:manifest_id>/enable", methods=["POST"])
@permission_required("deployment_manifest.manage")
def enable_manifest(manifest_id):
    manifest = DeploymentManifest.query.get_or_404(manifest_id)
    manifest.is_active = True
    db.session.commit()

    log_activity(
        action="ENABLE_DEPLOYMENT_MANIFEST",
        target_type="deployment_manifest",
        target_id=str(manifest.id),
        description=f"Re-enabled deployment manifest '{manifest.name}'",
    )

    flash(f"'{manifest.name}' re-enabled.", "success")
    return redirect(url_for("deployment_manifests.archived"))


def _load_manifests_from_form():
    manifest_ids = [_parse_uuid(raw) for raw in request.form.getlist("manifest_ids")]
    return [m for m in (DeploymentManifest.query.get(mid) for mid in manifest_ids if mid) if m is not None]


def _ordered_by_group(manifests, reverse):
    """A group deploy/update walks manifest.order ascending; a group
    stop/restart walks it descending (tear down/restart in reverse of how
    things went up) — don't just trust whatever order the client submitted
    manifest_ids in. Standalone (mixed-group or ungrouped) selections are
    left as submitted.
    """
    group_names = {manifest.group_name for manifest in manifests}
    group_name = group_names.pop() if len(group_names) == 1 else None
    if group_name is not None:
        manifests.sort(key=lambda m: (m.order, m.name), reverse=reverse)
    return manifests, group_name


@deployment_manifests_bp.route("/api/preview", methods=["POST"])
@permission_required("deployment_manifest.view")
def preview():
    """Resolved-version + target-server preview for the Deploy/Update
    trigger confirmation modals — never applies anything, just resolves.
    """
    manifests = _load_manifests_from_form()

    previews = []
    for manifest in manifests:
        entry = {
            "manifest_id": str(manifest.id),
            "name": manifest.name,
            "servers": [server.name for server in manifest.target_servers],
        }
        try:
            _rendered, resolved_versions = resolve_manifest(manifest)
            entry["resolved_versions"] = resolved_versions
            entry["error"] = None
        except UnresolvedPlaceholderError as exc:
            entry["resolved_versions"] = {}
            entry["error"] = str(exc)
        previews.append(entry)

    return jsonify({"previews": previews})


def _trigger_deploy_action(manifests, activity_action, verb, no_manifest_message):
    """Shared body for deploy() and update() — an update IS a deploy, just
    surfaced under a different button/permission for the case where a
    manifest is already live everywhere and a newer version showed up
    since (see worker.get_available_update). Both re-resolve fresh, so
    there's no behavioral difference at the enqueue level.
    """
    if not manifests:
        flash(no_manifest_message, "error")
        return redirect(url_for("deployment_manifests.index"))

    # Deploying/updating an archived manifest is refused — but restart()/
    # stop() (the *teardown*-style actions, _trigger_teardown_style_action)
    # deliberately don't share this check: an archived manifest that's
    # still deployed must still be stoppable/restartable, only *new*
    # rollouts of it are blocked.
    disabled = [manifest.name for manifest in manifests if not manifest.is_active]
    if disabled:
        flash(f"Cannot {verb}: disabled manifest(s): {', '.join(disabled)}.", "error")
        return redirect(url_for("deployment_manifests.index"))

    servers = {server for manifest in manifests for server in manifest.target_servers}
    if not servers:
        flash("None of the selected Manifests have any target servers configured.", "error")
        return redirect(url_for("deployment_manifests.index"))

    _enforce_trigger_access(manifests)

    manifests, group_name = _ordered_by_group(manifests, reverse=False)

    run, execution_count = enqueue_deployment_run(
        manifests=manifests, triggered_by=current_user.id, group_name=group_name
    )

    log_activity(
        action=activity_action,
        target_type="deployment_run",
        target_id=str(run.id),
        description=f"Triggered a {verb} run ({len(manifests)} manifest(s), {execution_count} execution(s))",
    )

    runs_url = url_for("deployment_runs.index")
    flash(
        Markup(
            f"{verb.capitalize()} queued ({execution_count} execution(s)). "
            f'<a href="{runs_url}" class="link link-primary">View in Deployment Runs</a>'
        ),
        "success",
    )
    return redirect(url_for("deployment_manifests.index"))


@deployment_manifests_bp.route("/deploy", methods=["POST"])
@permission_required("deployment.deploy")
def deploy():
    return _trigger_deploy_action(
        _load_manifests_from_form(), "TRIGGER_DEPLOYMENT_RUN", "deployment", "Select at least one Manifest to deploy."
    )


@deployment_manifests_bp.route("/update", methods=["POST"])
@permission_required("deployment.update")
def update():
    return _trigger_deploy_action(
        _load_manifests_from_form(), "TRIGGER_DEPLOYMENT_UPDATE", "update", "Select at least one Manifest to update."
    )


def _teardown_style_preview(manifests, reverse):
    """Shared body for stop_preview()/restart_preview() — which (manifest,
    server) pairs are actually currently deployed, in the order the actual
    action will use. Never acts, just reports.
    """
    manifests, _group_name = _ordered_by_group(manifests, reverse=reverse)

    previews = []
    for manifest in manifests:
        live_servers = [
            server.name for server in manifest.target_servers if is_currently_deployed(manifest.id, server.id)
        ]
        previews.append({"manifest_id": str(manifest.id), "name": manifest.name, "servers": live_servers})

    return jsonify({"previews": previews})


def _trigger_teardown_style_action(
    manifests, action, activity_action, verb, no_manifest_message, nothing_message
):
    """Shared body for stop()/restart() — both only ever act on (manifest,
    server) pairs that are actually currently deployed (see
    enqueue_deployment_run's stop/restart branch), walking a shared group
    in DESCENDING order (reverse of how a group deploy/update goes up).
    """
    if not manifests:
        flash(no_manifest_message, "error")
        return redirect(url_for("deployment_manifests.index"))

    _enforce_trigger_access(manifests)

    manifests, group_name = _ordered_by_group(manifests, reverse=True)

    run, execution_count = enqueue_deployment_run(
        manifests=manifests, triggered_by=current_user.id, group_name=group_name, action=action
    )

    if execution_count == 0:
        db.session.delete(run)
        db.session.commit()
        flash(nothing_message, "info")
        return redirect(url_for("deployment_manifests.index"))

    log_activity(
        action=activity_action,
        target_type="deployment_run",
        target_id=str(run.id),
        description=f"Triggered a {verb} run ({len(manifests)} manifest(s), {execution_count} execution(s))",
    )

    runs_url = url_for("deployment_runs.index")
    flash(
        Markup(
            f"{verb.capitalize()} queued ({execution_count} execution(s)). "
            f'<a href="{runs_url}" class="link link-primary">View in Deployment Runs</a>'
        ),
        "success",
    )
    return redirect(url_for("deployment_manifests.index"))


@deployment_manifests_bp.route("/api/stop-preview", methods=["POST"])
@permission_required("deployment_manifest.view")
def stop_preview():
    return _teardown_style_preview(_load_manifests_from_form(), reverse=True)


@deployment_manifests_bp.route("/stop", methods=["POST"])
@permission_required("deployment.stop")
def stop():
    return _trigger_teardown_style_action(
        _load_manifests_from_form(),
        action="stop",
        activity_action="TRIGGER_DEPLOYMENT_STOP",
        verb="stop",
        no_manifest_message="Select at least one Manifest to stop.",
        nothing_message="Nothing currently deployed for the selected manifest(s).",
    )


@deployment_manifests_bp.route("/api/restart-preview", methods=["POST"])
@permission_required("deployment_manifest.view")
def restart_preview():
    return _teardown_style_preview(_load_manifests_from_form(), reverse=True)


@deployment_manifests_bp.route("/restart", methods=["POST"])
@permission_required("deployment.restart")
def restart():
    return _trigger_teardown_style_action(
        _load_manifests_from_form(),
        action="restart",
        activity_action="TRIGGER_DEPLOYMENT_RESTART",
        verb="restart",
        no_manifest_message="Select at least one Manifest to restart.",
        nothing_message="Nothing currently deployed for the selected manifest(s) to restart.",
    )
