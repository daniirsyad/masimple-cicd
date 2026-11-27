"""Detect on servers (app/services/deployment/detect.py), {{SYS:TAG}}
placeholders, several versions in one Helm values file, and Find images
(app/services/deployment/image_slots.py). The cluster is faked by patching
KubernetesProvider's read-only lookups."""
import json

import pytest

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
    Version,
    VersionType,
)
from app.services.deployment import detect as detect_module
from app.services.deployment.image_slots import apply_image_slots, find_image_slots
from app.services.deployment.kubernetes_provider import KubernetesProvider
from app.services.deployment.resolver import resolve_manifest, split_image_ref
from app.services.deployment.worker import get_available_update, is_currently_deployed
from app.utils.crypto import encrypt

DEPLOY_CODES = ("deployment_manifest.view", "deployment.deploy", "deployment_server.manage")

YAML_MANIFEST = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: shop
spec:
  template:
    spec:
      containers:
        - name: web
          image: {{SYS:VERSION:web}}
        - name: api
          image: registry.acme.dev/api:{{SYS:TAG:api}}
---
apiVersion: v1
kind: Service
metadata:
  name: shop
"""

HELM_VALUES = """frontend:
  image:
    tag: {{SYS:TAG:web}}
backend:
  image:
    tag: {{SYS:TAG:api}}
worker:
  image: registry.acme.dev/worker:{{SYS:TAG:worker}}
