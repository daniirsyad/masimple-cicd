"""Tests for the manual build/deploy start/finish Telegram notifications —
app/services/telegram/helpers.py's notify_build_started/notify_build_finished
and notify_deploy_started/notify_deploy_finished, wired into
app/services/build/worker.py and app/services/deployment/worker.py.
"""
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
    Role,
    User,
    Version,
    VersionType,
    Workflow,
    WorkflowRun,
    WorkflowStep,
    WorkflowStepRun,
)
from app.services.build.worker import _claim_next_job as build_claim_next_job
from app.services.build.worker import _update_batch_status, enqueue_build_batch
from app.services.deployment.base import DeployResult
from app.services.deployment.kubernetes_provider import KubernetesProvider
from app.services.deployment.worker import _claim_next_job as deploy_claim_next_job
from app.services.deployment.worker import _run_deployment, enqueue_deployment_run
from app.services.telegram.client import TelegramNotifier
from app.utils.crypto import encrypt
from app.utils.system_config import get_system_config
from werkzeug.security import generate_password_hash


def _enable_notifications(app):
    with app.app_context():
        config = get_system_config()
        config.telegram_notifications_enabled = True
        config.encrypted_telegram_bot_token = encrypt("bot-token")
        db.session.commit()


def _patch_send_message(monkeypatch):
    sent = []
    monkeypatch.setattr(
        TelegramNotifier,
        "send_message",
        lambda self, chat_id, text, reply_markup=None: sent.append({"chat_id": chat_id, "text": text}),
    )
    return sent


