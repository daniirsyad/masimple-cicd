import pytest
from werkzeug.security import generate_password_hash

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
    Permission,
    RegistryTarget,
    Repository,
    Role,
    User,
    Version,
    VersionType,
)

MANIFEST_PASSWORD = "ManifestPass123!"


@pytest.fixture
def manifest_user(app):
    with app.app_context():
        permissions = [
            Permission(code="deployment_manifest.view", description="view"),
            Permission(code="deployment_manifest.manage", description="manage"),
            Permission(code="deployment.deploy", description="deploy"),
            Permission(code="deployment.update", description="update"),
            Permission(code="deployment.stop", description="stop"),
            Permission(code="deployment.restart", description="restart"),
            Permission(code="deployment_run.view", description="view runs"),
        ]
        db.session.add_all(permissions)
        role = Role(name="ManifestAdmin", description="Test manifest admin role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="manifest_test",
            password_hash=generate_password_hash(MANIFEST_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def manifest_client(client, manifest_user):
    client.post("/login", data={"username": "manifest_test", "password": MANIFEST_PASSWORD}, follow_redirects=True)
    return client


@pytest.fixture
def base_entities(app):
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
        server = DeploymentServer(name="srv1", connection_type="kube")
        db.session.add(server)
        db.session.commit()
        return {"builder_id": builder.id, "server_id": server.id, "build_id": build.id}


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/deployment-manifests/").status_code == 403

    def test_create_requires_permission(self, noperm_client):
        assert noperm_client.post("/deployment-manifests/create", data={}).status_code == 403

    def test_deploy_requires_permission(self, noperm_client):
        assert noperm_client.post("/deployment-manifests/deploy", data={}).status_code == 403


class TestDeployStopButtonVisibility:
    """Deploy hides once a manifest is live on every one of its target
    servers; Stop shows as soon as it's live on at least one — both can be
    visible at once for a manifest that's only partially deployed.
    """

    def test_never_deployed_shows_only_deploy(self, manifest_client, app, base_entities):
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [DeploymentServer.query.get(base_entities["server_id"])]
            db.session.add(manifest)
            db.session.commit()

        response = manifest_client.get("/deployment-manifests/")
        assert b'class="deploy-one-btn' in response.data
        assert b'class="stop-one-btn' not in response.data

    def test_fully_deployed_shows_only_stop(self, manifest_client, app, base_entities):
        with app.app_context():
            server = DeploymentServer.query.get(base_entities["server_id"])
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [server]
            db.session.add(manifest)
            db.session.flush()

            deploy_run = DeploymentRun(action="deploy", status="success")
            db.session.add(deploy_run)
            db.session.flush()
            db.session.add(
                DeploymentExecution(
                    run_id=deploy_run.id, manifest_id=manifest.id, server_id=server.id,
                    status="success", rendered_yaml="image: nginx",
                )
            )
            db.session.commit()

        response = manifest_client.get("/deployment-manifests/")
        assert b'class="deploy-one-btn' not in response.data
        assert b'class="stop-one-btn' in response.data

    def test_partially_deployed_shows_both(self, manifest_client, app, base_entities):
        with app.app_context():
            live_server = DeploymentServer.query.get(base_entities["server_id"])
            not_live_server = DeploymentServer(name="srv2", connection_type="kube")
            db.session.add(not_live_server)
            db.session.flush()

            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [live_server, not_live_server]
            db.session.add(manifest)
            db.session.flush()

            deploy_run = DeploymentRun(action="deploy", status="success")
            db.session.add(deploy_run)
            db.session.flush()
            db.session.add(
                DeploymentExecution(
                    run_id=deploy_run.id, manifest_id=manifest.id, server_id=live_server.id,
                    status="success", rendered_yaml="image: nginx",
                )
            )
            db.session.commit()

        response = manifest_client.get("/deployment-manifests/")
        assert b'class="deploy-one-btn' in response.data
        assert b'class="stop-one-btn' in response.data


class TestCreateManifest:
    def test_creates_manifest_with_binding_and_target_server(self, manifest_client, app, base_entities):
        response = manifest_client.post(
            "/deployment-manifests/create",
            data={
                "create-manifest-name": "m1",
                "create-manifest-yaml_content": "image: {{SYS:VERSION}}",
                "create-manifest-group_name": "grp1",
                "create-manifest-server_ids": [str(base_entities["server_id"])],
                "binding_key": ["default"],
                "binding_builder_id": [str(base_entities["builder_id"])],
                "binding_pinned_build_id": [""],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            manifest = DeploymentManifest.query.filter_by(name="m1").first()
            assert manifest is not None
            assert manifest.group_name == "grp1"
            assert len(manifest.target_servers) == 1
            bindings = manifest.version_bindings.all()
            assert len(bindings) == 1
            assert bindings[0].placeholder_key == "default"
            assert bindings[0].builder_id == base_entities["builder_id"]

    def test_incomplete_binding_rows_are_dropped(self, manifest_client, app, base_entities):
        manifest_client.post(
            "/deployment-manifests/create",
            data={
                "create-manifest-name": "m2",
                "create-manifest-yaml_content": "image: {{SYS:VERSION}}",
                "create-manifest-server_ids": [str(base_entities["server_id"])],
                "binding_key": ["default", ""],
                "binding_builder_id": [str(base_entities["builder_id"]), ""],
                "binding_pinned_build_id": ["", ""],
            },
            follow_redirects=True,
        )
        with app.app_context():
            manifest = DeploymentManifest.query.filter_by(name="m2").first()
            assert manifest.version_bindings.count() == 1

    def test_missing_target_servers_is_rejected(self, manifest_client, app):
        response = manifest_client.post(
            "/deployment-manifests/create",
            data={"create-manifest-name": "m3", "create-manifest-yaml_content": "image: nginx"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Select at least one Target Server" in response.data
        with app.app_context():
            assert DeploymentManifest.query.filter_by(name="m3").first() is None


class TestEditManifest:
    def test_edit_replaces_bindings(self, manifest_client, app, base_entities):
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: {{SYS:VERSION}}")
            db.session.add(manifest)
            db.session.flush()
            db.session.add(
                DeploymentManifestVersionBinding(
                    manifest_id=manifest.id, placeholder_key="default", builder_id=base_entities["builder_id"]
                )
            )
            db.session.commit()
            manifest_id = manifest.id

        manifest_client.post(
            f"/deployment-manifests/{manifest_id}/edit",
            data={
                f"manifest-{manifest_id}-name": "m1-renamed",
                f"manifest-{manifest_id}-yaml_content": "image: {{SYS:VERSION:other}}",
                f"manifest-{manifest_id}-server_ids": [str(base_entities["server_id"])],
                "binding_key": ["other"],
                "binding_builder_id": [str(base_entities["builder_id"])],
                "binding_pinned_build_id": [""],
            },
        )

        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            assert manifest.name == "m1-renamed"
            bindings = manifest.version_bindings.all()
            assert len(bindings) == 1
            assert bindings[0].placeholder_key == "other"

    def test_edit_without_target_servers_is_rejected(self, manifest_client, app, base_entities):
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [DeploymentServer.query.get(base_entities["server_id"])]
            db.session.add(manifest)
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post(
            f"/deployment-manifests/{manifest_id}/edit",
            data={
                f"manifest-{manifest_id}-name": "m1",
                f"manifest-{manifest_id}-yaml_content": "image: nginx",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Select at least one Target Server" in response.data
        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            assert len(manifest.target_servers) == 1  # unchanged


class TestDeleteManifest:
    def test_deletes_when_unreferenced(self, manifest_client, app):
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            db.session.add(manifest)
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post(f"/deployment-manifests/{manifest_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert DeploymentManifest.query.get(manifest_id) is None

    def test_blocked_when_still_deployed(self, manifest_client, app, base_entities):
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            db.session.add(manifest)
            db.session.flush()
            run = DeploymentRun(status="success", action="deploy")
            db.session.add(run)
            db.session.flush()
            db.session.add(
                DeploymentExecution(
                    run_id=run.id, manifest_id=manifest.id, server_id=base_entities["server_id"], status="success"
                )
            )
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post(f"/deployment-manifests/{manifest_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        assert b"still deployed on" in response.data
        with app.app_context():
            assert DeploymentManifest.query.get(manifest_id) is not None

    def test_blocked_when_execution_in_progress(self, manifest_client, app, base_entities):
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            db.session.add(manifest)
            db.session.flush()
            run = DeploymentRun(status="running", action="deploy")
            db.session.add(run)
            db.session.flush()
            db.session.add(
                DeploymentExecution(
                    run_id=run.id, manifest_id=manifest.id, server_id=base_entities["server_id"], status="running"
                )
            )
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post(f"/deployment-manifests/{manifest_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        assert b"currently in progress" in response.data
        with app.app_context():
            assert DeploymentManifest.query.get(manifest_id) is not None

    def test_allowed_after_stop_and_history_survives_with_null_manifest(self, manifest_client, app, base_entities):
        """Once a manifest is fully stopped (no longer currently deployed
        anywhere), it should be deletable even with recorded history — the
        DeploymentRun/DeploymentExecution rows survive with manifest_id
        nulled out rather than blocking the delete or being cascaded away.
        """
        with app.app_context():
            server_id = base_entities["server_id"]
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [DeploymentServer.query.get(server_id)]
            db.session.add(manifest)
            db.session.flush()

            deploy_run = DeploymentRun(status="success", action="deploy")
            db.session.add(deploy_run)
            db.session.flush()
            deploy_execution = DeploymentExecution(
                run_id=deploy_run.id, manifest_id=manifest.id, server_id=server_id,
                status="success", rendered_yaml="image: nginx",
            )
            db.session.add(deploy_execution)
            db.session.flush()

            stop_run = DeploymentRun(status="success", action="stop")
            db.session.add(stop_run)
            db.session.flush()
            db.session.add(
                DeploymentExecution(
                    run_id=stop_run.id, manifest_id=manifest.id, server_id=server_id,
                    status="success", source_execution_id=deploy_execution.id,
                )
            )
            db.session.commit()
            manifest_id = manifest.id
            deploy_run_id, stop_run_id = deploy_run.id, stop_run.id

        response = manifest_client.post(f"/deployment-manifests/{manifest_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        assert response.request.path == "/deployment-manifests/"

        with app.app_context():
            assert DeploymentManifest.query.get(manifest_id) is None
            # Both runs (and their executions) still exist for audit —
            # detached, not deleted.
            for run_id in (deploy_run_id, stop_run_id):
                assert DeploymentRun.query.get(run_id) is not None
                execution = DeploymentExecution.query.filter_by(run_id=run_id).first()
                assert execution is not None
                assert execution.manifest_id is None

    def test_deletes_manifest_with_version_bindings(self, manifest_client, app, base_entities):
        """A manifest with configured version bindings used to fail on the
        FK (deployment_manifest_version_bindings.manifest_id, NOT NULL, no
        cascade) since nothing cleaned them up before the delete.
        """
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: {{SYS:VERSION}}")
            db.session.add(manifest)
            db.session.flush()
            db.session.add(
                DeploymentManifestVersionBinding(
                    manifest_id=manifest.id, placeholder_key="default", builder_id=base_entities["builder_id"]
                )
            )
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post(f"/deployment-manifests/{manifest_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert DeploymentManifest.query.get(manifest_id) is None
            assert DeploymentManifestVersionBinding.query.filter_by(manifest_id=manifest_id).count() == 0


class TestPlaceholderKeysEndpoint:
    def test_scans_yaml_for_placeholder_keys(self, manifest_client):
        response = manifest_client.post(
            "/deployment-manifests/api/placeholder-keys",
            data={"yaml_content": "a: {{SYS:VERSION}}\nb: {{SYS:VERSION:worker}}"},
        )
        assert response.status_code == 200
        assert response.get_json()["keys"] == ["default", "worker"]


class TestPreviewEndpoint:
    def test_previews_resolved_versions_and_servers(self, manifest_client, app, base_entities):
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: {{SYS:VERSION}}")
            manifest.target_servers = [DeploymentServer.query.get(base_entities["server_id"])]
            db.session.add(manifest)
            db.session.flush()
            db.session.add(
                DeploymentManifestVersionBinding(
                    manifest_id=manifest.id, placeholder_key="default", builder_id=base_entities["builder_id"]
                )
            )
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post("/deployment-manifests/api/preview", data={"manifest_ids": [str(manifest_id)]})
        assert response.status_code == 200
        previews = response.get_json()["previews"]
        assert len(previews) == 1
        assert previews[0]["error"] is None
        assert previews[0]["resolved_versions"] == {"default": "u/app:DEV.0.0.1.x"}
        assert previews[0]["servers"] == ["srv1"]

    def test_previews_report_unresolved_placeholder_errors(self, manifest_client, app):
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: {{SYS:VERSION:missing}}")
            db.session.add(manifest)
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post("/deployment-manifests/api/preview", data={"manifest_ids": [str(manifest_id)]})
        previews = response.get_json()["previews"]
        assert previews[0]["error"] is not None


class TestDeployTrigger:
    def test_deploy_blocked_when_role_not_in_server_allowed_roles(self, manifest_client, app, base_entities):
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: {{SYS:VERSION}}")
            manifest.target_servers = [DeploymentServer.query.get(base_entities["server_id"])]
            db.session.add(manifest)
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post("/deployment-manifests/deploy", data={"manifest_ids": [str(manifest_id)]})
        assert response.status_code == 403

    def test_deploy_requires_at_least_one_manifest(self, manifest_client):
        response = manifest_client.post("/deployment-manifests/deploy", data={}, follow_redirects=True)
        assert response.status_code == 200
        assert b"Select at least one" in response.data

    def test_deploy_requires_target_servers(self, manifest_client, app):
        with app.app_context():
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            db.session.add(manifest)
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post(
            "/deployment-manifests/deploy", data={"manifest_ids": [str(manifest_id)]}, follow_redirects=True
        )
        assert response.status_code == 200
        assert b"target servers configured" in response.data

    def test_deploy_enqueues_a_run_and_redirects_to_runs(self, manifest_client, app, base_entities):
        with app.app_context():
            # deployment.deploy alone isn't enough to deploy to a given
            # server — the triggering user's role must also be granted via
            # DeploymentServer.allowed_roles (or hold deployment_server.manage),
            # mirroring Builder.allowed_roles's access gate.
            server = DeploymentServer.query.get(base_entities["server_id"])
            server.allowed_roles = [Role.query.filter_by(name="ManifestAdmin").first()]

            manifest = DeploymentManifest(name="m1", yaml_content="image: {{SYS:VERSION}}", group_name="grp1")
            manifest.target_servers = [server]
            db.session.add(manifest)
            db.session.flush()
            db.session.add(
                DeploymentManifestVersionBinding(
                    manifest_id=manifest.id, placeholder_key="default", builder_id=base_entities["builder_id"]
                )
            )
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post(
            "/deployment-manifests/deploy", data={"manifest_ids": [str(manifest_id)]}, follow_redirects=True
        )
        assert response.status_code == 200
        assert response.request.path == "/deployment-runs/"

        with app.app_context():
            run = DeploymentRun.query.first()
            assert run is not None
            assert run.group_name == "grp1"
            executions = DeploymentExecution.query.filter_by(run_id=run.id).all()
            assert len(executions) == 1
            assert executions[0].manifest_id == manifest_id
            assert executions[0].server_id == base_entities["server_id"]


class TestReorderManifests:
    def test_requires_permission(self, noperm_client):
        response = noperm_client.post(
            "/deployment-manifests/reorder", json={"group_name": "grp", "manifest_ids": []}
        )
        assert response.status_code == 403

    def test_persists_new_order(self, manifest_client, app):
        with app.app_context():
            m1 = DeploymentManifest(name="a", yaml_content="x", group_name="grp1")
            m2 = DeploymentManifest(name="b", yaml_content="x", group_name="grp1")
            db.session.add_all([m1, m2])
            db.session.commit()
            m1_id, m2_id = m1.id, m2.id

        response = manifest_client.post(
            "/deployment-manifests/reorder",
            json={"group_name": "grp1", "manifest_ids": [str(m2_id), str(m1_id)]},
        )
        assert response.status_code == 200

        with app.app_context():
            assert DeploymentManifest.query.get(m2_id).order == 0
            assert DeploymentManifest.query.get(m1_id).order == 1

    def test_rejects_manifest_from_a_different_group(self, manifest_client, app):
        with app.app_context():
            m1 = DeploymentManifest(name="a", yaml_content="x", group_name="grp1")
            m2 = DeploymentManifest(name="b", yaml_content="x", group_name="grp2")
            db.session.add_all([m1, m2])
            db.session.commit()
            m1_id, m2_id = m1.id, m2.id

        response = manifest_client.post(
            "/deployment-manifests/reorder",
            json={"group_name": "grp1", "manifest_ids": [str(m1_id), str(m2_id)]},
        )
        assert response.status_code == 400


class TestStopTrigger:
    def test_requires_permission(self, noperm_client):
        assert noperm_client.post("/deployment-manifests/stop", data={}).status_code == 403

    def test_stop_requires_at_least_one_manifest(self, manifest_client):
        response = manifest_client.post("/deployment-manifests/stop", data={}, follow_redirects=True)
        assert response.status_code == 200
        assert b"Select at least one" in response.data

    def test_stop_with_nothing_deployed_flashes_info_and_creates_no_run(self, manifest_client, app, base_entities):
        with app.app_context():
            server = DeploymentServer.query.get(base_entities["server_id"])
            server.allowed_roles = [Role.query.filter_by(name="ManifestAdmin").first()]
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [server]
            db.session.add(manifest)
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post(
            "/deployment-manifests/stop", data={"manifest_ids": [str(manifest_id)]}, follow_redirects=True
        )
        assert response.status_code == 200
        assert b"Nothing currently deployed" in response.data
        with app.app_context():
            assert DeploymentRun.query.count() == 0

    def test_stop_tears_down_live_pairs_in_descending_group_order(self, manifest_client, app, base_entities):
        with app.app_context():
            server = DeploymentServer.query.get(base_entities["server_id"])
            server.allowed_roles = [Role.query.filter_by(name="ManifestAdmin").first()]

            m1 = DeploymentManifest(name="a", yaml_content="image: nginx", group_name="grp1", order=0)
            m2 = DeploymentManifest(name="b", yaml_content="image: nginx", group_name="grp1", order=1)
            m1.target_servers = [server]
            m2.target_servers = [server]
            db.session.add_all([m1, m2])
            db.session.commit()

            # Simulate both already successfully deployed.
            deploy_run = DeploymentRun(action="deploy", status="success")
            db.session.add(deploy_run)
            db.session.flush()
            exec1 = DeploymentExecution(
                run_id=deploy_run.id, manifest_id=m1.id, server_id=server.id, status="success", rendered_yaml="image: nginx"
            )
            exec2 = DeploymentExecution(
                run_id=deploy_run.id, manifest_id=m2.id, server_id=server.id, status="success", rendered_yaml="image: nginx"
            )
            db.session.add_all([exec1, exec2])
            db.session.commit()
            m1_id, m2_id, exec1_id, exec2_id = m1.id, m2.id, exec1.id, exec2.id

        # Submitted in ascending (deploy) order — the route must still stop
        # them in descending order regardless of what the client sent.
        response = manifest_client.post(
            "/deployment-manifests/stop",
            data={"manifest_ids": [str(m1_id), str(m2_id)]},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert response.request.path == "/deployment-runs/"

        with app.app_context():
            stop_run = DeploymentRun.query.filter_by(action="stop").first()
            assert stop_run is not None
            executions = (
                DeploymentExecution.query.filter_by(run_id=stop_run.id)
                .order_by(DeploymentExecution.created_at.asc())
                .all()
            )
            assert len(executions) == 2
            assert executions[0].manifest_id == m2_id
            assert executions[0].source_execution_id == exec2_id
            assert executions[1].manifest_id == m1_id
            assert executions[1].source_execution_id == exec1_id


class TestGranularPermissions:
    """deployment.trigger was split into deployment.deploy/update/stop/
    restart — a role holding only one must be able to do that one action
    and get 403 on the other three.
    """

    ACTIONS = ("deploy", "update", "stop", "restart")

    def _make_single_permission_client(self, app, client, action):
        with app.app_context():
            permission = Permission(code=f"deployment.{action}", description=action)
            db.session.add(permission)
            role = Role(name=f"{action.capitalize()}Only", description=f"Can only {action}")
            role.permissions = [permission]
            db.session.add(role)
            db.session.flush()

            username = f"{action}_only_test"
            user = User(
                username=username, password_hash=generate_password_hash(MANIFEST_PASSWORD), is_active=True, role_id=role.id
            )
            db.session.add(user)
            db.session.commit()

        client.post("/login", data={"username": username, "password": MANIFEST_PASSWORD}, follow_redirects=True)
        return client

    @pytest.mark.parametrize("action", ACTIONS)
    def test_holding_only_this_permission_allows_only_this_route(self, action, app, client):
        scoped_client = self._make_single_permission_client(app, client, action)
        for candidate in self.ACTIONS:
            response = scoped_client.post(f"/deployment-manifests/{candidate}", data={})
            if candidate == action:
                assert response.status_code != 403, f"{action}-only role should be able to POST /{action}"
            else:
                assert response.status_code == 403, f"{action}-only role should NOT be able to POST /{candidate}"


class TestUpdateTrigger:
    def test_requires_permission(self, noperm_client):
        assert noperm_client.post("/deployment-manifests/update", data={}).status_code == 403

    def test_update_requires_at_least_one_manifest(self, manifest_client):
        response = manifest_client.post("/deployment-manifests/update", data={}, follow_redirects=True)
        assert response.status_code == 200
        assert b"Select at least one" in response.data

    def test_update_enqueues_a_deploy_action_run(self, manifest_client, app, base_entities):
        with app.app_context():
            server = DeploymentServer.query.get(base_entities["server_id"])
            server.allowed_roles = [Role.query.filter_by(name="ManifestAdmin").first()]

            manifest = DeploymentManifest(name="m1", yaml_content="image: {{SYS:VERSION}}")
            manifest.target_servers = [server]
            db.session.add(manifest)
            db.session.flush()
            db.session.add(
                DeploymentManifestVersionBinding(
                    manifest_id=manifest.id, placeholder_key="default", builder_id=base_entities["builder_id"]
                )
            )
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post(
            "/deployment-manifests/update", data={"manifest_ids": [str(manifest_id)]}, follow_redirects=True
        )
        assert response.status_code == 200
        assert response.request.path == "/deployment-runs/"

        with app.app_context():
            run = DeploymentRun.query.first()
            assert run is not None
            assert run.action == "deploy"  # update IS a deploy, just triggered under a different permission


class TestRestartTrigger:
    def test_requires_permission(self, noperm_client):
        assert noperm_client.post("/deployment-manifests/restart", data={}).status_code == 403

    def test_restart_requires_at_least_one_manifest(self, manifest_client):
        response = manifest_client.post("/deployment-manifests/restart", data={}, follow_redirects=True)
        assert response.status_code == 200
        assert b"Select at least one" in response.data

    def test_restart_with_nothing_deployed_flashes_info_and_creates_no_run(self, manifest_client, app, base_entities):
        with app.app_context():
            server = DeploymentServer.query.get(base_entities["server_id"])
            server.allowed_roles = [Role.query.filter_by(name="ManifestAdmin").first()]
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [server]
            db.session.add(manifest)
            db.session.commit()
            manifest_id = manifest.id

        response = manifest_client.post(
            "/deployment-manifests/restart", data={"manifest_ids": [str(manifest_id)]}, follow_redirects=True
        )
        assert response.status_code == 200
        assert b"Nothing currently deployed" in response.data
        with app.app_context():
            assert DeploymentRun.query.count() == 0

    def test_restart_enqueues_a_run_chained_to_current_deployment(self, manifest_client, app, base_entities):
        with app.app_context():
            server = DeploymentServer.query.get(base_entities["server_id"])
            server.allowed_roles = [Role.query.filter_by(name="ManifestAdmin").first()]

            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [server]
            db.session.add(manifest)
            db.session.flush()

            deploy_run = DeploymentRun(action="deploy", status="success")
            db.session.add(deploy_run)
            db.session.flush()
            deploy_execution = DeploymentExecution(
                run_id=deploy_run.id, manifest_id=manifest.id, server_id=server.id,
                status="success", rendered_yaml="image: nginx",
            )
            db.session.add(deploy_execution)
            db.session.commit()
            manifest_id, deploy_execution_id = manifest.id, deploy_execution.id

        response = manifest_client.post(
            "/deployment-manifests/restart", data={"manifest_ids": [str(manifest_id)]}, follow_redirects=True
        )
        assert response.status_code == 200
        assert response.request.path == "/deployment-runs/"

        with app.app_context():
            restart_run = DeploymentRun.query.filter_by(action="restart").first()
            assert restart_run is not None
            execution = DeploymentExecution.query.filter_by(run_id=restart_run.id).first()
            assert execution.source_execution_id == deploy_execution_id


class TestManifestAllowedUsersAccess:
    """DeploymentManifest.allowed_users — per-user (not role) access gate on
    top of the existing per-server allowed_roles gate. Empty allowed_users
    means unrestricted (see the model's docstring for why this differs from
    DeploymentServer.allowed_roles's locked-when-empty default).
    """

    def _make_second_manifest_admin(self, app, client):
        with app.app_context():
            role = Role.query.filter_by(name="ManifestAdmin").first()
            user = User(
                username="manifest_test_2",
                password_hash=generate_password_hash(MANIFEST_PASSWORD),
                is_active=True,
                role_id=role.id,
            )
            db.session.add(user)
            db.session.commit()
            user_id = user.id

        client.post("/login", data={"username": "manifest_test_2", "password": MANIFEST_PASSWORD}, follow_redirects=True)
        return client, user_id

    def test_empty_allowed_users_permits_anyone_with_permission(self, manifest_client, app, base_entities):
        with app.app_context():
            server = DeploymentServer.query.get(base_entities["server_id"])
            server.allowed_roles = [Role.query.filter_by(name="ManifestAdmin").first()]
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [server]
            db.session.add(manifest)
            db.session.commit()
            assert manifest.allowed_users == []
            manifest_id = manifest.id

        response = manifest_client.post(
            "/deployment-manifests/deploy", data={"manifest_ids": [str(manifest_id)]}, follow_redirects=True
        )
        assert response.status_code == 200
        assert response.request.path == "/deployment-runs/"

    def test_nonempty_allowed_users_blocks_a_user_not_on_the_list(self, client, app, base_entities, manifest_client):
        # manifest_client fixture ensures the "ManifestAdmin" role exists —
        # its own user isn't otherwise used in this test.
        scoped_client, _user_id = self._make_second_manifest_admin(app, client)

        with app.app_context():
            server = DeploymentServer.query.get(base_entities["server_id"])
            server.allowed_roles = [Role.query.filter_by(name="ManifestAdmin").first()]

            only_admin = User.query.filter_by(username="manifest_test_2").first()
            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [server]
            manifest.allowed_users = [only_admin]
            db.session.add(manifest)
            db.session.commit()
            manifest_id = manifest.id

        response = scoped_client.post("/deployment-manifests/deploy", data={"manifest_ids": [str(manifest_id)]})
        assert response.status_code != 403  # manifest_test_2 IS on the allowlist

        # A third user who can deploy in general (holds deployment.deploy)
        # but does NOT hold deployment_manifest.manage — i.e. not on this
        # manifest's allowlist and with no bypass available — must be
        # blocked. (Reusing "ManifestAdmin" here would be a no-op test: that
        # role also holds deployment_manifest.manage, which always bypasses
        # allowed_users by design — see test_manifest_manage_permission_
        # bypasses_allowed_users below.)
        with app.app_context():
            deploy_only_permission = Permission.query.filter_by(code="deployment.deploy").first()
            deploy_only_role = Role(name="DeployOnly", description="Can deploy, no manage")
            deploy_only_role.permissions = [deploy_only_permission]
            db.session.add(deploy_only_role)
            db.session.flush()

            third_user = User(
                username="manifest_test_3",
                password_hash=generate_password_hash(MANIFEST_PASSWORD),
                is_active=True,
                role_id=deploy_only_role.id,
            )
            db.session.add(third_user)
            db.session.commit()

        third_client = app.test_client()
        third_client.post("/login", data={"username": "manifest_test_3", "password": MANIFEST_PASSWORD})
        response = third_client.post("/deployment-manifests/deploy", data={"manifest_ids": [str(manifest_id)]})
        assert response.status_code == 403

    def test_manifest_manage_permission_bypasses_allowed_users(self, manifest_client, app, base_entities):
        """deployment_manifest.manage always grants full access, same as
        DeploymentServer.allowed_roles' equivalent bypass.
        """
        with app.app_context():
            server = DeploymentServer.query.get(base_entities["server_id"])
            server.allowed_roles = [Role.query.filter_by(name="ManifestAdmin").first()]

            someone_else = User(
                username="someone_else", password_hash=generate_password_hash(MANIFEST_PASSWORD), is_active=True
            )
            db.session.add(someone_else)
            db.session.flush()

            manifest = DeploymentManifest(name="m1", yaml_content="image: nginx")
            manifest.target_servers = [server]
            manifest.allowed_users = [someone_else]  # manifest_client's user is deliberately NOT on this list
            db.session.add(manifest)
            db.session.commit()
            manifest_id = manifest.id

        # manifest_client holds deployment_manifest.manage (see manifest_user
        # fixture), which bypasses allowed_users entirely.
        response = manifest_client.post(
            "/deployment-manifests/deploy", data={"manifest_ids": [str(manifest_id)]}, follow_redirects=True
        )
        assert response.status_code == 200
        assert response.request.path == "/deployment-runs/"
