"""Unit tests for app.services.workflow.resolver — resolving a WorkflowStep's
group_name selections + individually selected items into a concrete, ordered
list of Builders/DeploymentManifests, without touching any routes or the
orchestrator.
"""
import pytest

from app.extensions import db
from app.models import (
    Builder,
    DeploymentManifest,
    GitSource,
    RegistryTarget,
    Repository,
    Version,
    VersionType,
    Workflow,
    WorkflowStep,
    WorkflowStepGroup,
)
from app.services.workflow.resolver import resolve_step_builders, resolve_step_manifests


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
        db.session.commit()
        return {"repository_id": repository.id, "registry_target_id": registry_target.id, "version_id": version.id}


def _make_builder(base_entities, name, group_name=None):
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


def _make_manifest(name, group_name=None, order=0):
    manifest = DeploymentManifest(name=name, yaml_content="kind: Pod", group_name=group_name, order=order)
    db.session.add(manifest)
    db.session.flush()
    return manifest


def _make_workflow_step(step_type="build"):
    workflow = Workflow(name="wf")
    db.session.add(workflow)
    db.session.flush()
    step = WorkflowStep(workflow_id=workflow.id, order=0, step_type=step_type, on_failure="stop")
    db.session.add(step)
    db.session.flush()
    return step


class TestResolveStepBuilders:
    def test_group_selection_resolves_to_current_group_members(self, app, base_entities):
        with app.app_context():
            _make_builder(base_entities, "a", group_name="prod")
            _make_builder(base_entities, "b", group_name="prod")
            _make_builder(base_entities, "c", group_name="staging")

            step = _make_workflow_step("build")
            db.session.add(WorkflowStepGroup(workflow_step_id=step.id, group_name="prod"))
            db.session.commit()

            resolved = resolve_step_builders(step)
            assert [b.name for b in resolved] == ["a", "b"]

    def test_individually_selected_ungrouped_items_are_included(self, app, base_entities):
        with app.app_context():
            standalone = _make_builder(base_entities, "solo")

            step = _make_workflow_step("build")
            step.selected_builders = [standalone]
            db.session.commit()

            resolved = resolve_step_builders(step)
            assert [b.name for b in resolved] == ["solo"]

    def test_group_plus_items_combine_without_duplicates(self, app, base_entities):
        with app.app_context():
            _make_builder(base_entities, "a", group_name="prod")
            solo = _make_builder(base_entities, "solo")

            step = _make_workflow_step("build")
            db.session.add(WorkflowStepGroup(workflow_step_id=step.id, group_name="prod"))
            step.selected_builders = [solo]
            db.session.commit()

            resolved = resolve_step_builders(step)
            assert sorted(b.name for b in resolved) == ["a", "solo"]

    def test_group_membership_is_resolved_live_not_frozen(self, app, base_entities):
        """A builder added to the group AFTER the step was created still
        shows up on the next resolve — group selection is a live reference,
        not a snapshot (see WorkflowStepGroup's docstring)."""
        with app.app_context():
            _make_builder(base_entities, "a", group_name="prod")

            step = _make_workflow_step("build")
            db.session.add(WorkflowStepGroup(workflow_step_id=step.id, group_name="prod"))
            db.session.commit()

            assert [b.name for b in resolve_step_builders(step)] == ["a"]

            _make_builder(base_entities, "b", group_name="prod")
            db.session.commit()

            assert [b.name for b in resolve_step_builders(step)] == ["a", "b"]

    def test_no_selection_resolves_to_empty(self, app, base_entities):
        with app.app_context():
            step = _make_workflow_step("build")
            db.session.commit()
            assert resolve_step_builders(step) == []


class TestResolveStepManifests:
    def test_group_selection_is_ordered_by_manifest_order(self, app):
        with app.app_context():
            _make_manifest("second", group_name="prod", order=1)
            _make_manifest("first", group_name="prod", order=0)

            step = _make_workflow_step("deploy")
            db.session.add(WorkflowStepGroup(workflow_step_id=step.id, group_name="prod"))
            db.session.commit()

            resolved = resolve_step_manifests(step)
            assert [m.name for m in resolved] == ["first", "second"]

    def test_group_plus_items_combine_without_duplicates(self, app):
        with app.app_context():
            _make_manifest("a", group_name="prod", order=0)
            solo = _make_manifest("solo")

            step = _make_workflow_step("deploy")
            db.session.add(WorkflowStepGroup(workflow_step_id=step.id, group_name="prod"))
            step.selected_manifests = [solo]
            db.session.commit()

            resolved = resolve_step_manifests(step)
            assert sorted(m.name for m in resolved) == ["a", "solo"]
