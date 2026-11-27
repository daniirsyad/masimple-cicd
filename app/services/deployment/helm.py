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
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import zipfile
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
# A .zip upload is unpacked in memory to repackage it; caps against zip bombs.
MAX_ZIP_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_ZIP_FILES = 5000
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
_ZIP_JUNK_NAMES = {".DS_Store", "Thumbs.db"}


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
        raise ChartArchiveError(
            "This isn't a .tgz or .zip file. Create one with `helm package <chart-folder>`."
        ) from None
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


def zip_to_chart_tgz(data):
    """A .zip of a chart folder (or of its contents) repackaged in memory as
    the .tgz `helm package` would make: one top folder named after the chart.
    Only the chart is kept: the folder of the shallowest Chart.yaml (subcharts
    in charts/ come along), minus macOS/Windows clutter. The output is the
    same bytes for the same zip (fixed times and owners), so uploading it again
    matches the stored version's checksum. Raises ChartArchiveError.
    ponytail: .helmignore isn't applied; everything under the chart folder is kept."""
    if len(data) > MAX_CHART_UPLOAD_BYTES:
        raise ChartArchiveError(f"The chart is larger than {MAX_CHART_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ChartArchiveError("This .zip file is damaged or incomplete.") from None

    with archive:
        entries = {}
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            parts = name.split("/")
            if info.is_dir() or name.startswith("__MACOSX/") or parts[-1] in _ZIP_JUNK_NAMES:
                continue
            if name.startswith("/") or ".." in parts:
                raise ChartArchiveError(f"The .zip has an unsafe path: {info.filename}")
            entries[name] = info
        if len(entries) > MAX_ZIP_FILES:
            raise ChartArchiveError(f"The .zip has more than {MAX_ZIP_FILES} files.")
        if sum(info.file_size for info in entries.values()) > MAX_ZIP_UNPACKED_BYTES:
            raise ChartArchiveError(f"The .zip unpacks to more than {MAX_ZIP_UNPACKED_BYTES // (1024 * 1024)} MB.")

        roots = {name[: -len("Chart.yaml")] for name in entries if name.split("/")[-1] == "Chart.yaml"}
        if not roots:
            raise ChartArchiveError("No Chart.yaml in the .zip. Zip the chart's folder, the one with Chart.yaml in it.")
        depth = min(root.count("/") for root in roots)
        top = sorted(root for root in roots if root.count("/") == depth)
        if len(top) > 1:
            folders = ", ".join(root.rstrip("/") or "(top level)" for root in top)
            raise ChartArchiveError(f"The .zip holds more than one chart ({folders}). Zip one chart at a time.")
        root = top[0]

        unpacked = 0

        def read(info):
            nonlocal unpacked
            # Read no more than the cap allows, whatever the zip claims a file's size is.
            with archive.open(info) as member:
                content = member.read(MAX_ZIP_UNPACKED_BYTES - unpacked + 1)
            unpacked += len(content)
            if unpacked > MAX_ZIP_UNPACKED_BYTES:
                raise ChartArchiveError(f"The .zip unpacks to more than {MAX_ZIP_UNPACKED_BYTES // (1024 * 1024)} MB.")
            return content

        try:
            chart_yaml = yaml.safe_load(read(entries[root + "Chart.yaml"])) or {}
        except yaml.YAMLError:
            raise ChartArchiveError("The chart's Chart.yaml isn't valid YAML.") from None
        if not isinstance(chart_yaml, dict) or not chart_yaml.get("name"):
            raise ChartArchiveError("The chart's Chart.yaml needs a name and a version.")
        folder = re.sub(r"[^A-Za-z0-9._-]", "-", str(chart_yaml["name"])) or "chart"

        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
            for name in sorted(n for n in entries if n.startswith(root)):
                try:
                    content = read(entries[name])
                except (zipfile.BadZipFile, OSError, EOFError):
                    raise ChartArchiveError(f"Couldn't read {name} from the .zip; it may be damaged.") from None
                member = tarfile.TarInfo(f"{folder}/{name[len(root):]}")
                member.size, member.mode, member.mtime = len(content), 0o644, 0
                tar.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


def load_chart_upload(data):
    """(chart .tgz bytes, {"name", "version", "sha256"}) for an uploaded file:
    a .tgz as it is, a .zip repackaged first (zip_to_chart_tgz). What gets
    stored and deployed is always the .tgz. Raises ChartArchiveError."""
    if data[:4] in _ZIP_MAGIC:
        data = zip_to_chart_tgz(data)
    return data, read_chart_archive(data)
