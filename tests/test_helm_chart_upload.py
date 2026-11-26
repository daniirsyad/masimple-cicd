"""Uploaded Helm charts: checking the archive, storing every version,
picking one to deploy, and deploying it from the stored bytes."""
import io
import tarfile

import pytest

from app.blueprints.deployment_manifests import routes as manifest_routes
from app.extensions import db
from app.models import DeploymentExecution, DeploymentManifest, DeploymentServer, HelmChartUpload
from app.services.deployment.base import DeployResult
from app.services.deployment.helm import MAX_CHART_UPLOAD_BYTES, ChartArchiveError, HelmChart, read_chart_archive
from app.services.deployment.kubernetes_provider import KubernetesProvider
from app.services.deployment.worker import _run_deployment, enqueue_deployment_run
from app.utils.crypto import encrypt

MANAGE = ("deployment_manifest.view", "deployment_manifest.manage", "deployment_manifest.delete")


def make_chart(name="web", version="1.0.0", files=None, chart_yaml=None):
    """A .tgz shaped like `helm package` output: <name>/Chart.yaml + files."""
    buffer = io.BytesIO()
    contents = {f"{name}/Chart.yaml": chart_yaml or f"apiVersion: v2\nname: {name}\nversion: {version}\n"}
    contents.update(files or {})
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, text in contents.items():
            data = text.encode()
            info = tarfile.TarInfo(path)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class TestReadChartArchive:
    def test_reads_name_and_version(self):
        info = read_chart_archive(make_chart("web", "2.3.4", {"web/values.yaml": "a: 1\n"}))
        assert (info["name"], info["version"]) == ("web", "2.3.4") and len(info["sha256"]) == 64

    @pytest.mark.parametrize("data,message", [
        (b"", "empty"),
        (b"not a tarball", "isn't a .tgz"),
        (make_chart(files={"other/Chart.yaml": "name: x\nversion: 1\n"}), "one chart folder"),
        (make_chart(chart_yaml="apiVersion: v2\nname: web\n"), "needs a name and a version"),
        (make_chart(chart_yaml="name: [unclosed\n"), "isn't valid YAML"),
    ])
    def test_rejects_what_isnt_a_packaged_chart(self, data, message):
        with pytest.raises(ChartArchiveError, match=message):
            read_chart_archive(data)

    def test_rejects_an_oversized_file(self):
        with pytest.raises(ChartArchiveError, match="larger than 10 MB"):
            read_chart_archive(b"x" * (MAX_CHART_UPLOAD_BYTES + 1))

    def test_dependencies_must_be_packaged_inside(self):
        chart_yaml = "apiVersion: v2\nname: web\nversion: 1.0.0\ndependencies:\n  - name: redis\n    version: 1.x\n"
        with pytest.raises(ChartArchiveError, match="missing from the archive: redis"):
            read_chart_archive(make_chart(chart_yaml=chart_yaml))
        packaged = make_chart(chart_yaml=chart_yaml, files={"web/charts/redis-1.2.0.tgz": "fake"})
        assert read_chart_archive(packaged)["name"] == "web"


@pytest.fixture
def server_id(app):
    with app.app_context():
        server = DeploymentServer(name="k8s", connection_type="kube", encrypted_credentials=encrypt("fake-kubeconfig"))
        db.session.add(server)
        db.session.commit()
        return server.id


def _form(prefix, server_id, chart_bytes=None, filename="web-1.0.0.tgz", **overrides):
    data = {
        f"{prefix}name": "web",
        f"{prefix}source_type": "helm",
        f"{prefix}helm_chart_source": "upload",
        f"{prefix}yaml_content": "",
        f"{prefix}helm_release_name": "web",
        f"{prefix}helm_namespace": "apps",
        f"{prefix}server_ids": [str(server_id)],
    }
    if chart_bytes is not None:
        data[f"{prefix}helm_chart_file"] = (io.BytesIO(chart_bytes), filename)
    data.update({f"{prefix}{key}": value for key, value in overrides.items()})
    return data