def _make_linked_user(app):
    with app.app_context():
        role = Role(name="NotifyTestRole", description="test")
        db.session.add(role)
        db.session.flush()
        user = User(
            username="notify_test_user",
            password_hash=generate_password_hash("x"),
            is_active=True,
            role_id=role.id,
            telegram_chat_id="424242",
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def _make_build_entities(app):
    with app.app_context():
        git_source = GitSource(name="conn", provider_type="github", encrypted_token="x")
        db.session.add(git_source)
        db.session.flush()
        repository = Repository(
            git_source_id=git_source.id, full_name="owner/myapp", local_path="/tmp/myapp", status="ready"
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


def _make_queued_build(app, entities, requested_by):
    with app.app_context():
        version = Version.query.get(entities["version_id"])
        builder = Builder.query.get(entities["builder_id"])
        batch = enqueue_build_batch(
            version=version,
            bump_type="patch",
            builder_branches=[(builder, "main")],
            objects=[],
            additional_description=None,
            requested_by=requested_by,
        )
        return batch.id, ImageBuild.query.filter_by(batch_id=batch.id).first().id


def _make_deploy_entities(app):
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

        server = DeploymentServer(name="srv", connection_type="kube", encrypted_credentials=encrypt("fake-kubeconfig"))
        db.session.add(server)
        db.session.flush()
        manifest.target_servers = [server]
        db.session.commit()
        return {"manifest_id": manifest.id}


def _attach_to_workflow_step(app, *, batch_id=None, deployment_run_id=None):
    """Makes a BuildBatch/DeploymentRun look Workflow-driven, the same way
    app/services/workflow/worker.py._start_step's real WorkflowStepRun rows
    do — so notify_build_*/notify_deploy_*'s _is_workflow_driven_*() checks
    see it and skip notifying.
    """
    with app.app_context():
        workflow = Workflow(name="WF", is_active=True)
        db.session.add(workflow)
        db.session.flush()
        step = WorkflowStep(workflow_id=workflow.id, order=0, step_type="build" if batch_id else "deploy", on_failure="stop")
        db.session.add(step)
        db.session.flush()
        workflow_run = WorkflowRun(workflow_id=workflow.id, status="running", triggered_by=None)
        db.session.add(workflow_run)
        db.session.flush()
        step_run = WorkflowStepRun(
            workflow_run_id=workflow_run.id,
            workflow_step_id=step.id,
            step_order=0,
            step_type=step.step_type,
            status="running",
            batch_id=batch_id,
            deployment_run_id=deployment_run_id,
        )
        db.session.add(step_run)
        db.session.commit()


class TestBuildStartedNotification:
    def test_notifies_on_first_claim(self, app, monkeypatch):
        _enable_notifications(app)
        sent = _patch_send_message(monkeypatch)
        user_id = _make_linked_user(app)
        entities = _make_build_entities(app)
        _batch_id, _build_id = _make_queued_build(app, entities, requested_by=user_id)

        with app.app_context():
            build_claim_next_job()

        assert len(sent) == 1
        assert sent[0]["chat_id"] == "424242"
        assert "started" in sent[0]["text"]

    def test_skipped_when_no_requester(self, app, monkeypatch):
        _enable_notifications(app)
        sent = _patch_send_message(monkeypatch)
        entities = _make_build_entities(app)
        _make_queued_build(app, entities, requested_by=None)

        with app.app_context():
            build_claim_next_job()

        assert sent == []

    def test_skipped_for_a_workflow_driven_batch(self, app, monkeypatch):
        _enable_notifications(app)
        sent = _patch_send_message(monkeypatch)
        user_id = _make_linked_user(app)
        entities = _make_build_entities(app)
        batch_id, _build_id = _make_queued_build(app, entities, requested_by=user_id)
        _attach_to_workflow_step(app, batch_id=batch_id)

        with app.app_context():
            build_claim_next_job()

        assert sent == []


class TestBuildFinishedNotification:
    def test_notifies_once_on_reaching_a_terminal_status(self, app, monkeypatch):
        _enable_notifications(app)
        sent = _patch_send_message(monkeypatch)
        user_id = _make_linked_user(app)
        entities = _make_build_entities(app)
        batch_id, build_id = _make_queued_build(app, entities, requested_by=user_id)

        with app.app_context():
            build_claim_next_job()
            build = ImageBuild.query.get(build_id)
            build.status = "success"
            db.session.commit()
            _update_batch_status(batch_id)
            # A second call (e.g. from another finishing image) must not
            # re-notify — the batch was already terminal going in.
            _update_batch_status(batch_id)

        assert len(sent) == 2  # one "started", one "finished"
        assert "succeeded" in sent[-1]["text"]

    def test_skipped_for_a_workflow_driven_batch(self, app, monkeypatch):
        _enable_notifications(app)
        sent = _patch_send_message(monkeypatch)
        user_id = _make_linked_user(app)
        entities = _make_build_entities(app)
        batch_id, build_id = _make_queued_build(app, entities, requested_by=user_id)
        _attach_to_workflow_step(app, batch_id=batch_id)

        with app.app_context():
            build_claim_next_job()
            build = ImageBuild.query.get(build_id)
            build.status = "success"
            db.session.commit()
            _update_batch_status(batch_id)

        assert sent == []


class TestDeployStartedNotification:
    def test_notifies_on_first_claim(self, app, monkeypatch):
        _enable_notifications(app)
        sent = _patch_send_message(monkeypatch)
        user_id = _make_linked_user(app)
        entities = _make_deploy_entities(app)

        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            enqueue_deployment_run(manifests=[manifest], triggered_by=user_id)
            deploy_claim_next_job()

        assert len(sent) == 1
        assert sent[0]["chat_id"] == "424242"
        assert "started" in sent[0]["text"]
        assert "m1" in sent[0]["text"]

    def test_skipped_for_a_workflow_driven_run(self, app, monkeypatch):
        _enable_notifications(app)
        sent = _patch_send_message(monkeypatch)
        user_id = _make_linked_user(app)
        entities = _make_deploy_entities(app)

        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=user_id)
            run_id = run.id

        _attach_to_workflow_step(app, deployment_run_id=run_id)

        with app.app_context():
            deploy_claim_next_job()

        assert sent == []


class TestDeployFinishedNotification:
    def test_notifies_once_on_reaching_a_terminal_status(self, app, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider, "apply", lambda self, yaml, wait_timeout_seconds=None: DeployResult(success=True, log="ok")
        )
        _enable_notifications(app)
        sent = _patch_send_message(monkeypatch)
        user_id = _make_linked_user(app)
        entities = _make_deploy_entities(app)

        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=user_id)
            run_id = run.id
            execution_id = DeploymentExecution.query.filter_by(run_id=run.id).first().id
            deploy_claim_next_job()

        _run_deployment(app, execution_id)

        with app.app_context():
            run = DeploymentRun.query.get(run_id)
            assert run.status == "success"

        assert len(sent) == 2  # one "started", one "finished"
        assert "succeeded" in sent[-1]["text"]

    def test_skipped_for_a_workflow_driven_run(self, app, monkeypatch):
        monkeypatch.setattr(
            KubernetesProvider, "apply", lambda self, yaml, wait_timeout_seconds=None: DeployResult(success=True, log="ok")
        )
        _enable_notifications(app)
        sent = _patch_send_message(monkeypatch)
        user_id = _make_linked_user(app)
        entities = _make_deploy_entities(app)

        with app.app_context():
            manifest = DeploymentManifest.query.get(entities["manifest_id"])
            run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=user_id)
            run_id = run.id
            execution_id = DeploymentExecution.query.filter_by(run_id=run.id).first().id

        _attach_to_workflow_step(app, deployment_run_id=run_id)

        _run_deployment(app, execution_id)

        assert sent == []
