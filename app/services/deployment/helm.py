"""Helm chart support for DeploymentManifest(source_type="helm").

Shells out to the `helm` CLI, same as KubernetesProvider shells out to
`kubectl`. Every call runs in its own temporary HELM_CONFIG_HOME/
HELM_CACHE_HOME/HELM_DATA_HOME, so a repo added or a registry login made for
one deploy never leaks into another (or onto disk past the call), and
passwords go in over stdin, never on the command line.

The cluster-side operations (upgrade --install, uninstall, status, restart)
live on KubernetesProvider; this module holds what they share and the one
call that needs no cluster (show values).
"""
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

import yaml

from app.utils.crypto import decrypt, encrypt

HELM_PATH = os.environ.get("HELM_PATH", "helm")
REPO_TIMEOUT_SECONDS = 60
# Alias the chart repo is added under inside the throwaway HELM_*_HOME.
REPO_ALIAS = "masimple"

# Kubernetes name rules: a release name becomes part of resource names and
# labels (Helm caps it at 53); a namespace is a DNS-1123 label (63).
RELEASE_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
RELEASE_NAME_MAX = 53
NAMESPACE_MAX = 63

# Uploaded chart archives live in Postgres (HelmChartUpload.data).
MAX_CHART_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_CHART_YAML_BYTES = 1024 * 1024


class HelmError(RuntimeError):
    pass


class ChartArchiveError(ValueError):
    """An uploaded file that isn't a usable packaged chart."""


@dataclass
class HelmChart:
    repo: str  # https://... chart repo, or oci://registry/path
    name: str
    version: str
    username: str = ""
    password: str = ""
    archive: bytes = None  # an uploaded .tgz instead of a repo

    @property
    def is_oci(self):
        return self.repo.startswith("oci://")

    @classmethod
    def for_manifest(cls, manifest):
        if manifest.helm_chart_source == "upload":
            upload = manifest.selected_chart_upload
            if upload is None:
                raise HelmError(
                    f"Manifest '{manifest.name}' has no uploaded chart for version {manifest.helm_chart_version}."
                )
            return cls(repo="", name=upload.chart_name, version=upload.chart_version, archive=upload.data)
        credentials = decode_credentials(manifest.encrypted_helm_repo_credentials)
        return cls(
            repo=(manifest.helm_chart_repo or "").strip(),
            name=(manifest.helm_chart_name or "").strip(),
            version=(manifest.helm_chart_version or "").strip(),
            username=credentials.get("username", ""),
            password=credentials.get("password", ""),
        )

    def label(self):
        return f"{self.name}@{self.version}"

    def source_label(self):
        return "uploaded chart" if self.archive is not None else self.repo


def encode_credentials(username, password):
    if not username:
        return None
    return encrypt(json.dumps({"username": username, "password": password or ""}))


def decode_credentials(encrypted):
    if not encrypted:
        return {}
    return json.loads(decrypt(encrypted))


