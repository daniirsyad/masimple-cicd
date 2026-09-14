import uuid
from datetime import datetime

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.workflows import workflows_bp
from app.blueprints.workflows.forms import ApproveBuildStepForm, BuildStepForm, DeployStepForm, WorkflowForm
from app.extensions import db
from app.models import (
    Builder,
    ChangeType,
    DeploymentManifest,
    ImageBuild,
    Role,
    Workflow,
    WorkflowRun,
    WorkflowStep,
    WorkflowStepGroup,
    WorkflowStepRun,
)
from app.services.build.prefill import compute_build_prefill
from app.services.build.versioning import BUMP_TYPES
from app.services.workflow.resolver import (
    resolve_builders_from_selection,
    resolve_step_builders,
    resolve_step_manifests,
)
from app.services.workflow.worker import approve_awaiting_step, enqueue_workflow_run, reject_awaiting_step
from app.utils.decorators import permission_required
from app.utils.logger import log_activity

AUTO_GENERATE_CHOICE = ("", "— Auto (generate at run time) —")

CREATE_PREFIX = "create-workflow-"


def _parse_uuid(value):
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _role_choices():
    return [(str(r.id), r.name) for r in Role.query.order_by(Role.name).all()]


def _apply_allowed_roles(workflow, form):
    selected_ids = {uuid.UUID(rid) for rid in form.allowed_role_ids.data}
    workflow.allowed_roles = Role.query.filter(Role.id.in_(selected_ids)).all() if selected_ids else []


def _change_type_choices(include_auto=False):
    choices = [
        (str(ct.id), ct.name)
        for ct in ChangeType.query.filter_by(is_active=True).order_by(ChangeType.name).all()
    ]
    return [AUTO_GENERATE_CHOICE] + choices if include_auto else choices


def _bump_type_choices(include_auto=False):
    choices = [(bump_type, bump_type.capitalize()) for bump_type in BUMP_TYPES]
    return [AUTO_GENERATE_CHOICE] + choices if include_auto else choices


def _builder_group_name_choices():
    # Only groups with at least one active member — a disabled Builder alone
    # shouldn't keep its group selectable for new steps (existing steps
    # already referencing the group still resolve whatever's still active in
    # it, via resolve_builders_from_selection).
    names = [
        row[0]
        for row in Builder.query.with_entities(Builder.group_name)
        .filter(Builder.group_name.isnot(None), Builder.is_active.is_(True))
        .distinct()
        .order_by(Builder.group_name)
    ]
    return [(name, name) for name in names]


def _ungrouped_builder_choices():
    return [
        (str(b.id), b.name)
        for b in Builder.query.filter_by(group_name=None, is_active=True).order_by(Builder.name).all()
    ]


def _manifest_group_name_choices():
    names = [
        row[0]
        for row in DeploymentManifest.query.with_entities(DeploymentManifest.group_name)
        .filter(DeploymentManifest.group_name.isnot(None), DeploymentManifest.is_active.is_(True))
        .distinct()
        .order_by(DeploymentManifest.group_name)
    ]
    return [(name, name) for name in names]


def _ungrouped_manifest_choices():
    return [
        (str(m.id), m.name)
        for m in DeploymentManifest.query.filter_by(group_name=None, is_active=True)
        .order_by(DeploymentManifest.name)
        .all()
    ]


