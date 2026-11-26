"""The search box on Deployment Manifests, Builders and Workflows. Filtering
runs in the browser (js/list-search.js); these check the server renders what
it filters on: the box, the group cards and each row's lowercase search text."""
from app.extensions import db
from app.models import (
    Builder,
    DeploymentManifest,
    DeploymentServer,
    GitSource,
    RegistryTarget,
    Repository,
    Version,
    VersionType,
    Workflow,
)


def test_manifests_page(perm_client, app):
    with app.app_context():
        server = DeploymentServer(name="Prod-K8s", connection_type="kube")
        manifest = DeploymentManifest(
            name="Checkout-API", yaml_content="x", group_name="Payments", source_type="helm",
            helm_chart_name="web", helm_chart_version="1", helm_release_name="checkout", helm_namespace="apps",
        )
        manifest.target_servers = [server]
        db.session.add_all([server, manifest])
        db.session.commit()

    html = perm_client("deployment_manifest.view").get("/deployment-manifests/").get_data(as_text=True)
    assert 'data-list-search="#manifest-lists"' in html
    assert 'data-search-card="payments"' in html
    assert 'data-search="checkout-api payments prod-k8s web checkout apps helm"' in html


def test_builders_page(perm_client, app):
    with app.app_context():
        source = GitSource(name="gh", provider_type="github", encrypted_token="x")
        registry = RegistryTarget(name="Hub", provider_type="dockerhub")
        version_type = VersionType(name="DEV")
        db.session.add_all([source, registry, version_type])
        db.session.flush()
        repository = Repository(git_source_id=source.id, full_name="Org/Shop", local_path="/tmp/x", status="ready")
        version = Version(name="shop", version_type_id=version_type.id)
        db.session.add_all([repository, version])
        db.session.flush()
        db.session.add(Builder(
            name="Shop-Web", version_id=version.id, repository_id=repository.id, default_branch="main",
            dockerfile_path="Dockerfile", registry_target_id=registry.id,
        ))
        db.session.commit()

    html = perm_client("builder.view", "builder.manage").get("/builders/").get_data(as_text=True)
    assert 'data-list-search="#builder-lists"' in html
    assert 'data-search="shop-web shop org/shop main hub"' in html


def test_workflows_page(perm_client, app):
    with app.app_context():
        db.session.add(Workflow(name="Release Train", description="Build then Deploy"))
        db.session.commit()

    html = perm_client("workflow.view", "workflow.manage").get("/workflows/").get_data(as_text=True)
    assert 'data-list-search="#workflow-lists"' in html
    assert 'data-search="release train build then deploy"' in html


def test_no_search_box_when_there_is_nothing_to_search(perm_client):
    html = perm_client("deployment_manifest.view").get("/deployment-manifests/").get_data(as_text=True)
    assert "data-list-search" not in html
