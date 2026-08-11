import uuid

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.workflows import workflows_bp
from app.blueprints.workflows.forms import BuildStepForm, DeployStepForm, WorkflowForm
from app.extensions import db
from app.models import (
    Builder,
    ChangeType,
    DeploymentManifest,
    Role,
    Workflow,
    WorkflowRun,
    WorkflowStep,
    WorkflowStepGroup,
    WorkflowStepRun,
)
from app.services.build.versioning import BUMP_TYPES
from app.services.workflow.resolver import resolve_step_builders, resolve_step_manifests
from app.services.workflow.worker import enqueue_workflow_run
from app.utils.decorators import permission_required
from app.utils.logger import log_activity

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


def _change_type_choices():
    return [
        (str(ct.id), ct.name)
        for ct in ChangeType.query.filter_by(is_active=True).order_by(ChangeType.name).all()
    ]


def _bump_type_choices():
    return [(bump_type, bump_type.capitalize()) for bump_type in BUMP_TYPES]


def _builder_group_name_choices():
    names = [
        row[0]
        for row in Builder.query.with_entities(Builder.group_name)
        .filter(Builder.group_name.isnot(None))
        .distinct()
        .order_by(Builder.group_name)
    ]
    return [(name, name) for name in names]


def _ungrouped_builder_choices():
    return [
        (str(b.id), b.name) for b in Builder.query.filter_by(group_name=None).order_by(Builder.name).all()
    ]


def _manifest_group_name_choices():
    names = [
        row[0]
        for row in DeploymentManifest.query.with_entities(DeploymentManifest.group_name)
        .filter(DeploymentManifest.group_name.isnot(None))
        .distinct()
        .order_by(DeploymentManifest.group_name)
    ]
    return [(name, name) for name in names]


def _ungrouped_manifest_choices():
    return [
        (str(m.id), m.name)
        for m in DeploymentManifest.query.filter_by(group_name=None).order_by(DeploymentManifest.name).all()
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


def _render_index(create_form=None, open_modal=None):
    if create_form is None:
        create_form = WorkflowForm(prefix=CREATE_PREFIX)
    create_form.allowed_role_ids.choices = _role_choices()

    workflows = [w for w in Workflow.query.order_by(Workflow.name).all() if w.is_accessible_to(current_user)]

    return render_template(
        "workflows/index.html",
        workflows=workflows,
        create_form=create_form,
        open_modal=open_modal,
    )


@workflows_bp.route("/")
@permission_required("workflow.view")
def index():
    return _render_index()


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
    build_form.bump_type.choices = _bump_type_choices()
    build_form.change_type_id.choices = _change_type_choices()

    if deploy_form is None:
        deploy_form = DeployStepForm()
    deploy_form.group_names.choices = _manifest_group_name_choices()
    deploy_form.manifest_ids.choices = _ungrouped_manifest_choices()

    steps = workflow.steps.all()
    step_summaries = {step.id: _step_target_summary(step) for step in steps}
    runs = workflow.runs.order_by(WorkflowRun.created_at.desc()).limit(20).all()

    return render_template(
        "workflows/view.html",
        workflow=workflow,
        steps=steps,
        step_summaries=step_summaries,
        runs=runs,
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
    form.bump_type.choices = _bump_type_choices()
    form.change_type_id.choices = _change_type_choices()

    if form.validate_on_submit():
        if not form.group_names.data and not form.builder_ids.data:
            flash("Select at least one Builder group or individual Builder.", "error")
            return _render_view(workflow, build_form=form, open_modal="add-build-step-modal")

        step = WorkflowStep(
            workflow_id=workflow.id,
            order=_next_step_order(workflow.id),
            step_type="build",
            on_failure=form.on_failure.data,
            bump_type=form.bump_type.data,
            change_type_id=uuid.UUID(form.change_type_id.data),
            object=form.object.data.strip(),
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


@workflows_bp.route("/runs/<uuid:run_id>")
@permission_required("workflow.view")
def view_run(run_id):
    run = WorkflowRun.query.get_or_404(run_id)
    if not run.workflow.is_accessible_to(current_user):
        abort(403)

    step_runs = run.step_runs.all()
    step_links = {step_run.id: _step_run_links(step_run) for step_run in step_runs}

    return render_template("workflows/run.html", run=run, step_runs=step_runs, step_links=step_links)


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
