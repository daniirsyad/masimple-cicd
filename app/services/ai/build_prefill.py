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


def _name_appears_in_text(name, text):
    """Whole-name, case-insensitive match — word-boundaried so an existing
    Object/Change Type name doesn't false-positive as a substring of an
    unrelated one (e.g. "Test" inside "Test2"/"TestAI").
    """
    return re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE) is not None


def _match_existing_from_commits(commit_messages, existing_objects, existing_change_types):
    """Look for existing Object/Change Type names spelled out directly in
    the commit messages, before ever asking the AI to guess. Takes priority
    over the AI's own matched_objects/change_type guess in suggest_metadata
    below — same idea as Bump Type already being derived straight from
    commit text (bump_heuristic.suggest_bump_type) rather than the AI,
    just extended to these two fields too. Only existing names can be
    found this way; a genuinely *new* Object name still has to come from
    the AI (suggest_metadata's new_object_names), since there's nothing
    yet to match a commit message's text against.

    Returns (matched_object_names, change_type_name) — change_type_name is
    the first existing Change Type (in name order) found mentioned, or
    None if none were.
    """
    text = "\n".join(commit_messages)

    matched_objects = [name for name in existing_objects if _name_appears_in_text(name, text)]

    change_type_name = next(
        (name for name in existing_change_types if _name_appears_in_text(name, text)), None
    )

    return matched_objects, change_type_name


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
    """Draft of Object(s)/Change Type/Description from a list of commit
    messages, for the build-trigger modal's "Preview from Git".

    Object(s) and Change Type are resolved in two tiers, same priority
    order as Bump Type already uses (see bump_heuristic.suggest_bump_type):
    1. Direct match — an existing Object/Change Type name spelled out
       literally in a commit message (_match_existing_from_commits).
    2. Only for whatever tier 1 didn't find: fall back to the AI's own
       guess from _parse_response. A brand-new Object name (not yet in the
       system) can only ever come from tier 2, since there's nothing to
       text-match against.
    Description has no "spelled out in the commit" concept, so it's always
    the AI's summary (tier 2), same as before.

    Every field returned here is just a pre-fill for the modal's real
    fields — still fully editable, and nothing is submitted until the user
    hits "Build" — same "never save raw AI output unseen" philosophy as the
    documentation page's own AI-assist panel. The AI call uses its own
    small structured-JSON prompt built in code rather than the
    PromptTemplate singleton: that template is scoped to the single
    free-text `ai_description` field, not several distinct fields parsed
    back reliably.

    Never raises — a broken/unconfigured AI provider just means tier 2
    contributes nothing, so tier 1's direct matches (if any) still come
    through instead of the whole result going blank.
    """
    if not commit_messages:
        return _empty_result()

    existing_objects = [obj.name for obj in Object.query.filter_by(is_active=True).order_by(Object.name).all()]
    existing_change_types = [
        ct.name for ct in ChangeType.query.filter_by(is_active=True).order_by(ChangeType.name).all()
    ]

    direct_matched_objects, direct_change_type = _match_existing_from_commits(
        commit_messages, existing_objects, existing_change_types
    )

    try:
        provider_type = default_provider_type()
        prompt = _build_prompt(commit_messages, existing_objects, existing_change_types, additional_description)
        raw = get_ai_provider(provider_type).generate_description(prompt)
        ai_result = _parse_response(raw, existing_objects, existing_change_types)
    except Exception:
        ai_result = _empty_result()

    return {
        "matched_object_names": direct_matched_objects or ai_result["matched_object_names"],
        "new_object_names": ai_result["new_object_names"],
        "change_type_name": direct_change_type or ai_result["change_type_name"],
        "description": ai_result["description"],
    }