def _create(client, server_id, chart_bytes):
    return client.post(
        "/deployment-manifests/create", data=_form("create-manifest-", server_id, chart_bytes),
        content_type="multipart/form-data", follow_redirects=True,
    )


def _edit(client, manifest_id, server_id, chart_bytes=None, **overrides):
    return client.post(
        f"/deployment-manifests/{manifest_id}/edit",
        data=_form(f"manifest-{manifest_id}-", server_id, chart_bytes, **overrides),
        content_type="multipart/form-data", follow_redirects=True,
    )


class TestUploadThroughTheForm:
    def test_create_stores_the_chart_and_deploys_its_version(self, perm_client, app, server_id):
        response = _create(perm_client(*MANAGE), server_id, make_chart("web", "1.0.0"))
        assert "Manifest &#39;web&#39; created." in response.get_data(as_text=True)
        with app.app_context():
            manifest = DeploymentManifest.query.filter_by(name="web").one()
            assert (manifest.helm_chart_source, manifest.helm_chart_name, manifest.helm_chart_version) == ("upload", "web", "1.0.0")
            assert manifest.helm_chart_repo is None
            upload = manifest.selected_chart_upload
            assert upload.filename == "web-1.0.0.tgz" and upload.data == make_chart("web", "1.0.0")

    def test_create_without_a_file_is_rejected(self, perm_client, app, server_id):
        response = _create(perm_client(*MANAGE), server_id, None)
        assert "Choose a chart file (.tgz) to upload." in response.get_data(as_text=True)
        with app.app_context():
            assert DeploymentManifest.query.count() == 0

    def test_a_bad_file_is_rejected_with_the_reason(self, perm_client, app, server_id):
        response = _create(perm_client(*MANAGE), server_id, b"not a chart")
        assert "isn&#39;t a .tgz file" in response.get_data(as_text=True)

    def test_new_versions_are_added_and_an_older_one_can_be_picked(self, perm_client, app, server_id):
        client = perm_client(*MANAGE)
        _create(client, server_id, make_chart("web", "1.0.0"))
        with app.app_context():
            manifest_id = DeploymentManifest.query.filter_by(name="web").one().id

        _edit(client, manifest_id, server_id, make_chart("web", "1.1.0"))
        with app.app_context():
            manifest = DeploymentManifest.query.get(manifest_id)
            assert manifest.helm_chart_version == "1.1.0" and manifest.helm_chart_uploads.count() == 2

        html = client.get("/deployment-manifests/").get_data(as_text=True)
        assert "web 1.0.0 (" in html and "web 1.1.0 (" in html

        # Roll back: no new file, pick the older version.
        _edit(client, manifest_id, server_id, helm_chart_upload_version="1.0.0")
        with app.app_context():
            assert DeploymentManifest.query.get(manifest_id).helm_chart_version == "1.0.0"

    def test_same_version_with_different_contents_is_rejected(self, perm_client, app, server_id):
        client = perm_client(*MANAGE)
        _create(client, server_id, make_chart("web", "1.0.0"))
        with app.app_context():
            manifest_id = DeploymentManifest.query.filter_by(name="web").one().id
        changed = make_chart("web", "1.0.0", {"web/values.yaml": "changed: true\n"})
        response = _edit(client, manifest_id, server_id, changed)
        assert "Version 1.0.0 is already uploaded with different contents." in response.get_data(as_text=True)
        # Re-uploading the identical file is fine and adds nothing.
        _edit(client, manifest_id, server_id, make_chart("web", "1.0.0"))
        with app.app_context():
            assert HelmChartUpload.query.count() == 1

    def test_deleting_the_manifest_deletes_its_uploads(self, perm_client, app, server_id):
        client = perm_client(*MANAGE)
        _create(client, server_id, make_chart())
        with app.app_context():
            manifest_id = DeploymentManifest.query.filter_by(name="web").one().id
        client.post(f"/deployment-manifests/{manifest_id}/delete", follow_redirects=True)
        with app.app_context():
            assert DeploymentManifest.query.count() == 0 and HelmChartUpload.query.count() == 0