"""


def _builder(name, version_type):
    git_source = GitSource.query.first() or GitSource(name="gs", provider_type="github", encrypted_token="x")
    db.session.add(git_source)
    db.session.flush()
    repository = Repository(git_source_id=git_source.id, full_name=f"org/{name}", local_path=f"/tmp/{name}", status="ready")
    registry = RegistryTarget.query.first() or RegistryTarget(name="reg", provider_type="dockerhub", username="u")
    db.session.add_all([repository, registry])
    db.session.flush()
    version = Version(name=name, version_type_id=version_type.id)
    db.session.add(version)
    db.session.flush()
    builder = Builder(name=f"{name}-builder", version_id=version.id, repository_id=repository.id,
                      default_branch="main", registry_target_id=registry.id)
    db.session.add(builder)
    db.session.flush()
    return builder


def _build(builder, image_tag):
    batch = BuildBatch(version_id=builder.version_id, bump_type="patch", status="success", full_version_string=image_tag)
    db.session.add(batch)
    db.session.flush()
    build = ImageBuild(batch_id=batch.id, builder_id=builder.id, branch_used="main", status="success", image_tag=image_tag)
    db.session.add(build)
    db.session.flush()
    return build


@pytest.fixture
def world(app):
    """Builders web/api/worker with builds, a kube server and an api server."""
    with app.app_context():
        version_type = VersionType(name="DEV")
        db.session.add(version_type)
        db.session.flush()
        builders = {name: _builder(name, version_type) for name in ("web", "api", "worker")}
        _build(builders["web"], "registry.acme.dev/web:DEV.1.0.0")
        _build(builders["web"], "registry.acme.dev/web:DEV.1.1.0")
        _build(builders["api"], "registry.acme.dev/api:DEV.2.0.0")
        _build(builders["worker"], "registry.acme.dev/worker:DEV.3.0.0")
        kube = DeploymentServer(name="kube1", connection_type="kube", encrypted_credentials=encrypt("fake-kubeconfig"))
        agent = DeploymentServer(name="agent1", connection_type="api",
                                 encrypted_credentials=encrypt(json.dumps({"api_url": "http://x", "token": "t"})))
        db.session.add_all([kube, agent])
        db.session.commit()
        return {
            "builders": {name: builder.id for name, builder in builders.items()},
            "kube": kube.id,
            "agent": agent.id,
        }


def _manifest(app, world, yaml_content, keys, helm=False, servers=("kube",), name="shop"):
    with app.app_context():
        manifest = DeploymentManifest(name=name, yaml_content=yaml_content)
        if helm:
            manifest.source_type = "helm"
            manifest.helm_chart_repo = "https://charts.example.com"
            manifest.helm_chart_name = "shop"
            manifest.helm_chart_version = "1.2.0"
            manifest.helm_release_name = "shop"
            manifest.helm_namespace = "apps"
        manifest.target_servers = [DeploymentServer.query.get(world[s]) for s in servers]
        db.session.add(manifest)
        db.session.flush()
        for key in keys:
            db.session.add(DeploymentManifestVersionBinding(
                manifest_id=manifest.id, placeholder_key=key, builder_id=world["builders"][key]))
        db.session.commit()
        return manifest.id


def _live_deployment(web_image, api_image):
    return [
        {"kind": "Deployment", "metadata": {"name": "shop", "namespace": "default"},
         "spec": {"template": {"spec": {"containers": [
             {"name": "api", "image": api_image},  # order differs from the manifest on purpose
             {"name": "web", "image": web_image},
         ]}}}},
        {"kind": "Service", "metadata": {"name": "shop", "namespace": "default"}},
    ]


@pytest.fixture
def fake_cluster(monkeypatch):
    """Set .objects / .release / .diff_text to what the cluster should report."""
    class Cluster:
        objects = []
        release = None
        diff_text = ""

    def _objects(self, manifest_yaml):
        return Cluster.objects

    monkeypatch.setattr(KubernetesProvider, "get_live_objects", _objects)
    monkeypatch.setattr(KubernetesProvider, "diff", lambda self, manifest_yaml: Cluster.diff_text)
    monkeypatch.setattr(KubernetesProvider, "helm_release_info", lambda self, release, namespace: Cluster.release)
    return Cluster


def _detect(app, manifest_id, server_id):
    with app.app_context():
        return detect_module.detect(DeploymentManifest.query.get(manifest_id), DeploymentServer.query.get(server_id))


def _versions(result):
    return {entry["key"]: entry for entry in result["versions"]}


class TestTagPlaceholder:
    def test_tag_resolves_to_only_the_tag(self, app, world):
        manifest_id = _manifest(app, world, "full: {{SYS:VERSION:web}}\ntag: {{SYS:TAG:web}}\n", ["web"])
        with app.app_context():
            rendered, versions = resolve_manifest(DeploymentManifest.query.get(manifest_id))
        assert rendered == "full: registry.acme.dev/web:DEV.1.1.0\ntag: DEV.1.1.0\n"
        assert versions == {"web": "registry.acme.dev/web:DEV.1.1.0"}

    def test_split_image_ref_ignores_a_registry_port(self):
        assert split_image_ref("reg:5000/org/app:1.2") == ("reg:5000/org/app", "1.2")
        assert split_image_ref("reg:5000/org/app") == ("reg:5000/org/app", "")


class TestSeveralVersionsInHelmValues:
    def test_each_key_resolves_from_its_own_builder(self, app, world):
        manifest_id = _manifest(app, world, HELM_VALUES, ["web", "api", "worker"], helm=True)
        with app.app_context():
            rendered, versions = resolve_manifest(DeploymentManifest.query.get(manifest_id))
        assert "tag: DEV.1.1.0" in rendered and "tag: DEV.2.0.0" in rendered
        assert "image: registry.acme.dev/worker:DEV.3.0.0" in rendered
        assert list(versions) == ["web", "api", "worker", "helm chart"]


class TestDetectKubernetesYaml:
    def test_found_reads_each_version_by_container_name(self, app, world, fake_cluster):
        manifest_id = _manifest(app, world, YAML_MANIFEST, ["web", "api"])
        fake_cluster.objects = _live_deployment("registry.acme.dev/web:DEV.1.0.0", "registry.acme.dev/api:hotfix")
        fake_cluster.diff_text = "-image: old\n+image: new\n"
        result = _detect(app, manifest_id, world["kube"])
        assert result["state"] == "found"
        assert result["found"] == ["Deployment shop", "Service shop"]
        versions = _versions(result)
        assert versions["web"]["state"] == "build"
        assert versions["web"]["ref"] == "registry.acme.dev/web:DEV.1.0.0"
        assert versions["api"]["state"] == "outside" and versions["api"]["value"] == "hotfix"
        assert result["diff"].startswith("-image")

    def test_partly_found_is_not_markable(self, app, world, fake_cluster):
        manifest_id = _manifest(app, world, YAML_MANIFEST, ["web", "api"])
        fake_cluster.objects = _live_deployment("registry.acme.dev/web:DEV.1.0.0", "x")[:1]
        result = _detect(app, manifest_id, world["kube"])
        assert result["state"] == "partial"
        assert result["missing"] == ["Service shop"]
        assert "_record" not in result

    def test_not_found(self, app, world, fake_cluster):
        manifest_id = _manifest(app, world, YAML_MANIFEST, ["web", "api"])
        fake_cluster.objects = []
        assert _detect(app, manifest_id, world["kube"])["state"] == "missing"

    def test_custom_agent_server_is_unsupported(self, app, world, fake_cluster):
        manifest_id = _manifest(app, world, YAML_MANIFEST, ["web", "api"], servers=("agent",))
        assert _detect(app, manifest_id, world["agent"])["state"] == "unsupported"

    def test_mixed_values_for_one_key(self, app, world, fake_cluster):
        manifest_id = _manifest(app, world, "kind: ConfigMap\nmetadata:\n  name: c\ndata:\n  a: {{SYS:TAG:web}}\n  b: {{SYS:TAG:web}}\n", ["web"])
        fake_cluster.objects = [{"kind": "ConfigMap", "metadata": {"name": "c"}, "data": {"a": "DEV.1.0.0", "b": "DEV.1.1.0"}}]
        versions = _versions(_detect(app, manifest_id, world["kube"]))
        assert versions["web"]["state"] == "mixed"


class TestDetectHelm:
    def test_reads_three_versions_and_the_chart(self, app, world, fake_cluster):
        manifest_id = _manifest(app, world, HELM_VALUES, ["web", "api", "worker"], helm=True)
        fake_cluster.release = {
            "status": "deployed",
            "chart": "shop-1.1.0",
            "values": {
                "frontend": {"image": {"tag": "DEV.1.1.0"}},
                "worker": {"image": "registry.acme.dev/worker:DEV.3.0.0"},
            },
        }
        result = _detect(app, manifest_id, world["kube"])
        assert result["state"] == "found"
        versions = _versions(result)
        assert versions["web"]["state"] == "build"
        assert versions["api"]["state"] == "chart default"
        assert versions["worker"]["state"] == "build"
        assert "1.1.0" in result["chart_note"]
        assert result["_record"]["helm_release_name"] == "shop"
        assert result["_record"]["resolved_version_string"].endswith("helm chart=shop@1.1.0")

    def test_release_not_installed(self, app, world, fake_cluster):
        manifest_id = _manifest(app, world, HELM_VALUES, ["web", "api", "worker"], helm=True)
        fake_cluster.release = None
        assert _detect(app, manifest_id, world["kube"])["state"] == "missing"

    def test_failed_release_is_not_markable(self, app, world, fake_cluster):
        manifest_id = _manifest(app, world, HELM_VALUES, ["web", "api", "worker"], helm=True)
        fake_cluster.release = {"status": "failed", "chart": "shop-1.2.0", "values": {}}
        assert _detect(app, manifest_id, world["kube"])["state"] == "unhealthy"


class TestMarkAsDeployed:
    def _mark(self, client, manifest_id, server_id):
        return client.post("/deployment-manifests/detect", data={"pairs": f"{manifest_id}:{server_id}"})

    def test_marked_manifest_is_deployed_and_offers_update_and_stop(self, app, world, fake_cluster, perm_client):
        manifest_id = _manifest(app, world, YAML_MANIFEST, ["web", "api"])
        fake_cluster.objects = _live_deployment("registry.acme.dev/web:DEV.1.1.0", "registry.acme.dev/api:DEV.2.0.0")
        client = perm_client(*DEPLOY_CODES)
        assert self._mark(client, manifest_id, world["kube"]).status_code == 302
        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            server = DeploymentServer.query.get(world["kube"])
            assert is_currently_deployed(manifest.id, server.id)
            run = DeploymentRun.query.one()
            assert (run.action, run.status) == ("detect", "success")
            execution = DeploymentExecution.query.one()
            assert execution.resolved_version_string == (
                "web=registry.acme.dev/web:DEV.1.1.0; api=registry.acme.dev/api:DEV.2.0.0"
            )
            assert "image: registry.acme.dev/api:DEV.2.0.0" in execution.rendered_yaml
            # Everything is on the latest build: nothing to update...
            assert get_available_update(manifest, server) is None
            # ...until one Builder gets a newer build.
            _build(Builder.query.get(world["builders"]["api"]), "registry.acme.dev/api:DEV.2.1.0")
            db.session.commit()
            assert get_available_update(manifest, server) is not None

    def test_outside_version_offers_update(self, app, world, fake_cluster, perm_client):
        manifest_id = _manifest(app, world, YAML_MANIFEST, ["web", "api"])
        fake_cluster.objects = _live_deployment("nginx:latest", "registry.acme.dev/api:DEV.2.0.0")
        self._mark(perm_client(*DEPLOY_CODES), manifest_id, world["kube"])
        with app.app_context():
            assert get_available_update(DeploymentManifest.query.get(manifest_id), DeploymentServer.query.get(world["kube"]))

    def test_stop_deletes_what_was_detected(self, app, world, fake_cluster, perm_client, monkeypatch):
        manifest_id = _manifest(app, world, YAML_MANIFEST, ["web", "api"])
        fake_cluster.objects = _live_deployment("registry.acme.dev/web:DEV.1.1.0", "registry.acme.dev/api:DEV.2.0.0")
        self._mark(perm_client(*DEPLOY_CODES), manifest_id, world["kube"])
        from app.services.deployment.worker import enqueue_deployment_run
        with app.app_context():
            run, count = enqueue_deployment_run([DeploymentManifest.query.get(manifest_id)], None, action="stop")
            stop = DeploymentExecution.query.filter_by(run_id=run.id).one()
            assert count == 1
            assert stop.source_execution.run.action == "detect"
            assert "kind: Service" in stop.source_execution.rendered_yaml

    def test_helm_mark_records_the_release(self, app, world, fake_cluster, perm_client):
        manifest_id = _manifest(app, world, HELM_VALUES, ["web", "api", "worker"], helm=True)
        fake_cluster.release = {"status": "deployed", "chart": "shop-1.2.0", "values": {}}
        self._mark(perm_client(*DEPLOY_CODES), manifest_id, world["kube"])
        with app.app_context():
            execution = DeploymentExecution.query.one()
            assert (execution.helm_release_name, execution.helm_namespace) == ("shop", "apps")

    def test_not_found_anymore_is_not_marked(self, app, world, fake_cluster, perm_client):
        manifest_id = _manifest(app, world, YAML_MANIFEST, ["web", "api"])
        fake_cluster.objects = []
        response = self._mark(perm_client(*DEPLOY_CODES), manifest_id, world["kube"])
        assert response.status_code == 302
        with app.app_context():
            assert DeploymentRun.query.count() == 0

    def test_already_deployed_is_skipped(self, app, world, fake_cluster, perm_client):
        manifest_id = _manifest(app, world, YAML_MANIFEST, ["web", "api"])
        fake_cluster.objects = _live_deployment("registry.acme.dev/web:DEV.1.1.0", "registry.acme.dev/api:DEV.2.0.0")
        client = perm_client(*DEPLOY_CODES)
        self._mark(client, manifest_id, world["kube"])
        self._mark(client, manifest_id, world["kube"])
        with app.app_context():
            assert DeploymentRun.query.count() == 1

    def test_preview_returns_json_without_the_record(self, app, world, fake_cluster, perm_client):
        manifest_id = _manifest(app, world, YAML_MANIFEST, ["web", "api"])
        fake_cluster.objects = _live_deployment("registry.acme.dev/web:DEV.1.1.0", "registry.acme.dev/api:DEV.2.0.0")
        data = perm_client(*DEPLOY_CODES).post(
            "/deployment-manifests/api/detect-preview", data={"manifest_ids": str(manifest_id)}).get_json()
        server = data["results"][0]["servers"][0]
        assert server["state"] == "found" and "_record" not in server

    def test_needs_deploy_permission(self, app, world, fake_cluster, perm_client):
        manifest_id = _manifest(app, world, YAML_MANIFEST, ["web", "api"])
        client = perm_client("deployment_manifest.view", "deployment_server.manage")
        assert self._mark(client, manifest_id, world["kube"]).status_code == 403
        assert client.post("/deployment-manifests/api/detect-preview",
                           data={"manifest_ids": str(manifest_id)}).status_code == 403
        html = client.get("/deployment-manifests/").get_data(as_text=True)
        assert "detect-one-btn" not in html and "detect-trigger-modal" not in html

    def test_deployer_sees_the_menu_item(self, app, world, perm_client):
        _manifest(app, world, YAML_MANIFEST, ["web", "api"])
        html = perm_client(*DEPLOY_CODES).get("/deployment-manifests/").get_data(as_text=True)
        assert "Detect on servers" in html and "detect-trigger-modal" in html


class TestFindImages:
    VALUES = (
        "# keep this comment\n"
        "frontend:\n  image:\n    repository: acme/web\n    tag: \"\"\n"
        "worker:\n  image: registry.acme.dev/worker:1.0\n"
        "done:\n  image:\n    tag: {{SYS:TAG:done}}\n"
    )

    def test_finds_tags_and_full_images_but_not_placeholders(self, app, world):
        with app.app_context():
            slots = find_image_slots(self.VALUES)
        assert [(s["path"], s["kind"], s["key"]) for s in slots] == [
            ("frontend.image.tag", "tag", "frontend"),
            ("worker.image", "image", "worker"),
        ]
        assert slots[1]["builder_id"] == str(world["builders"]["worker"])  # same repository

    def test_apply_keeps_comments_and_sets_the_repository(self, app, world):
        web = str(world["builders"]["web"])
        with app.app_context():
            content, bindings = apply_image_slots(self.VALUES, [
                {"id": 0, "key": "web", "builder_id": web},
                {"id": 1, "key": "worker", "builder_id": ""},
            ])
        assert content.startswith("# keep this comment\n")
        assert 'repository: "registry.acme.dev/web"' in content
        assert 'tag: "{{SYS:TAG:web}}"' in content
        assert 'image: "{{SYS:VERSION:worker}}"' in content
        assert bindings == [{"key": "web", "builder_id": web}, {"key": "worker", "builder_id": None}]

    def test_routes_need_manage_permission(self, perm_client):
        client = perm_client("deployment_manifest.view")
        assert client.post("/deployment-manifests/api/find-images", data={"yaml_content": "a: 1"}).status_code == 403
        assert client.post("/deployment-manifests/api/apply-images", data={"yaml_content": "a: 1"}).status_code == 403
