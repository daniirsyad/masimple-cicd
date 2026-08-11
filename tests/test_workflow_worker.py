"""Tests for the workflow orchestrator (app/services/workflow/worker.py).

Neither the real build worker nor the real deploy worker runs under TESTING
(see their own start_worker's TESTING-skip guard), so a step's underlying
BuildBatch/DeploymentRun stays "queued" forever unless a test manually
flips its status — exactly standing in for what those workers would do in
production. _tick() is called directly (white-box) to advance the
orchestrator one step at a time between those manual status flips.
"""
import pytest

from app.extensions import db
from app.models import (
    BuildBatch,
    ChangeType,
    DeploymentManifest,
    DeploymentRun,
    GitSource,
    RegistryTarget,
    Repository,
    Version,
    VersionType,
    Workflow,
    WorkflowRun,
    WorkflowStep,
    WorkflowStepGroup,
    WorkflowStepRun,
)
from app.services.workflow.worker import _tick, enqueue_workflow_run


@pytest.fixture
def base_entities(app):
    with app.app_context():
        git_source = GitSource(name="conn", provider_type="github", encrypted_token="x")
        db.session.add(git_source)
        db.session.flush()
        repository = Repository(
            git_source_id=git_source.id, full_name="org/repo", local_path="/tmp/repo", status="ready"
        )
        db.session.add(repository)
        registry_target = RegistryTarget(name="reg", provider_type="dockerhub")
        db.session.add(registry_target)
        version_type = VersionType(name="DEV")
        db.session.add(version_type)
        db.session.flush()
        version = Version(name="svc", version_type_id=version_type.id)
        db.session.add(version)
        change_type = ChangeType(name="Bug Fix")
        db.session.add(change_type)
        db.session.commit()
        return {
            "repository_id": repository.id,
            "registry_target_id": registry_target.id,
            "version_id": version.id,
            "change_type_id": change_type.id,
        }


def _make_builder(base_entities, name="b1", group_name=None):
    from app.models import Builder

    builder = Builder(
        name=name,
        version_id=base_entities["version_id"],
        repository_id=base_entities["repository_id"],
        default_branch="main",
        group_name=group_name,
        registry_target_id=base_entities["registry_target_id"],
    )
    db.session.add(builder)
    db.session.flush()
    return builder


def _make_manifest(name="m1", group_name=None):
    manifest = DeploymentManifest(name=name, yaml_content="kind: Pod", group_name=group_name)
    db.session.add(manifest)
    db.session.flush()
    return manifest


def _make_build_step(workflow, base_entities, builders, order=0, on_failure="stop"):
    step = WorkflowStep(
        workflow_id=workflow.id,
        order=order,
        step_type="build",
        on_failure=on_failure,
        bump_type="patch",
        change_type_id=base_entities["change_type_id"],
        object="svc",
    )
    db.session.add(step)
    db.session.flush()
    step.selected_builders = builders
    return step


def _make_deploy_step(workflow, manifests, order=0, on_failure="stop"):
    step = WorkflowStep(workflow_id=workflow.id, order=order, step_type="deploy", on_failure=on_failure)
    db.session.add(step)
    db.session.flush()
    step.selected_manifests = manifests
    return step


class TestSingleBuildStepRun:
    def test_run_advances_through_queued_running_success(self, app, base_entities):
        with app.app_context():
            workflow = Workflow(name="wf")
            db.session.add(workflow)
            db.session.flush()
            builder = _make_builder(base_entities)
            _make_build_step(workflow, base_entities, [builder])
            db.session.commit()

            run = enqueue_workflow_run(workflow, triggered_by=None)
            assert run.status == "queued"

            _tick(app)
            db.session.refresh(run)
            assert run.status == "running"
            step_run = WorkflowStepRun.query.filter_by(workflow_run_id=run.id).one()
            assert step_run.status == "running"
            batch = BuildBatch.query.get(step_run.batch_id)
            assert batch is not None

            # Simulate the (not-running-under-TESTING) build worker finishing it.
            batch.status = "success"
            db.session.commit()

            _tick(app)
            db.session.refresh(run)
            db.session.refresh(step_run)
            assert step_run.status == "success"
            assert run.status == "success"
            assert run.has_failed_step is False

    def test_step_with_nothing_resolved_fails_immediately_without_a_batch(self, app, base_entities):
        with app.app_context():
            workflow = Workflow(name="wf")
            db.session.add(workflow)
            db.session.flush()
            _make_build_step(workflow, base_entities, [])  # nothing selected
            db.session.commit()

            run = enqueue_workflow_run(workflow, triggered_by=None)
            _tick(app)

            db.session.refresh(run)
            assert run.status == "failed"
            step_run = WorkflowStepRun.query.filter_by(workflow_run_id=run.id).one()
            assert step_run.status == "failed"
            assert step_run.batch_id is None
            assert "No builders resolved" in step_run.error


