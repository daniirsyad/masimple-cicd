"""The manifest form's "Find images" button: every image in a manifest (a
Helm values file's image.repository/image.tag, a container's image:) that
could become a version placeholder, and the edit that makes it one.

Edits are made by character position from the YAML parser's own marks, so
the rest of the file (comments, quoting, order) is left exactly as it was.
"""
import re

import yaml

from app.models import Builder, ImageBuild
from app.services.deployment.resolver import DEFAULT_PLACEHOLDER_KEY, PLACEHOLDER_PATTERN, split_image_ref

# Path steps that say nothing about which app an image belongs to.
_GENERIC_STEPS = {"spec", "template", "containers", "initContainers", "image", "tag", "global", "values", "jobTemplate"}


def _mask(content):
    """Placeholders swapped for same-length plain text, so the YAML parses and
    every character position still matches `content`."""
    return PLACEHOLDER_PATTERN.sub(lambda match: "x" * len(match.group(0)), content)


def _scalar(node):
    return node if isinstance(node, yaml.ScalarNode) else None


def _child(mapping, name):
    for key_node, value_node in mapping.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == name:
            return value_node
    return None


def _walk(node, path, found):
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            name = key_node.value if isinstance(key_node, yaml.ScalarNode) else "?"
            if name == "image":
                if isinstance(value_node, yaml.MappingNode) and _scalar(_child(value_node, "tag")):
                    found.append(("tag", path + [name], _child(value_node, "tag"), _scalar(_child(value_node, "repository"))))
                elif _scalar(value_node):
                    found.append(("image", path + [name], value_node, None))
            _walk(value_node, path + [name], found)
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            label = None
            if isinstance(item, yaml.MappingNode) and _scalar(_child(item, "name")):
                label = _child(item, "name").value
            _walk(item, path + [label or f"[{index}]"], found)


def _suggested_key(path):
    for step in reversed(path):
        if step not in _GENERIC_STEPS and not step.startswith("["):
            return re.sub(r"[^A-Za-z0-9_.-]", "-", step).strip("-") or DEFAULT_PLACEHOLDER_KEY
    return DEFAULT_PLACEHOLDER_KEY


def _builder_repositories():
    """{builder_id: image repository of its latest successful build}."""
    repos = {}
    for builder in Builder.query.order_by(Builder.name).all():
        build = (
            ImageBuild.query.filter_by(builder_id=builder.id, status="success")
            .filter(ImageBuild.image_tag.isnot(None))
            .order_by(ImageBuild.created_at.desc())
            .first()
        )
        if build is not None:
            repos[str(builder.id)] = split_image_ref(build.image_tag)[0]
    return repos


def _same_repository(a, b):
    """"docker.io/acme/web" and "acme/web" are the same image; so is "web" vs ".../web"."""
    a, b = a.lower(), b.lower()
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def find_image_slots(content):
    """[{id, path, kind, value, key, builder_id, start, end, repo_start, repo_end}]
    for every image that isn't a placeholder yet. Raises yaml.YAMLError."""
    nodes = []
    for document in yaml.compose_all(_mask(content or "")):
        if document is not None:
            _walk(document, [], nodes)

    repos = _builder_repositories()
    slots, used_keys = [], set()
    for kind, path, node, repo_node in nodes:
        start, end = node.start_mark.index, node.end_mark.index
        if "{{SYS:" in content[start:end]:
            continue
        key = base = _suggested_key(path)
        counter = 2
        while key in used_keys:
            key, counter = f"{base}-{counter}", counter + 1
        used_keys.add(key)

        repository = repo_node.value if repo_node is not None else (split_image_ref(node.value)[0] if kind == "image" else "")
        builder_id = next(
            (builder_id for builder_id, repo in repos.items() if repository and _same_repository(repo, repository)),
            None,
        )
        slots.append(
            {
                "id": len(slots),
                "path": ".".join(path) + (".tag" if kind == "tag" else ""),
                "kind": kind,
                "value": node.value,
                "key": key,
                "builder_id": builder_id,
                "start": start,
                "end": end,
                "repo_start": repo_node.start_mark.index if repo_node is not None else None,
                "repo_end": repo_node.end_mark.index if repo_node is not None else None,
            }
        )
    return slots


def apply_image_slots(content, choices):
    """`content` with each chosen slot turned into a placeholder, and the
    bindings to add. `choices` is [{id, key, builder_id}] from the form; for a
    chart's image.tag the sibling image.repository is set to the chosen
    Builder's image too, so tag and repository always match."""
    slots = {slot["id"]: slot for slot in find_image_slots(content)}
    repos = _builder_repositories()
    edits, bindings, keys = [], [], set()
    for choice in choices:
        slot = slots.get(choice.get("id"))
        key = (choice.get("key") or "").strip() or DEFAULT_PLACEHOLDER_KEY
        if slot is None or not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
            continue
        suffix = "" if key == DEFAULT_PLACEHOLDER_KEY else f":{key}"
        token = f'"{{{{SYS:{"TAG" if slot["kind"] == "tag" else "VERSION"}{suffix}}}}}"'
        # An empty value (`tag:`) has no characters to replace; keep a space after the colon.
        edits.append((slot["start"], slot["end"], token if slot["end"] > slot["start"] else " " + token))
        builder_id = choice.get("builder_id") or None
        if slot["kind"] == "tag" and slot["repo_start"] is not None and builder_id in repos:
            edits.append((slot["repo_start"], slot["repo_end"], f'"{repos[builder_id]}"'))
        if key not in keys:
            keys.add(key)
            bindings.append({"key": key, "builder_id": builder_id})
    for start, end, text in sorted(edits, key=lambda edit: edit[0], reverse=True):
        content = content[:start] + text + content[end:]
    return content, bindings
