import re

from app.models import ImageBuild

DEFAULT_PLACEHOLDER_KEY = "default"
HELM_CHART_VERSION_KEY = "helm chart"

# {{SYS:VERSION}} / {{SYS:VERSION:some_key}}: the bound build's full image
# reference (registry/repo:tag). {{SYS:TAG}} / {{SYS:TAG:some_key}}: only its
# tag, for Helm charts that split image.repository and image.tag. Both use
# the same binding per key. Group "kind" is VERSION or TAG; group "key" is
# unmatched for the default key.
PLACEHOLDER_PATTERN = re.compile(r"\{\{SYS:(?P<kind>VERSION|TAG)(?::(?P<key>[A-Za-z0-9_.-]+))?\}\}")


def placeholder_key(match):
    return match.group("key") or DEFAULT_PLACEHOLDER_KEY


def split_image_ref(image_ref):
    """("registry:5000/org/app", "DEV.1.2.3") from "registry:5000/org/app:DEV.1.2.3".
    The tag is after the last ":" only if that comes after the last "/"
    (a registry port isn't a tag); no tag gives ("...", "")."""
    head, sep, tag = image_ref.rpartition(":")
    if not sep or "/" in tag:
        return image_ref, ""
    return head, tag


def placeholder_value(kind, image_ref):
    """What a placeholder of this kind becomes for a build's image reference."""
    return split_image_ref(image_ref)[1] if kind == "TAG" else image_ref


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
        key = placeholder_key(match)
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
    """Renders manifest.yaml_content with every {{SYS:VERSION[:key]}}
    placeholder substituted for a real image reference ({{SYS:TAG[:key]}}
    for just its tag),
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
        key = placeholder_key(match)
        if key in resolved_versions:
            return placeholder_value(match.group("kind"), resolved_versions[key])

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
        return placeholder_value(match.group("kind"), image_build.image_tag)

    rendered_yaml = PLACEHOLDER_PATTERN.sub(_substitute, manifest.yaml_content)
    if manifest.is_helm:
        # Part of the version string, so a chart version change counts as an
        # available update and shows in the deploy preview. The space keeps
        # it from ever colliding with a placeholder key.
        resolved_versions[HELM_CHART_VERSION_KEY] = f"{manifest.helm_chart_name}@{manifest.helm_chart_version}"
    return rendered_yaml, resolved_versions
