"""Detect on servers: is a manifest's app already running on a server
(deployed outside MASIMPLE CICD), and on which version of each placeholder?

detect() only reads from the cluster. mark_detected() records what it found
as a successful run with action "detect", which every other part of the app
(the server lamps, Update, Stop, Restart, the live-status poller) already
treats like a deploy: see worker.is_currently_deployed.

Reading versions back: each placeholder in the manifest is swapped for a
unique marker, the YAML is parsed, and every string holding a marker gives a
(document, path, template) locator. The live object's value at that path is
matched against the template to pull out exactly what the placeholder stood
for: a full image reference for {{SYS:VERSION}}, a tag for {{SYS:TAG}}.
"""
import re
from datetime import datetime

import yaml

from app.extensions import db
from app.models import DeploymentExecution, DeploymentRun, ImageBuild
from app.services.deployment.helpers import KUBE_CONNECTION_TYPE, provider_for_server
from app.services.deployment.resolver import (
    HELM_CHART_VERSION_KEY,
    PLACEHOLDER_PATTERN,
    UnresolvedPlaceholderError,
    find_placeholder_keys,
    placeholder_key,
    placeholder_value,
    resolve_manifest,
    split_image_ref,
)
from app.services.deployment.worker import is_currently_deployed

MARKER_RE = re.compile(r"__MASIMPLE_(VERSION|TAG)_(\d+)__")
MAX_DIFF_CHARS = 20000
MAX_VALUE_CHANGES = 100
_MISSING = object()

MARKABLE = "found"


def _mark(content):
    """(content with each placeholder swapped for a unique marker, [(kind, key)] per marker index)."""
    slots = []

    def _substitute(match):
        slots.append((match.group("kind"), placeholder_key(match)))
        return f"__MASIMPLE_{match.group('kind')}_{len(slots) - 1}__"

    return PLACEHOLDER_PATTERN.sub(_substitute, content or ""), slots


def _locators(node, path=()):
    """(path, template) for every string under `node` holding a marker. A list
    item with a "name" (a container, a port) is addressed by that name, not
    its position, since the live object may order them differently."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _locators(value, path + (key,))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            step = ("name", item["name"]) if isinstance(item, dict) and "name" in item else index
            yield from _locators(item, path + (step,))
    elif isinstance(node, str) and MARKER_RE.search(node):
        yield path, node


def _lookup(node, path):
    for step in path:
        if isinstance(step, tuple):
            if not isinstance(node, list):
                return _MISSING
            node = next((item for item in node if isinstance(item, dict) and item.get("name") == step[1]), _MISSING)
        elif isinstance(node, dict) and step in node:
            node = node[step]
        elif isinstance(node, list) and isinstance(step, int) and step < len(node):
            node = node[step]
        else:
            return _MISSING
        if node is _MISSING:
            return _MISSING
    return node


def _extract(template, live_value):
    """{marker_index: value} pulled out of `live_value` by `template`
    ("registry/app:__MASIMPLE_TAG_0__" + "registry/app:1.2" -> {0: "1.2"});
    None if the live value doesn't have the template's shape."""
    if live_value is _MISSING or live_value is None or isinstance(live_value, (dict, list)):
        return None
    indexes = []
    pattern = ""
    last = 0
    for match in MARKER_RE.finditer(template):
        pattern += re.escape(template[last:match.start()]) + "(.+?)"
        indexes.append(int(match.group(2)))
        last = match.end()
    pattern += re.escape(template[last:])
    found = re.fullmatch(pattern, str(live_value), flags=re.DOTALL)
    if not found:
        return None
    return dict(zip(indexes, found.groups()))


def _builds_for(binding):
    if binding is None or binding.builder_id is None:
        return []
    return (
        ImageBuild.query.filter_by(builder_id=binding.builder_id, status="success")
        .filter(ImageBuild.image_tag.isnot(None))
        .order_by(ImageBuild.created_at.desc())
        .limit(500)
        .all()
    )


def _version_results(manifest, slots, marker_values, missing_state):
    """One entry per placeholder key, in the YAML's order: what's running and
    whether it's a known build of the key's bound Builder."""
    bindings = {binding.placeholder_key: binding for binding in manifest.version_bindings}
    results = []
    for key in find_placeholder_keys(manifest.yaml_content):
        seen = [(slots[i][0], marker_values[i]) for i in range(len(slots)) if slots[i][1] == key and i in marker_values]
        entry = {"key": key, "value": None, "ref": None, "label": None}
        tags = {split_image_ref(value)[1] if kind == "VERSION" else value for kind, value in seen}
        if not seen:
            entry["state"] = missing_state
        elif len(tags) > 1:
            entry["state"] = "mixed"
            entry["value"] = ", ".join(sorted(tags))
        else:
            kind, value = seen[0]
            entry["value"] = value
            builds = _builds_for(bindings.get(key))
            match = next(
                (build for build in builds if placeholder_value(kind, build.image_tag) == value),
                None,
            )
            if match is not None:
                entry["state"] = "build"
                entry["ref"] = match.image_tag
                builder_name = match.builder.name if match.builder else match.builder_label
                entry["label"] = f"{builder_name} build {split_image_ref(match.image_tag)[1]}"
            else:
                entry["state"] = "outside"
        results.append(entry)
    return results


def _version_string(versions, chart=None):
    """Same "key=value; ..." shape a deploy records, so get_available_update's
    comparison works unchanged: a known build records its image reference
    (equal to what a deploy of it records); anything else records a value no
    deploy ever produces, so Update is offered."""
    parts = []
    for entry in versions:
        if entry["state"] == "build":
            parts.append(f"{entry['key']}={entry['ref']}")
        elif entry["value"]:
            parts.append(f"{entry['key']}=outside ({entry['value']})")
        else:
            parts.append(f"{entry['key']}=outside ({entry['state']})")
    if chart:
        parts.append(f"{HELM_CHART_VERSION_KEY}={chart}")
    return "; ".join(parts) or None


def _render(marked, slots, marker_values, versions):
    """The manifest with each marker filled in with what's running (falling
    back to the key's known build, then "unknown"): what Stop later deletes
    or what the live-status check looks for. Only names matter for those."""
    refs = {entry["key"]: entry["ref"] for entry in versions}

    def _fill(match):
        index = int(match.group(2))
        if index in marker_values:
            return marker_values[index]
        kind, key = slots[index]
        return placeholder_value(kind, refs[key]) if refs.get(key) else "unknown"

    return MARKER_RE.sub(_fill, marked)


def _doc_label(doc):
    return f"{doc.get('kind')} {(doc.get('metadata') or {}).get('name')}"


def _flatten(node, prefix=""):
    if isinstance(node, dict) and node:
        flat = {}
        for key, value in node.items():
            flat.update(_flatten(value, f"{prefix}.{key}" if prefix else str(key)))
        return flat
    return {prefix: node}


def _values_changes(live_values, target_values):
    """Plain-text list of what an Update would change in a Helm release's values."""
    live, target = _flatten(live_values or {}), _flatten(target_values or {})
    lines = []
    for path in sorted(set(live) | set(target)):
        if path not in target:
            lines.append(f"- {path}: {live[path]!r} (back to the chart default)")
        elif path not in live:
            lines.append(f"+ {path}: {target[path]!r}")
        elif live[path] != target[path]:
            lines.append(f"~ {path}: {live[path]!r} -> {target[path]!r}")
    if len(lines) > MAX_VALUE_CHANGES:
        lines = lines[:MAX_VALUE_CHANGES] + [f"... and {len(lines) - MAX_VALUE_CHANGES} more"]
    return "\n".join(lines)