class TestMultiStepRun:
    def test_build_then_deploy_advances_across_two_real_queues(self, app, base_entities):
        with app.app_context():
            workflow = Workflow(name="wf")
            db.session.add(workflow)
            db.session.flush()
            builder = _make_builder(base_entities)
            manifest = _make_manifest()
            _make_build_step(workflow, base_entities, [builder], order=0)
            _make_deploy_step(workflow, [manifest], order=1)
            db.session.commit()

            run = enqueue_workflow_run(workflow, triggered_by=None)
            _tick(app)  # starts step 0 (build)

            build_step_run = WorkflowStepRun.query.filter_by(workflow_run_id=run.id, step_order=0).one()
            batch = BuildBatch.query.get(build_step_run.batch_id)
            batch.status = "success"
            db.session.commit()

            _tick(app)  # finishes step 0, starts step 1 (deploy)

            db.session.refresh(run)
            assert run.status == "running"
            deploy_step_run = WorkflowStepRun.query.filter_by(workflow_run_id=run.id, step_order=1).one()
            assert deploy_step_run.status == "running"
            deployment_run = DeploymentRun.query.get(deploy_step_run.deployment_run_id)
            assert deployment_run is not None

            deployment_run.status = "success"
            db.session.commit()

            _tick(app)  # finishes step 1

            db.session.refresh(run)
            assert run.status == "success"

    def test_failure_with_on_failure_stop_halts_the_run(self, app, base_entities):
        with app.app_context():
            workflow = Workflow(name="wf")
            db.session.add(workflow)
            db.session.flush()
            builder = _make_builder(base_entities)
            manifest = _make_manifest()
            _make_build_step(workflow, base_entities, [builder], order=0, on_failure="stop")
            _make_deploy_step(workflow, [manifest], order=1)
            db.session.commit()

            run = enqueue_workflow_run(workflow, triggered_by=None)
            _tick(app)

            build_step_run = WorkflowStepRun.query.filter_by(workflow_run_id=run.id, step_order=0).one()
            BuildBatch.query.get(build_step_run.batch_id).status = "failed"
            db.session.commit()

            _tick(app)

            db.session.refresh(run)
            assert run.status == "failed"
            assert run.has_failed_step is True
            # The deploy step never started — on_failure="stop" halted the run.
            assert WorkflowStepRun.query.filter_by(workflow_run_id=run.id, step_order=1).first() is None

    def test_failure_with_on_failure_continue_still_runs_the_next_step(self, app, base_entities):
        with app.app_context():
            workflow = Workflow(name="wf")
            db.session.add(workflow)
            db.session.flush()
            builder = _make_builder(base_entities)
            manifest = _make_manifest()
            _make_build_step(workflow, base_entities, [builder], order=0, on_failure="continue")
            _make_deploy_step(workflow, [manifest], order=1)
            db.session.commit()

            run = enqueue_workflow_run(workflow, triggered_by=None)
            _tick(app)

            build_step_run = WorkflowStepRun.query.filter_by(workflow_run_id=run.id, step_order=0).one()
            BuildBatch.query.get(build_step_run.batch_id).status = "failed"
            db.session.commit()

            _tick(app)  # step 0 fails but continues into step 1

            deploy_step_run = WorkflowStepRun.query.filter_by(workflow_run_id=run.id, step_order=1).one()
            DeploymentRun.query.get(deploy_step_run.deployment_run_id).status = "success"
            db.session.commit()

            _tick(app)

            db.session.refresh(run)
            assert run.status == "completed_with_failures"
            assert run.has_failed_step is True


class TestBuildStepValidation:
    def test_builders_across_different_versions_fail_the_step(self, app, base_entities):
        with app.app_context():
            other_version_type = VersionType(name="PROD")
            db.session.add(other_version_type)
            db.session.flush()
            other_version = Version(name="other-svc", version_type_id=other_version_type.id)
            db.session.add(other_version)
            db.session.flush()

            workflow = Workflow(name="wf")
            db.session.add(workflow)
            db.session.flush()
            builder_a = _make_builder(base_entities, name="a")
            builder_b = _make_builder({**base_entities, "version_id": other_version.id}, name="b")
            _make_build_step(workflow, base_entities, [builder_a, builder_b])
            db.session.commit()

            run = enqueue_workflow_run(workflow, triggered_by=None)
            _tick(app)

            db.session.refresh(run)
            assert run.status == "failed"
            step_run = WorkflowStepRun.query.filter_by(workflow_run_id=run.id).one()
            assert "same Version" in step_run.error