def _workflow_or_404(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    if not workflow.is_accessible_to(current_user):
        abort(403)
    return workflow


def _step_target_summary(step):
    """Display-only: what a step currently resolves to, right now — a live
    preview using the exact same resolver the orchestrator itself uses (see
    app/services/workflow/resolver.py), so the workflow detail page never
    shows a stale/frozen list.
    """
    if step.step_type == "build":
        return [builder.name for builder in resolve_step_builders(step)]
    return [manifest.name for manifest in resolve_step_manifests(step)]


def _visible_active_workflows():
    return [
        w for w in Workflow.query.filter_by(is_active=True).order_by(Workflow.name).all()
        if w.is_accessible_to(current_user)
    ]


def _latest_runs_for(workflows):
    # workflow.is_active's own "Status" column reflects whether the
    # Workflow is enabled, not whether a run is in progress — this is a
    # separate, per-run lookup so the index page can also show the most
    # recent run's own status (queued/running/success/failed/...) without
    # the two being conflated.
    return {workflow.id: workflow.runs.order_by(WorkflowRun.created_at.desc()).first() for workflow in workflows}


def _render_index(create_form=None, open_modal=None):
    if create_form is None:
        create_form = WorkflowForm(prefix=CREATE_PREFIX)
    create_form.allowed_role_ids.choices = _role_choices()

    workflows = _visible_active_workflows()
    latest_runs = _latest_runs_for(workflows)

    return render_template(
        "workflows/index.html",
        workflows=workflows,
        create_form=create_form,
        open_modal=open_modal,
        latest_runs=latest_runs,
    )


def _render_archived():
    workflows = [
        w for w in Workflow.query.filter_by(is_active=False).order_by(Workflow.name).all()
        if w.is_accessible_to(current_user)
    ]
    return render_template("workflows/archived.html", workflows=workflows)


@workflows_bp.route("/")
@permission_required("workflow.view")
def index():
    return _render_index()


@workflows_bp.route("/statuses")
@permission_required("workflow.view")
def statuses():
    """Polled by the index page (see workflows.js) to auto-refresh the Last
    Run column without a manual reload — deliberately just {workflow_id:
    latest run status/id}, not a full HTML re-render, so an unrelated field
    a poll can't see changing (e.g. an edit made from another tab) never
    forces the page to reload; a change here always means an actual run
    advanced.
    """
    workflows = _visible_active_workflows()
    latest_runs = _latest_runs_for(workflows)
    return jsonify(
        {
            str(workflow_id): {"id": str(run.id), "status": run.status} if run else None
            for workflow_id, run in latest_runs.items()
        }
    )


@workflows_bp.route("/archived")
@permission_required("workflow.view")
def archived():
    return _render_archived()


@workflows_bp.route("/create", methods=["POST"])
@permission_required("workflow.manage")
def create_workflow():
    form = WorkflowForm(prefix=CREATE_PREFIX)
    form.allowed_role_ids.choices = _role_choices()

    if form.validate_on_submit():
        workflow = Workflow(
            name=form.name.data,
            description=(form.description.data or "").strip() or None,
            is_active=form.is_active.data,
            created_by=current_user.id,
        )
        _apply_allowed_roles(workflow, form)
        db.session.add(workflow)
        db.session.commit()

        log_activity(
            action="CREATE_WORKFLOW",
            target_type="workflow",
            target_id=str(workflow.id),
            description=f"Created workflow '{workflow.name}'",
        )

        flash(f"Workflow '{workflow.name}' created.", "success")
        return redirect(url_for("workflows.view_workflow", workflow_id=workflow.id))

    return _render_index(create_form=form, open_modal="create-workflow-modal")


def _render_view(workflow, edit_form=None, build_form=None, deploy_form=None, open_modal=None):
    if edit_form is None:
        edit_form = WorkflowForm(obj=workflow)
        edit_form.allowed_role_ids.data = [str(role.id) for role in workflow.allowed_roles]
    edit_form.allowed_role_ids.choices = _role_choices()

    if build_form is None:
        build_form = BuildStepForm()
    build_form.group_names.choices = _builder_group_name_choices()
    build_form.builder_ids.choices = _ungrouped_builder_choices()
    build_form.bump_type.choices = _bump_type_choices(include_auto=True)
    build_form.change_type_id.choices = _change_type_choices(include_auto=True)

    if deploy_form is None:
        deploy_form = DeployStepForm()
    deploy_form.group_names.choices = _manifest_group_name_choices()
    deploy_form.manifest_ids.choices = _ungrouped_manifest_choices()

    steps = workflow.steps.all()
    step_summaries = {step.id: _step_target_summary(step) for step in steps}
    runs = workflow.runs.order_by(WorkflowRun.created_at.desc()).limit(20).all()

    # Same guards delete_workflow/delete_step themselves check before
    # rejecting the request — computed here too so the Delete buttons can be
    # disabled up front instead of only failing after a click.
    run_count = workflow.runs.count()
    delete_workflow_reason = f"Has {run_count} recorded run(s)." if run_count else None
    step_delete_reasons = {}
    for step in steps:
        step_run_count = WorkflowStepRun.query.filter_by(workflow_step_id=step.id).count()
        step_delete_reasons[step.id] = f"Has {step_run_count} recorded run(s)." if step_run_count else None

    return render_template(
        "workflows/view.html",
        workflow=workflow,
        steps=steps,
        step_summaries=step_summaries,
        runs=runs,
        delete_workflow_reason=delete_workflow_reason,
        step_delete_reasons=step_delete_reasons,
        edit_form=edit_form,
        build_form=build_form,
        deploy_form=deploy_form,
        open_modal=open_modal,
    )


@workflows_bp.route("/<uuid:workflow_id>")
@permission_required("workflow.view")
def view_workflow(workflow_id):
    workflow = _workflow_or_404(workflow_id)
    return _render_view(workflow)


@workflows_bp.route("/<uuid:workflow_id>/edit", methods=["POST"])
@permission_required("workflow.manage")
def edit_workflow(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    form = WorkflowForm()
    form.allowed_role_ids.choices = _role_choices()

    if form.validate_on_submit():
        workflow.name = form.name.data
        workflow.description = (form.description.data or "").strip() or None
        workflow.is_active = form.is_active.data
        _apply_allowed_roles(workflow, form)
        db.session.commit()

        log_activity(
            action="UPDATE_WORKFLOW",
            target_type="workflow",
            target_id=str(workflow.id),
            description=f"Updated workflow '{workflow.name}'",
        )

        flash(f"Workflow '{workflow.name}' updated.", "success")
        return redirect(url_for("workflows.view_workflow", workflow_id=workflow.id))

    return _render_view(workflow, edit_form=form, open_modal="edit-workflow-modal")


@workflows_bp.route("/<uuid:workflow_id>/delete", methods=["POST"])
@permission_required("workflow.manage")
def delete_workflow(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)

    run_count = WorkflowRun.query.filter_by(workflow_id=workflow.id).count()
    if run_count:
        flash(f"Cannot delete '{workflow.name}' — it has {run_count} recorded run(s).", "error")
        return redirect(url_for("workflows.view_workflow", workflow_id=workflow.id))

    name = workflow.name
    workflow_id_str = str(workflow.id)
    # Cascades to this workflow's WorkflowSteps (and their group/item
    # selections) — see the cascade="all, delete-orphan" on
    # Workflow.steps/WorkflowStep.selected_groups.
    db.session.delete(workflow)
    db.session.commit()

    log_activity(
        action="DELETE_WORKFLOW",
        target_type="workflow",
        target_id=workflow_id_str,
        description=f"Deleted workflow '{name}'",
    )

    flash(f"Workflow '{name}' deleted.", "success")
    return redirect(url_for("workflows.index"))


@workflows_bp.route("/<uuid:workflow_id>/disable", methods=["POST"])
@permission_required("workflow.manage")
def disable_workflow(workflow_id):
    # Reuses the same is_active column the Edit form's own "Active" checkbox
    # already writes — this is just a guided shortcut for the "can't delete,
    # do this instead" case, not a separate concept.
    workflow = Workflow.query.get_or_404(workflow_id)
    workflow.is_active = False
    db.session.commit()

    log_activity(
        action="DISABLE_WORKFLOW",
        target_type="workflow",
        target_id=str(workflow.id),
        description=f"Disabled workflow '{workflow.name}'",
    )

    flash(f"'{workflow.name}' disabled — moved to Archived.", "info")
    return redirect(url_for("workflows.index"))


@workflows_bp.route("/<uuid:workflow_id>/enable", methods=["POST"])
@permission_required("workflow.manage")
def enable_workflow(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    workflow.is_active = True
    db.session.commit()

    log_activity(
        action="ENABLE_WORKFLOW",
        target_type="workflow",
        target_id=str(workflow.id),
        description=f"Re-enabled workflow '{workflow.name}'",
    )

    flash(f"'{workflow.name}' re-enabled.", "success")
    return redirect(url_for("workflows.archived"))


def _next_step_order(workflow_id):
    max_order = db.session.query(db.func.max(WorkflowStep.order)).filter_by(workflow_id=workflow_id).scalar()
    return 0 if max_order is None else max_order + 1


@workflows_bp.route("/<uuid:workflow_id>/steps/build", methods=["POST"])
@permission_required("workflow.manage")
def add_build_step(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    form = BuildStepForm()
    form.group_names.choices = _builder_group_name_choices()
    form.builder_ids.choices = _ungrouped_builder_choices()
    form.bump_type.choices = _bump_type_choices(include_auto=True)
    form.change_type_id.choices = _change_type_choices(include_auto=True)

    if form.validate_on_submit():
        if not form.group_names.data and not form.builder_ids.data:
            flash("Select at least one Builder group or individual Builder.", "error")
            return _render_view(workflow, build_form=form, open_modal="add-build-step-modal")

        auto = form.auto_generate_build_metadata.data
        if not auto and (not form.bump_type.data or not form.change_type_id.data or not (form.object.data or "").strip()):
            flash(
                "Fill in Version Bump, Change Type, and Object — or enable auto-generate at run time.", "error"
            )
            return _render_view(workflow, build_form=form, open_modal="add-build-step-modal")

        step = WorkflowStep(
            workflow_id=workflow.id,
            order=_next_step_order(workflow.id),
            step_type="build",
            on_failure=form.on_failure.data,
            auto_generate_build_metadata=auto,
            require_review_before_build=form.require_review_before_build.data,
            bump_type=None if auto else form.bump_type.data,
            change_type_id=None if auto else uuid.UUID(form.change_type_id.data),
            object=None if auto else form.object.data.strip(),
            additional_description=(form.additional_description.data or "").strip() or None,
        )
        db.session.add(step)
        db.session.flush()  # assign step.id so the group/item rows below can reference it

        for group_name in form.group_names.data:
            db.session.add(WorkflowStepGroup(workflow_step_id=step.id, group_name=group_name))

        selected_ids = {uuid.UUID(bid) for bid in form.builder_ids.data}
        step.selected_builders = Builder.query.filter(Builder.id.in_(selected_ids)).all() if selected_ids else []

        db.session.commit()

        log_activity(
            action="ADD_WORKFLOW_STEP",
            target_type="workflow",
            target_id=str(workflow.id),
            description=f"Added a build step to workflow '{workflow.name}'",
        )

        flash("Build step added.", "success")
        return redirect(url_for("workflows.view_workflow", workflow_id=workflow.id))

    return _render_view(workflow, build_form=form, open_modal="add-build-step-modal")


@workflows_bp.route("/<uuid:workflow_id>/steps/build/preview", methods=["POST"])
@permission_required("workflow.manage")
def build_step_preview(workflow_id):
    """AJAX-only, fired automatically whenever the Add Build Step modal's
    group/builder checkboxes change — no separate "Preview" button, unlike
    the Image Builder trigger modal's own click-to-preview (see
    builders.routes.build_preview) — since a step's target selection is
    itself made inside this modal via checkboxes, not chosen beforehand.

    Resolves the currently-checked selection the same way the orchestrator
    itself would (resolve_builders_from_selection) and returns the same
    heuristic Bump Type guess plus AI-assisted Object/Change Type/
    Description draft (compute_build_prefill) to pre-fill the rest of the
    form. Every field stays editable; nothing is submitted until "Add Build
    Step" is actually clicked — and note this only pre-fills the step's
    *authoring-time* values, which then replay unchanged on every future run
    of this step (same as every other WorkflowStep field today); it does not
    change WorkflowStep's existing "captured once, not re-resolved per run"
    design.
    """
    _workflow_or_404(workflow_id)

    group_names = request.form.getlist("group_names")
    builder_ids = [bid for bid in (_parse_uuid(raw) for raw in request.form.getlist("builder_ids")) if bid is not None]

    builders = resolve_builders_from_selection(group_names, builder_ids)
    if not builders:
        return jsonify({"error": "Select at least one Builder group or individual Builder first."}), 400

    if len({builder.version_id for builder in builders}) > 1:
        return jsonify({"error": "The selected Builders don't all share the same Version."}), 400

    if ImageBuild.query.filter_by(status="running").first() is not None:
        return jsonify({"error": "A build is currently running — try again once it finishes."}), 409

    additional_description = (request.form.get("additional_description") or "").strip()
    prefill = compute_build_prefill(
        [(builder, builder.default_branch) for builder in builders],
        additional_description=additional_description,
    )

    object_names = [obj.name for obj in prefill["matched_objects"]] + prefill["new_object_names"]

    return jsonify(
        {
            "bump_type": prefill["bump_type"],
            "object": ", ".join(object_names),
            "change_type_id": str(prefill["change_type_id"]) if prefill["change_type_id"] else None,
            "description": prefill["description"],
            "commit_count": prefill["commit_count"],
        }
    )


@workflows_bp.route("/<uuid:workflow_id>/steps/deploy", methods=["POST"])
@permission_required("workflow.manage")
def add_deploy_step(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    form = DeployStepForm()
    form.group_names.choices = _manifest_group_name_choices()
    form.manifest_ids.choices = _ungrouped_manifest_choices()

    if form.validate_on_submit():
        if not form.group_names.data and not form.manifest_ids.data:
            flash("Select at least one Manifest group or individual Manifest.", "error")
            return _render_view(workflow, deploy_form=form, open_modal="add-deploy-step-modal")

        step = WorkflowStep(
            workflow_id=workflow.id,
            order=_next_step_order(workflow.id),
            step_type="deploy",
            on_failure=form.on_failure.data,
        )
        db.session.add(step)
        db.session.flush()

        for group_name in form.group_names.data:
            db.session.add(WorkflowStepGroup(workflow_step_id=step.id, group_name=group_name))

        selected_ids = {uuid.UUID(mid) for mid in form.manifest_ids.data}
        step.selected_manifests = (
            DeploymentManifest.query.filter(DeploymentManifest.id.in_(selected_ids)).all() if selected_ids else []
        )

        db.session.commit()

        log_activity(
            action="ADD_WORKFLOW_STEP",
            target_type="workflow",
            target_id=str(workflow.id),
            description=f"Added a deploy step to workflow '{workflow.name}'",
        )

        flash("Deploy step added.", "success")
        return redirect(url_for("workflows.view_workflow", workflow_id=workflow.id))

    return _render_view(workflow, deploy_form=form, open_modal="add-deploy-step-modal")


@workflows_bp.route("/<uuid:workflow_id>/steps/<uuid:step_id>/delete", methods=["POST"])
@permission_required("workflow.manage")
def delete_step(workflow_id, step_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    step = WorkflowStep.query.filter_by(id=step_id, workflow_id=workflow.id).first_or_404()

    # A step that's actually been run (win or lose) can't be deleted — its
    # WorkflowStepRun rows have a NOT NULL FK back to it and this app
    # doesn't null-out-and-preserve step history the way e.g. a hard user
    # delete does for ActivityLog.user_id. The workflow itself has the same
    # rule (see delete_workflow) — a never-run step can still be removed
    # from a workflow that otherwise has run history on its other steps.
    run_count = WorkflowStepRun.query.filter_by(workflow_step_id=step.id).count()
    if run_count:
        flash(f"Cannot delete this step — it has {run_count} recorded run(s).", "error")
        return redirect(url_for("workflows.view_workflow", workflow_id=workflow.id))

    db.session.delete(step)
    db.session.commit()

    log_activity(
        action="DELETE_WORKFLOW_STEP",
        target_type="workflow",
        target_id=str(workflow.id),
        description=f"Deleted a step from workflow '{workflow.name}'",
    )

    flash("Step deleted.", "success")
    return redirect(url_for("workflows.view_workflow", workflow_id=workflow.id))


@workflows_bp.route("/<uuid:workflow_id>/steps/reorder", methods=["POST"])
@permission_required("workflow.manage")
def reorder_steps(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    payload = request.get_json(silent=True) or {}
    step_ids = payload.get("step_ids", [])

    if not isinstance(step_ids, list):
        return jsonify({"error": "Invalid payload."}), 400

    try:
        ids = [uuid.UUID(sid) for sid in step_ids]
    except (ValueError, AttributeError, TypeError):
        return jsonify({"error": "Invalid step id."}), 400

    steps = {
        step.id: step
        for step in WorkflowStep.query.filter(WorkflowStep.workflow_id == workflow.id, WorkflowStep.id.in_(ids)).all()
    }
    if len(steps) != len(ids) or len(steps) != workflow.steps.count():
        return jsonify({"error": "Step list doesn't match this workflow's current steps."}), 400

    for index, step_id in enumerate(ids):
        steps[step_id].order = index
    db.session.commit()

    log_activity(
        action="REORDER_WORKFLOW_STEPS",
        target_type="workflow",
        target_id=str(workflow.id),
        description=f"Reordered steps for workflow '{workflow.name}'",
    )

    return jsonify({"status": "ok"})


@workflows_bp.route("/<uuid:workflow_id>/run", methods=["POST"])
@permission_required("workflow.run")
def run_workflow(workflow_id):
    workflow = _workflow_or_404(workflow_id)

    if not workflow.is_active:
        flash(f"'{workflow.name}' is inactive — activate it before running.", "error")
        return redirect(url_for("workflows.view_workflow", workflow_id=workflow.id))

    if workflow.steps.count() == 0:
        flash("Add at least one step before running this workflow.", "error")
        return redirect(url_for("workflows.view_workflow", workflow_id=workflow.id))

    run = enqueue_workflow_run(workflow, triggered_by=current_user.id)

    log_activity(
        action="TRIGGER_WORKFLOW_RUN",
        target_type="workflow_run",
        target_id=str(run.id),
        description=f"Triggered a run of workflow '{workflow.name}'",
    )

    flash(f"Workflow '{workflow.name}' queued to run.", "success")
    return redirect(url_for("workflows.view_run", run_id=run.id))


def _step_run_links(step_run):
    if step_run.batch_id:
        return {"label": "View build", "url": url_for("images.list_images")}
    if step_run.deployment_run_id:
        return {"label": "View deployment", "url": url_for("deployment_runs.detail", run_id=step_run.deployment_run_id)}
    return None


def _approve_step_run_prefix(step_run_id):
    return f"approve-{step_run_id}-"


def _review_form_for(step_run):
    form = ApproveBuildStepForm(prefix=_approve_step_run_prefix(step_run.id))
    form.bump_type.choices = _bump_type_choices()
    form.change_type_id.choices = _change_type_choices()
    if not form.is_submitted():
        form.bump_type.data = step_run.suggested_bump_type
        form.change_type_id.data = (
            str(step_run.suggested_change_type_id) if step_run.suggested_change_type_id else None
        )
        form.object.data = step_run.suggested_object_names
        form.description.data = step_run.suggested_description
    return form


@workflows_bp.route("/runs/<uuid:run_id>")
@permission_required("workflow.view")
def view_run(run_id):
    run = WorkflowRun.query.get_or_404(run_id)
    if not run.workflow.is_accessible_to(current_user):
        abort(403)

    step_runs = run.step_runs.all()
    step_links = {step_run.id: _step_run_links(step_run) for step_run in step_runs}
    review_forms = {
        step_run.id: _review_form_for(step_run) for step_run in step_runs if step_run.status == "awaiting_review"
    }

    return render_template(
        "workflows/run.html", run=run, step_runs=step_runs, step_links=step_links, review_forms=review_forms
    )


@workflows_bp.route("/runs/<uuid:run_id>/status")
@permission_required("workflow.view")
def run_status(run_id):
    run = WorkflowRun.query.get_or_404(run_id)
    if not run.workflow.is_accessible_to(current_user):
        abort(403)

    step_runs = run.step_runs.all()
    return jsonify(
        {
            "status": run.status,
            "step_runs": [
                {
                    "id": str(step_run.id),
                    "step_order": step_run.step_order,
                    "step_type": step_run.step_type,
                    "status": step_run.status,
                    "error": step_run.error,
                    "link": _step_run_links(step_run),
                }
                for step_run in step_runs
            ],
        }
    )


def _awaiting_review_step_run_or_404(step_run_id):
    step_run = WorkflowStepRun.query.get_or_404(step_run_id)
    if not step_run.run.workflow.is_accessible_to(current_user):
        abort(403)
    if step_run.status != "awaiting_review":
        abort(404)
    return step_run


@workflows_bp.route("/step-runs/<uuid:step_run_id>/approve", methods=["POST"])
@permission_required("workflow.run")
def approve_step_run(step_run_id):
    step_run = _awaiting_review_step_run_or_404(step_run_id)
    run = step_run.run

    form = ApproveBuildStepForm(prefix=_approve_step_run_prefix(step_run.id))
    form.bump_type.choices = _bump_type_choices()
    form.change_type_id.choices = _change_type_choices()

    if not form.validate_on_submit():
        flash("Fill in Version Bump and Change Type before approving.", "error")
        return redirect(url_for("workflows.view_run", run_id=run.id))

    try:
        approve_awaiting_step(
            step_run,
            bump_type=form.bump_type.data,
            change_type_id=uuid.UUID(form.change_type_id.data),
            object_names_text=form.object.data,
            description=form.description.data,
            requested_by=current_user.id,
        )
    except ValueError as exc:
        flash(f"Could not approve — {exc}", "error")
        return redirect(url_for("workflows.view_run", run_id=run.id))

    log_activity(
        action="APPROVE_WORKFLOW_BUILD_STEP",
        target_type="workflow_run",
        target_id=str(run.id),
        description=f"Approved AI-suggested build metadata for a step in workflow '{run.workflow.name}'",
    )

    flash("Build approved — queued.", "success")
    return redirect(url_for("workflows.view_run", run_id=run.id))


@workflows_bp.route("/step-runs/<uuid:step_run_id>/reject", methods=["POST"])
@permission_required("workflow.run")
def reject_step_run(step_run_id):
    step_run = _awaiting_review_step_run_or_404(step_run_id)
    run = step_run.run

    reject_awaiting_step(step_run)

    log_activity(
        action="REJECT_WORKFLOW_BUILD_STEP",
        target_type="workflow_run",
        target_id=str(run.id),
        description=f"Rejected AI-suggested build metadata for a step in workflow '{run.workflow.name}'",
    )

    flash("Build step rejected.", "info")
    return redirect(url_for("workflows.view_run", run_id=run.id))