def _target_rendering(manifest):
    try:
        return resolve_manifest(manifest)[0]
    except UnresolvedPlaceholderError:
        return None


def _detect_yaml(manifest, provider, result):
    marked, slots = _mark(manifest.yaml_content)
    try:
        docs = [doc for doc in yaml.safe_load_all(marked) if isinstance(doc, dict) and doc.get("kind")]
    except yaml.YAMLError as exc:
        raise RuntimeError(f"The manifest's YAML can't be read: {exc}") from None
    live_objects = provider.get_live_objects(marked)

    def _live_for(doc):
        meta = doc.get("metadata") or {}
        for obj in live_objects:
            live_meta = obj.get("metadata") or {}
            if obj.get("kind") == doc.get("kind") and live_meta.get("name") == meta.get("name"):
                if not meta.get("namespace") or meta.get("namespace") == live_meta.get("namespace"):
                    return obj
        return None

    marker_values = {}
    found, missing = [], []
    for doc in docs:
        live = _live_for(doc)
        (found if live is not None else missing).append(_doc_label(doc))
        if live is None:
            continue
        for path, template in _locators(doc):
            marker_values.update(_extract(template, _lookup(live, path)) or {})

    result["found"], result["missing"] = found, missing
    if not docs or not found:
        result["state"] = "missing"
        return
    result["state"] = MARKABLE if not missing else "partial"
    versions = _version_results(manifest, slots, marker_values, "unreadable")
    result["versions"] = versions
    if result["state"] != MARKABLE:
        return

    target = _target_rendering(manifest)
    if target is None:
        result["diff_note"] = "No build to compare with yet, so Update can't be previewed."
    else:
        try:
            diff = provider.diff(target)
            result["diff"] = diff[:MAX_DIFF_CHARS] if diff else ""
        except RuntimeError as exc:
            result["diff_note"] = f"Couldn't compare: {exc}"
    result["_record"] = {
        "rendered_yaml": _render(marked, slots, marker_values, versions),
        "resolved_version_string": _version_string(versions),
    }


