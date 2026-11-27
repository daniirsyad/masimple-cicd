"""Per-page overhead: static caching, Server-Timing, the shared per-page queries."""

import re

import pytest
from flask import url_for

from app.extensions import db
from app.models import (
    Builder,
    BuildBatch,
    DeploymentManifest,
    DeploymentManifestVersionBinding,
    DeploymentServer,
    GitSource,
    ImageBuild,
    ImageBuildCommit,
    Menu,
    Object,
    Permission,
    RegistryTarget,
    Repository,
    User,
    Version,
    VersionDocumentation,
    VersionType,
)
from app.utils.setup_status import is_setup_complete
from app.utils.system_config import get_system_config


def _query_count(response):
    return int(re.search(r'db;desc="(\d+) queries"', response.headers["Server-Timing"]).group(1))


def test_static_urls_are_versioned_and_cached(app, client):
    with app.test_request_context():
        url = url_for("static", filename="js/page.js")
    assert re.search(r"\?v=\d+$", url)
    response = client.get(url)
    assert response.status_code == 200
    assert "max-age=31536000" in response.headers["Cache-Control"]


def test_base_page_query_count_stays_small(admin_client):
    # The layout's own queries (user, role, permissions, menus, config,
    # review badge) plus the users list. The layout alone took 7 more
    # before it shared them per request; don't let it creep back up.
    response = admin_client.get("/users/")
    assert response.status_code == 200
    assert _query_count(response) <= 9


def test_system_config_is_reread_on_each_request(app, admin_client):
    admin_client.get("/users/")
    with app.app_context():
        get_system_config().session_timeout_minutes = 77
        db.session.commit()
    # The next request's session lifetime comes from the new value, not one
    # cached by the previous request.
    admin_client.get("/users/")
    assert app.permanent_session_lifetime.total_seconds() == 77 * 60


def test_setup_check_fails_cleanly_without_alembic_table(app):
    # The test DB is built by create_all(), so it has no alembic_version.
    with app.app_context():
        assert is_setup_complete() is False
        # Ran on its own connection: the ORM session is still usable.
        User.query.first()


# --- List pages: query count must not grow with the number of rows --------

LIST_PAGES = [
    "/images/",
    "/builders/",
    "/deployment-manifests/",
    "/documentation/",
    "/permissions/",
    "/menus/",
]

PAGE_CODES = (
    "image.view", "builder.view", "builder.manage", "builder.build", "deployment_manifest.view",
    "deployment_manifest.manage", "deployment.deploy", "documentation.edit", "permission.view",
    "permission.edit", "permission.delete", "menu.view", "menu.edit", "logs.view",
)


@pytest.fixture
def list_entities(app):
    with app.app_context():
        git_source = GitSource(name="conn", provider_type="github", encrypted_token="x")
        db.session.add(git_source)
        db.session.flush()
        repository = Repository(
            git_source_id=git_source.id, full_name="org/repo", local_path="/tmp/repo", status="ready"
        )
        registry = RegistryTarget(name="reg", provider_type="dockerhub")
        version_type = VersionType(name="DEV")
        server = DeploymentServer(name="srv", connection_type="kube")
        db.session.add_all([repository, registry, version_type, server])
        db.session.flush()
        version = Version(name="svc", version_type_id=version_type.id)
        db.session.add(version)
        db.session.commit()
        return {
            "repository_id": repository.id, "registry_id": registry.id,
            "version_id": version.id, "server_id": server.id, "rounds": 0,
        }


def _add_rows(app, entities, count):
    """`count` more of everything the list pages show, each row linked the
    way real ones are (build + commit + documentation, manifest + binding)."""
    with app.app_context():
        for _ in range(count):
            n = entities["rounds"] = entities["rounds"] + 1
            builder = Builder(
                name=f"b{n}", version_id=entities["version_id"], repository_id=entities["repository_id"],
                default_branch="main", registry_target_id=entities["registry_id"],
            )
            batch = BuildBatch(
                version_id=entities["version_id"], bump_type="patch", status="success",
                full_version_string=f"DEV.0.0.{n}",
            )
            db.session.add_all([builder, batch])
            db.session.flush()
            build = ImageBuild(
                batch_id=batch.id, builder_id=builder.id, branch_used="main", status="success",
                image_tag=f"u/app:DEV.0.0.{n}", commit_sha=f"sha{n}",
            )
            db.session.add(build)
            db.session.flush()
            db.session.add(ImageBuildCommit(image_build_id=build.id, sha=f"sha{n}", message="change"))
            db.session.add(VersionDocumentation(batch_id=batch.id, objects=[Object(name=f"obj{n}")]))

            manifest = DeploymentManifest(name=f"m{n}", yaml_content="image: {{SYS:VERSION}}")
            manifest.target_servers = [DeploymentServer.query.get(entities["server_id"])]
            db.session.add(manifest)
            db.session.flush()
            db.session.add(
                DeploymentManifestVersionBinding(manifest_id=manifest.id, placeholder_key="default", builder_id=builder.id)
            )

            db.session.add(Permission(code=f"extra.p{n}", description="extra"))
            parent = Menu(label=f"Group {n}", order=n)
            db.session.add(parent)
            db.session.flush()
            db.session.add(Menu(label=f"Item {n}", url=f"/item{n}", parent_id=parent.id, permission_code=f"extra.p{n}"))
        db.session.commit()


@pytest.mark.parametrize("path", LIST_PAGES)
def test_list_page_queries_do_not_grow_with_rows(app, perm_client, list_entities, monkeypatch, path):
    # Not covered: Manifests' "update available" check still resolves each
    # manifest that is deployed somewhere (none are here).
    # Builders reads each repo's branches; a failing read would log an error
    # (a commit, which reloads every row) and hide what this test checks.
    class FakeGit:
        def list_branches(self, path):
            return ["main"]

        def list_files(self, path):
            return ["Dockerfile"]

    monkeypatch.setattr("app.blueprints.builders.routes.provider_for_git_source", lambda source: FakeGit())
    client = perm_client(*PAGE_CODES)
    _add_rows(app, list_entities, 1)
    few = client.get(path)
    assert few.status_code == 200
    _add_rows(app, list_entities, 3)
    many = client.get(path)
    assert many.status_code == 200
    assert _query_count(many) == _query_count(few)
