import json
import re

from app.models import ChangeType, Object
from app.services.ai.factory import default_provider_type, get_ai_provider

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _build_prompt(commit_messages, existing_objects, existing_change_types, additional_description):
    commits_text = "\n".join(f"- {message}" for message in commit_messages)
    return (
        "You are helping fill in a software build's release metadata from its git "
        "commit messages. Respond with ONLY a single JSON object (no prose, no markdown "
        "fences) matching exactly this shape:\n"
        '{"matched_objects": [], "new_objects": [], "change_type": null, "description": ""}\n\n'
        "- matched_objects: existing Object names (copied exactly from the list below) "
        "that these commits relate to.\n"
        "- new_objects: short new Object name(s) (1-4 words each) for anything these "
        "commits are about that isn't covered by an existing Object. Empty array if "
        "everything is already covered.\n"
        "- change_type: the single best-fitting name copied exactly from the existing "
        "Change Types list below, or null if none fit.\n"
        "- description: a concise 1-3 sentence draft summary of what changed, suitable "
        "as a release note.\n\n"
        f"Existing Objects: {', '.join(existing_objects) or '(none yet)'}\n"
        f"Existing Change Types: {', '.join(existing_change_types) or '(none yet)'}\n"
        f"Additional notes from whoever is triggering this build: {additional_description or '(none)'}\n\n"
        f"Commit messages:\n{commits_text}\n"
    )


def _empty_result():
    return {"matched_object_names": [], "new_object_names": [], "change_type_name": None, "description": ""}


def _parse_response(raw, existing_objects, existing_change_types):
    if not raw:
        return _empty_result()
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        return _empty_result()
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return _empty_result()
    if not isinstance(data, dict):
        return _empty_result()

    existing_objects_lower = {name.lower(): name for name in existing_objects}
    matched = []
    for name in data.get("matched_objects") or []:
        canonical = existing_objects_lower.get(str(name).strip().lower())
        if canonical and canonical not in matched:
            matched.append(canonical)

    new_names = []
    for name in data.get("new_objects") or []:
        name = str(name).strip()
        # The AI doesn't always follow "only put unmatched names here" —
        # anything it proposed as "new" that actually matches an existing
        # name is folded into matched instead of creating a near-duplicate.
        canonical = existing_objects_lower.get(name.lower())
        if canonical:
            if canonical not in matched:
                matched.append(canonical)
        elif name and name not in new_names:
            new_names.append(name)

    change_types_lower = {name.lower(): name for name in existing_change_types}
    change_type_raw = data.get("change_type")
    change_type_name = change_types_lower.get(str(change_type_raw).strip().lower()) if change_type_raw else None

    return {
        "matched_object_names": matched,
        "new_object_names": new_names,
        "change_type_name": change_type_name,
        "description": str(data.get("description") or "").strip(),
    }


def suggest_metadata(commit_messages, additional_description=None):
    """Best-effort AI draft of Object(s)/Change Type/Description from a list
    of commit messages, for the build-trigger modal's "Preview from Git".

    Every field returned here is just a pre-fill for the modal's real
    fields — still fully editable, and nothing is submitted until the user
    hits "Build" — same "never save raw AI output unseen" philosophy as the
    documentation page's own AI-assist panel. Uses its own small structured-
    JSON prompt built in code rather than the PromptTemplate singleton: that
    template is scoped to the single free-text `ai_description` field, not
    several distinct fields parsed back reliably.

    Never raises — returns an all-empty result if there's no AI provider
    configured, or the call/parse fails for any reason, so the modal just
    falls back to blank fields rather than blocking on a broken AI setup.
    """
    if not commit_messages:
        return _empty_result()

    existing_objects = [obj.name for obj in Object.query.filter_by(is_active=True).order_by(Object.name).all()]
    existing_change_types = [
        ct.name for ct in ChangeType.query.filter_by(is_active=True).order_by(ChangeType.name).all()
    ]

    try:
        provider_type = default_provider_type()
        prompt = _build_prompt(commit_messages, existing_objects, existing_change_types, additional_description)
        raw = get_ai_provider(provider_type).generate_description(prompt)
    except Exception:
        return _empty_result()

    return _parse_response(raw, existing_objects, existing_change_types)