@lru_cache(maxsize=1)
def rollback_on_failure_flag():
    """Helm 4 renamed `--atomic` to `--rollback-on-failure`. Asked once per
    process; anything that doesn't report v3 gets the new name."""
    try:
        version = subprocess.run(
            [HELM_PATH, "version", "--template", "{{.Version}}"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        version = ""
    return "--atomic" if version.startswith("v3") else "--rollback-on-failure"


@contextmanager
def helm_session(kubeconfig=None):
    """Yields run(args, input_text=None, timeout=...) -> CompletedProcess,
    plus the session's temp dir (for writing a values file)."""
    with tempfile.TemporaryDirectory() as home:
        env = {
            **os.environ,
            "HELM_CONFIG_HOME": os.path.join(home, "config"),
            "HELM_CACHE_HOME": os.path.join(home, "cache"),
            "HELM_DATA_HOME": os.path.join(home, "data"),
        }
        if kubeconfig:
            kubeconfig_path = os.path.join(home, "kubeconfig.yaml")
            with open(kubeconfig_path, "w") as kubeconfig_file:
                kubeconfig_file.write(kubeconfig)
            env["KUBECONFIG"] = kubeconfig_path

        def run(args, input_text=None, timeout=REPO_TIMEOUT_SECONDS):
            return subprocess.run(
                [HELM_PATH, *args], input=input_text, capture_output=True, text=True, env=env, timeout=timeout
            )

        yield run, home


def prepare_chart(run, chart, home):
    """Makes `chart` reachable inside this session (the uploaded archive
    written into it, a repo add, or a registry login for a private OCI
    registry) and returns the chart reference to pass to helm. Raises
    HelmError with helm's own message on failure."""
    if chart.archive is not None:
        path = os.path.join(home, "chart.tgz")
        with open(path, "wb") as archive_file:
            archive_file.write(chart.archive)
        return path

    if chart.is_oci:
        if chart.username:
            host = urlparse(chart.repo).netloc
            process = run(
                ["registry", "login", host, "--username", chart.username, "--password-stdin"],
                input_text=chart.password,
            )
            if process.returncode != 0:
                raise HelmError(f"Registry login to {host} failed: {process.stderr.strip()}")
        return f"{chart.repo.rstrip('/')}/{chart.name}"

    args = ["repo", "add", REPO_ALIAS, chart.repo]
    if chart.username:
        args += ["--username", chart.username, "--password-stdin"]
    process = run(args, input_text=chart.password if chart.username else None)
    if process.returncode != 0:
        raise HelmError(f"Could not add chart repository {chart.repo}: {process.stderr.strip()}")
    return f"{REPO_ALIAS}/{chart.name}"


def show_values(chart):
    """The chart's own default values.yaml, for the manifest form's
    "Load chart defaults" button. Needs no cluster."""
    try:
        with helm_session() as (run, home):
            ref = prepare_chart(run, chart, home)
            args = ["show", "values", ref]
            if chart.archive is None:
                args += ["--version", chart.version]
            process = run(args)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HelmError(str(exc)) from None
    if process.returncode != 0:
        raise HelmError(process.stderr.strip() or "helm show values failed.")
    return process.stdout


def live_status_from_json(status_json):
    """`helm status -o json` -> this app's "live"/"missing"/"unknown"."""
    try:
        status = (json.loads(status_json).get("info") or {}).get("status")
    except (ValueError, AttributeError):
        return "unknown"
    if status == "deployed":
        return "live"
    if status in ("uninstalled", "uninstalling"):
        return "missing"
    return "unknown"  # failed / pending-* / superseded: resources may or may not be there


def read_chart_archive(data):
    """Checks an uploaded file is a packaged chart (`helm package` output)
    and returns {"name", "version", "sha256"} from its Chart.yaml. Reads the
    archive in memory and never extracts it to disk. Dependencies declared
    in Chart.yaml must be packaged inside (charts/), since nothing is
    downloaded at deploy time. Raises ChartArchiveError with a message for
    the person uploading."""
    if not data:
        raise ChartArchiveError("The file is empty.")
    if len(data) > MAX_CHART_UPLOAD_BYTES:
        raise ChartArchiveError(f"The chart is larger than {MAX_CHART_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            chart_files = [m for m in members if m.isfile() and m.name.count("/") == 1 and m.name.endswith("/Chart.yaml")]
            if len(chart_files) != 1:
                raise ChartArchiveError(
                    "This isn't a packaged Helm chart: expected one chart folder with a Chart.yaml. "
                    "Create the file with `helm package <chart-folder>`."
                )
            if chart_files[0].size > MAX_CHART_YAML_BYTES:
                raise ChartArchiveError("Chart.yaml is too large.")
            chart_yaml = yaml.safe_load(archive.extractfile(chart_files[0]).read()) or {}
    except (tarfile.TarError, OSError, EOFError):
        raise ChartArchiveError("This isn't a .tgz file. Create one with `helm package <chart-folder>`.") from None
    except yaml.YAMLError:
        raise ChartArchiveError("The chart's Chart.yaml isn't valid YAML.") from None

    if not isinstance(chart_yaml, dict) or not chart_yaml.get("name") or not chart_yaml.get("version"):
        raise ChartArchiveError("The chart's Chart.yaml needs a name and a version.")

    top = chart_files[0].name.split("/")[0]
    packaged = {m.name for m in members}
    missing = []
    for dependency in chart_yaml.get("dependencies") or []:
        dep_name = (dependency or {}).get("name") if isinstance(dependency, dict) else None
        if dep_name and not any(name.startswith(f"{top}/charts/{dep_name}") for name in packaged):
            missing.append(dep_name)
    if missing:
        raise ChartArchiveError(
            f"Dependencies missing from the archive: {', '.join(missing)}. Run `helm dependency update` "
            "before `helm package` so they're included."
        )

    return {
        "name": str(chart_yaml["name"]),
        "version": str(chart_yaml["version"]),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
