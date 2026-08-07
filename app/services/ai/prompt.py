import re

from app.models import PromptTemplate

_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_template(template_text, **substitutions):
    """Substitute `{{name}}` placeholders in `template_text` from `substitutions`.

    Unknown placeholders are left as-is rather than raising, so a template
    referencing a not-yet-supported variable degrades visibly instead of crashing.
    """

    def _replace(match):
        key = match.group(1)
        if key in substitutions:
            return str(substitutions[key])
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, template_text)


def render_default_prompt(context, object_value, additional_description=None):
    """Render the active default PromptTemplate with a gathered AI context
    (see `app.services.ai.context`) plus the batch's `object` and
    `additional_description` fields — shared by both AI-assist call sites
    (the documentation page's prompt preview, the worker's automatic
    post-success generation) so the "no default template configured" and
    "inactive default template" fallbacks only live in one place.

    `additional_description` (whatever was typed in at build-trigger time)
    is exposed as `{{additional_description}}` so the AI can actually take
    it into account when drafting, instead of it only ever being appended
    after the fact (see worker._create_documentation_for_successful_batch).

    Returns "" if there's no active default template to render.
    """
    template = PromptTemplate.query.filter_by(is_default=True, is_active=True).first()
    if template is None:
        return ""
    return render_template(
        template.template_text,
        branch_names=context["branch_names"],
        object=object_value or "",
        commit_messages=context["commit_messages"],
        additional_description=additional_description or "",
    )
