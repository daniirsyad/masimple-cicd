import re

_BREAKING_RE = re.compile(r"BREAKING[ -]CHANGE", re.IGNORECASE)
_CONVENTIONAL_RE = re.compile(r"^(\w+)(\([^)]*\))?(!)?:\s")
_MINOR_TYPES = {"feat", "feature"}

_SEVERITY_ORDER = {"patch": 0, "minor": 1, "major": 2}


def classify_commit(message):
    """Best-guess Conventional-Commits bump severity for one commit message:
    'major' (a `!` breaking marker or a BREAKING CHANGE footer/body), 'minor'
    (a `feat`/`feature` prefix), 'patch' (any other recognized conventional
    prefix, e.g. `fix`/`chore`/`docs`), or None if the message doesn't look
    conventional at all — the caller treats an all-None batch as "nothing to
    go on" and falls back to 'patch' rather than guessing wrong in either
    direction.
    """
    if _BREAKING_RE.search(message):
        return "major"

    first_line = message.strip().splitlines()[0] if message.strip() else ""
    match = _CONVENTIONAL_RE.match(first_line)
    if not match:
        return None
    if match.group(3) == "!":
        return "major"
    return "minor" if match.group(1).lower() in _MINOR_TYPES else "patch"


def suggest_bump_type(messages):
    """Aggregate bump type across every commit message: the single most
    severe signal found. Defaults to 'patch' (the least disruptive guess)
    when nothing looks conventional at all — Bump Type stays a mandatory,
    always-populated field at trigger time, never left blank.
    """
    severities = [classify_commit(message) for message in messages]
    severities = [s for s in severities if s is not None]
    if not severities:
        return "patch"
    return max(severities, key=lambda s: _SEVERITY_ORDER[s])
