from app.extensions import db
from app.models import (
    Builder,
    BuildBatch,
    DeploymentManifest,
    DeploymentManifestVersionBinding,
    GitSource,
    ImageBuild,
    Repository,
    RegistryTarget,
    Version,
    VersionType,
)
from app.services.deployment.resolver import (
    UnresolvedPlaceholderError,
    find_placeholder_keys,
    resolve_manifest,
)


def _make_builder(app):
    with app.app_context():
        git_source = GitSource(name="gs", provider_type="github", encrypted_token="x")
        db.session.add(git_source)
        db.session.flush()
        repository = Repository(
            git_source_id=git_source.id, full_name="org/app", local_path="/tmp/app", status="ready"
        )
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
            name="b1",
            version_id=version.id,
            repository_id=repository.id,
            default_branch="main",
            registry_target_id=registry_target.id,
        )
        db.session.add(builder)
        db.session.commit()
        return builder.id


def _make_successful_build(app, builder_id, tag, batch_status="success", build_status="success"):
    with app.app_context():
        builder = Builder.query.get(builder_id)
        batch = BuildBatch(
            version_id=builder.version_id, bump_type="patch", status=batch_status, full_version_string="DEV.0.0.1.x"
        )
        db.session.add(batch)
        db.session.flush()
        build = ImageBuild(batch_id=batch.id, builder_id=builder_id, branch_used="main", status=build_status, image_tag=tag)
        db.session.add(build)
        db.session.commit()
        return build.id


def _make_manifest(app, yaml_content, bindings):
    with app.app_context():
        manifest = DeploymentManifest(name="m1", yaml_content=yaml_content)
        db.session.add(manifest)
        db.session.flush()
        for binding in bindings:
            db.session.add(DeploymentManifestVersionBinding(manifest_id=manifest.id, **binding))
        db.session.commit()
        return manifest.id


class TestFindPlaceholderKeys:
    def test_default_and_keyed_placeholders_deduped_in_order(self):
        yaml_content = "a: {{SYS:VERSION}}\nb: {{SYS:VERSION:worker}}\nc: {{SYS:VERSION}}"
        assert find_placeholder_keys(yaml_content) == ["default", "worker"]

    def test_no_placeholders(self):
        assert find_placeholder_keys("image: nginx:latest") == []


class TestResolveManifest:
    def test_resolves_default_placeholder_to_latest_successful_build(self, app):
        builder_id = _make_builder(app)
        _make_successful_build(app, builder_id, "u/app:DEV.0.0.1.x")
        manifest_id = _make_manifest(
            app, "image: {{SYS:VERSION}}", [{"placeholder_key": "default", "builder_id": builder_id}]
        )

        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            rendered, resolved = resolve_manifest(manifest)

        assert rendered == "image: u/app:DEV.0.0.1.x"
        assert resolved == {"default": "u/app:DEV.0.0.1.x"}

    def test_uses_the_latest_of_several_successful_builds(self, app):
        builder_id = _make_builder(app)
        _make_successful_build(app, builder_id, "u/app:DEV.0.0.1.x")
        _make_successful_build(app, builder_id, "u/app:DEV.0.0.2.x")
        manifest_id = _make_manifest(
            app, "image: {{SYS:VERSION}}", [{"placeholder_key": "default", "builder_id": builder_id}]
        )

        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            rendered, resolved = resolve_manifest(manifest)

        assert resolved == {"default": "u/app:DEV.0.0.2.x"}
        assert "DEV.0.0.2.x" in rendered

    def test_pinned_build_overrides_latest(self, app):
        builder_id = _make_builder(app)
        pinned_id = _make_successful_build(app, builder_id, "u/app:DEV.0.0.1.x")
        _make_successful_build(app, builder_id, "u/app:DEV.0.0.2.x")
        manifest_id = _make_manifest(
            app,
            "image: {{SYS:VERSION}}",
            [{"placeholder_key": "default", "builder_id": builder_id, "pinned_image_build_id": pinned_id}],
        )

        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            rendered, resolved = resolve_manifest(manifest)

        assert resolved == {"default": "u/app:DEV.0.0.1.x"}

    def test_multiple_keyed_placeholders_resolve_independently(self, app):
        builder_id = _make_builder(app)
        _make_successful_build(app, builder_id, "u/app:DEV.0.0.1.x")

        with app.app_context():
            git_source = GitSource(name="gs2", provider_type="github", encrypted_token="x")
            db.session.add(git_source)
            db.session.flush()
            repository = Repository(
                git_source_id=git_source.id, full_name="org/worker", local_path="/tmp/worker", status="ready"
            )
            db.session.add(repository)
            registry_target = RegistryTarget(name="reg2", provider_type="dockerhub", username="u")
            db.session.add(registry_target)
            builder = Builder.query.get(builder_id)
            worker_builder = Builder(
                name="worker",
                version_id=builder.version_id,
                repository_id=repository.id,
                default_branch="main",
                registry_target_id=registry_target.id,
            )
            db.session.add(worker_builder)
            db.session.commit()
            worker_builder_id = worker_builder.id
        _make_successful_build(app, worker_builder_id, "u/worker:DEV.0.0.1.x")

        manifest_id = _make_manifest(
            app,
            "app: {{SYS:VERSION}}\nworker: {{SYS:VERSION:worker}}",
            [
                {"placeholder_key": "default", "builder_id": builder_id},
                {"placeholder_key": "worker", "builder_id": worker_builder_id},
            ],
        )

        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            rendered, resolved = resolve_manifest(manifest)

        assert resolved == {"default": "u/app:DEV.0.0.1.x", "worker": "u/worker:DEV.0.0.1.x"}
        assert "u/app:DEV.0.0.1.x" in rendered and "u/worker:DEV.0.0.1.x" in rendered

    def test_unbound_placeholder_raises(self, app):
        manifest_id = _make_manifest(app, "image: {{SYS:VERSION:unbound}}", [])

        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            try:
                resolve_manifest(manifest)
                assert False, "expected UnresolvedPlaceholderError"
            except UnresolvedPlaceholderError as exc:
                assert "unbound" in str(exc)

    def test_builder_with_no_successful_build_raises(self, app):
        builder_id = _make_builder(app)
        manifest_id = _make_manifest(
            app, "image: {{SYS:VERSION}}", [{"placeholder_key": "default", "builder_id": builder_id}]
        )

        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            try:
                resolve_manifest(manifest)
                assert False, "expected UnresolvedPlaceholderError"
            except UnresolvedPlaceholderError:
                pass

    def test_only_successful_builds_are_considered(self, app):
        builder_id = _make_builder(app)
        _make_successful_build(app, builder_id, "u/app:DEV.0.0.2.x", build_status="failed")
        _make_successful_build(app, builder_id, "u/app:DEV.0.0.1.x", build_status="success")
        manifest_id = _make_manifest(
            app, "image: {{SYS:VERSION}}", [{"placeholder_key": "default", "builder_id": builder_id}]
        )

        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            _rendered, resolved = resolve_manifest(manifest)

        assert resolved == {"default": "u/app:DEV.0.0.1.x"}