class TestDefaultValuesFromAnUpload:
    def test_from_a_picked_file(self, perm_client, monkeypatch):
        seen = {}
        monkeypatch.setattr(manifest_routes, "show_values", lambda chart: seen.setdefault("chart", chart) and "a: 1\n")
        response = perm_client(*MANAGE).post(
            "/deployment-manifests/api/helm-default-values",
            data={"source": "upload", "chart_file": (io.BytesIO(make_chart("web", "3.0.0")), "web.tgz")},
            content_type="multipart/form-data",
        )
        assert response.json["ok"] is True
        assert seen["chart"].archive == make_chart("web", "3.0.0") and seen["chart"].label() == "web@3.0.0"

    def test_from_a_stored_version(self, perm_client, app, server_id, monkeypatch):
        client = perm_client(*MANAGE)
        _create(client, server_id, make_chart("web", "1.0.0"))
        with app.app_context():
            manifest_id = DeploymentManifest.query.filter_by(name="web").one().id
        monkeypatch.setattr(manifest_routes, "show_values", lambda chart: f"from {chart.label()}")
        response = client.post(
            "/deployment-manifests/api/helm-default-values",
            data={"source": "upload", "manifest_id": str(manifest_id), "upload_version": "1.0.0"},
        )
        assert response.json == {"ok": True, "values": "from web@1.0.0"}

    def test_bad_file_and_nothing_chosen_are_400s(self, perm_client):
        client = perm_client(*MANAGE)
        bad = client.post("/deployment-manifests/api/helm-default-values",
                          data={"source": "upload", "chart_file": (io.BytesIO(b"nope"), "x.tgz")},
                          content_type="multipart/form-data")
        assert bad.status_code == 400 and "isn't a .tgz" in bad.json["error"]
        nothing = client.post("/deployment-manifests/api/helm-default-values", data={"source": "upload"})
        assert nothing.status_code == 400


def test_deploy_passes_the_uploaded_archive_to_helm(app, server_id, monkeypatch):
    with app.app_context():
        manifest = DeploymentManifest(
            name="web", yaml_content="", source_type="helm", helm_chart_source="upload",
            helm_chart_name="web", helm_chart_version="1.0.0", helm_release_name="web", helm_namespace="apps",
        )
        manifest.target_servers = [DeploymentServer.query.get(server_id)]
        db.session.add(manifest)
        db.session.flush()
        db.session.add(HelmChartUpload(manifest_id=manifest.id, chart_name="web", chart_version="1.0.0",
                                       filename="web.tgz", sha256="x" * 64, size_bytes=3, data=b"tgz"))
        db.session.commit()
        run, _count = enqueue_deployment_run(manifests=[manifest], triggered_by=None)
        execution_id = DeploymentExecution.query.filter_by(run_id=run.id).one().id

    seen = {}

    def _fake_upgrade(self, chart, release, namespace, values, wait_timeout_seconds=None):
        seen["chart"] = chart
        return DeployResult(success=True, log="ok")

    monkeypatch.setattr(KubernetesProvider, "helm_upgrade", _fake_upgrade)
    _run_deployment(app, execution_id)

    assert seen["chart"].archive == b"tgz" and seen["chart"].label() == "web@1.0.0"
    with app.app_context():
        execution = DeploymentExecution.query.get(execution_id)
        assert execution.status == "success" and "from uploaded chart" in execution.log


def test_provider_installs_from_the_archive_file_without_version_flag(monkeypatch):
    import subprocess

    from app.services.deployment import kubernetes_provider

    calls = []

    def _fake_run(args, input=None, capture_output=None, text=None, env=None, timeout=None):
        if args[1] == "upgrade":
            with open(args[4], "rb") as archive_file:
                calls.append((args, archive_file.read()))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr(kubernetes_provider, "rollback_on_failure_flag", lambda: "--atomic")
    chart = HelmChart(repo="", name="web", version="1.0.0", archive=b"tgz-bytes")
    result = KubernetesProvider("kubeconfig").helm_upgrade(chart, "web", "apps", "")

    args, written = calls[0]
    assert result.success and written == b"tgz-bytes"
    assert args[4].endswith("chart.tgz") and "--version" not in args and "repo" not in args