def _detect_helm(manifest, provider, result):
    release, namespace = manifest.helm_release_name, manifest.helm_namespace
    info = provider.helm_release_info(release, namespace)
    if info is None:
        result["state"] = "missing"
        return
    result["found"] = [f"Helm release {release} in {namespace}"]
    if info["status"] != "deployed":
        result["state"] = "unhealthy"
        result["message"] = f"The release is {info['status']}, so it isn't marked."
        return

    marked, slots = _mark(manifest.yaml_content)
    try:
        values_doc = yaml.safe_load(marked) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError(f"The values file can't be read: {exc}") from None
    marker_values = {}
    for path, template in _locators(values_doc):
        marker_values.update(_extract(template, _lookup(info["values"], path)) or {})

    live_chart_version = info["chart_version"]
    # An unreadable version records as "unknown", which never matches a
    # deploy, so Update is offered and brings it onto a known chart.
    chart = f"{manifest.helm_chart_name}@{live_chart_version or 'unknown'}"
    if not live_chart_version:
        result["chart_note"] = "Couldn't read which chart version is running. Update would install the manifest's."
    elif live_chart_version != manifest.helm_chart_version:
        result["chart_note"] = (
            f"Running chart version {live_chart_version}; the manifest has {manifest.helm_chart_version}. "
            "Update would change it."
        )

    versions = _version_results(manifest, slots, marker_values, "chart default")
    result["state"] = MARKABLE
    result["versions"] = versions
    target = _target_rendering(manifest)
    if target is None:
        result["diff_note"] = "No build to compare with yet, so Update can't be previewed."
    else:
        try:
            result["diff"] = _values_changes(info["values"], yaml.safe_load(target) or {})
        except yaml.YAMLError:
            result["diff_note"] = "The values file can't be read, so Update can't be previewed."
    result["_record"] = {
        "rendered_yaml": _render(marked, slots, marker_values, versions),
        "resolved_version_string": _version_string(versions, chart),
        "helm_release_name": release,
        "helm_namespace": namespace,
    }


def detect(manifest, server):
    """What's running for `manifest` on `server`. Read-only. "state" is one of
    found (can be marked), partial, missing, unhealthy (Helm release not
    deployed), already (MASIMPLE CICD deployed it), unsupported, error."""
    result = {
        "server_id": str(server.id),
        "server_name": server.name,
        "found": [],
        "missing": [],
        "versions": [],
        "message": None,
        "diff": None,
        "diff_note": None,
        "chart_note": None,
    }
    if is_currently_deployed(manifest.id, server.id):
        result["state"] = "already"
        return result
    if server.connection_type != KUBE_CONNECTION_TYPE:
        result["state"] = "unsupported"
        result["message"] = "A custom agent doesn't report what's running."
        return result
    try:
        provider = provider_for_server(server)
        if manifest.is_helm:
            _detect_helm(manifest, provider, result)
        else:
            _detect_yaml(manifest, provider, result)
    except (RuntimeError, NotImplementedError, ValueError) as exc:
        result["state"] = "error"
        result["message"] = str(exc)
    return result


def public(result):
    """`result` without its internal record, for JSON."""
    return {key: value for key, value in result.items() if not key.startswith("_")}


def _log_text(manifest, server, result, username):
    lines = [
        f"Detected '{manifest.name}' already running on '{server.name}' (checked by {username}).",
        "MASIMPLE CICD didn't deploy it; nothing was changed on the server.",
        f"Found: {', '.join(result['found'])}",
    ]
    if result["versions"]:
        lines.append("Versions:")
        for entry in result["versions"]:
            if entry["state"] == "build":
                lines.append(f"  {entry['key']}: {entry['label']}")
            elif entry["value"]:
                lines.append(f"  {entry['key']}: {entry['value']} ({entry['state']})")
            else:
                lines.append(f"  {entry['key']}: {entry['state']}")
    if result.get("chart_note"):
        lines.append(result["chart_note"])
    return "\n".join(lines) + "\n"


def mark_detected(pairs, user):
    """Re-checks each (manifest, server) pair (the confirm never trusts what
    the browser saw) and records the ones still found as one "detect" run.
    Returns (run or None, [(manifest, server, result)] marked, [... skipped])."""
    checked = [(manifest, server, detect(manifest, server)) for manifest, server in pairs]
    marked = [entry for entry in checked if entry[2]["state"] == MARKABLE]
    skipped = [entry for entry in checked if entry[2]["state"] != MARKABLE]
    if not marked:
        return None, marked, skipped

    groups = {manifest.group_name for manifest, _server, _result in marked}
    now = datetime.utcnow()
    run = DeploymentRun(
        group_name=groups.pop() if len(groups) == 1 else None,
        triggered_by=user.id,
        status="success",
        action="detect",
    )
    db.session.add(run)
    db.session.flush()
    for manifest, server, result in marked:
        record = result["_record"]
        db.session.add(
            DeploymentExecution(
                run_id=run.id,
                manifest_id=manifest.id,
                server_id=server.id,
                status="success",
                rendered_yaml=record["rendered_yaml"],
                resolved_version_string=record["resolved_version_string"],
                helm_release_name=record.get("helm_release_name"),
                helm_namespace=record.get("helm_namespace"),
                log=_log_text(manifest, server, result, user.username),
                live_status="live",
                live_checked_at=now,
                started_at=now,
                finished_at=now,
            )
        )
    db.session.commit()
    return run, marked, skipped
