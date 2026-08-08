import re

from app.models import ImageBuild

DEFAULT_PLACEHOLDER_KEY = "default"

# {{SYS:VERSION}} (group 1 unmatched -> DEFAULT_PLACEHOLDER_KEY) or
# {{SYS:VERSION:some_key}} (group 1 -> "some_key").
PLACEHOLDER_PATTERN = re.compile(r"\{\{SYS:VERSION(?::([A-Za-z0-9_.-]+))?\}\}")


class UnresolvedPlaceholderError(RuntimeError):
    """Raised when a manifest's YAML references a placeholder key with no
    matching DeploymentManifestVersionBinding, or a bound Builder has no
    successful ImageBuild yet to resolve "latest" against.
    """


def find_placeholder_keys(yaml_content):
    """Every distinct placeholder key referenced in `yaml_content`, in the
    order first seen — powers the manifest setup form's binding picker (one
    row per key actually used in the YAML).
    """
    keys = []
    seen = set()
    for match in PLACEHOLDER_PATTERN.finditer(yaml_content):
        key = match.group(1) or DEFAULT_PLACEHOLDER_KEY
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _latest_successful_build(builder_id):
    return (
        ImageBuild.query.filter_by(builder_id=builder_id, status="success")
        .order_by(ImageBuild.created_at.desc())
        .first()
    )


def resolve_manifest(manifest):
    """Renders manifest.yaml_content with every {{SYS:VERSION}} /
    {{SYS:VERSION:key}} placeholder substituted for a real image tag,
    resolved against the manifest's DeploymentManifestVersionBinding rows.

    Must be called at execution time (inside the deploy worker), never at
    save time, so "latest" always means latest-at-deploy.

    Returns (rendered_yaml, resolved_versions), where resolved_versions is a
    {placeholder_key: image_tag} dict covering only the keys actually
    referenced in the YAML — used both to populate
    DeploymentExecution.resolved_version_string and to preview the
    deploy-trigger confirmation modal before a deploy is actually triggered.

    Raises UnresolvedPlaceholderError (never a bare KeyError/AttributeError)
    if a referenced key has no binding, or a binding's builder has no
    successful build to resolve "latest" against.
    """
    bindings_by_key = {binding.placeholder_key: binding for binding in manifest.version_bindings}
    resolved_versions = {}

    def _substitute(match):
        key = match.group(1) or DEFAULT_PLACEHOLDER_KEY
        if key in resolved_versions:
            return resolved_versions[key]

        binding = bindings_by_key.get(key)
        if binding is None:
            raise UnresolvedPlaceholderError(
                f"Manifest '{manifest.name}' has no version binding for placeholder key '{key}'."
            )

        if binding.pinned_image_build_id is not None:
            image_build = binding.pinned_image_build
        else:
            image_build = _latest_successful_build(binding.builder_id)

        if image_build is None or not image_build.image_tag:
            raise UnresolvedPlaceholderError(
                f"Manifest '{manifest.name}' placeholder key '{key}' is bound to builder "
                f"'{binding.builder.name}', which has no successful build to resolve against."
            )

        resolved_versions[key] = image_build.image_tag
        return image_build.image_tag

    rendered_yaml = PLACEHOLDER_PATTERN.sub(_substitute, manifest.yaml_content)
    return rendered_yaml, resolved_versions
