from datetime import datetime

BUMP_TYPES = ("patch", "minor", "major")


def compute_next_version(major, minor, patch, bump_type):
    """Pure MAJOR.MINOR.PATCH bump — no DB access, directly unit-testable.

    Bumping major resets minor and patch to 0; bumping minor resets patch to 0
    (major unchanged); bumping patch only increments patch. Applied exactly
    once per BuildBatch, never once per image in the batch.
    """
    if bump_type == "major":
        return major + 1, 0, 0
    if bump_type == "minor":
        return major, minor + 1, 0
    if bump_type == "patch":
        return major, minor, patch + 1
    raise ValueError(f"Unknown bump type: {bump_type!r}. Expected one of {BUMP_TYPES}.")


def format_datetime_string(dt):
    """%d%m%y%H%M%S — 12 digits, e.g. 220726105433 for 22 July 2026, 10:54:33.

    Generated once per batch. Seconds precision is enough for uniqueness since
    the concurrency queue only ever runs one build (and therefore one batch
    creation) at a time system-wide.
    """
    return dt.strftime("%d%m%y%H%M%S")


def build_full_version_string(version_type_name, major, minor, patch, datetime_string):
    """Pure string assembly — {TYPE}.{MAJOR}.{MINOR}.{PATCH}.{UNIQUE_DATETIME}."""
    return f"{version_type_name}.{major}.{minor}.{patch}.{datetime_string}"


def bump_version(version, bump_type, now=None):
    """Mutates `version` (a Version model instance) in place: applies the bump
    to its own major/minor/patch — no history lookup needed, since a Version
    tracks its own running numbers directly — and returns the full version
    string this BuildBatch will share across every ImageBuild in it.

    Does not commit; the caller persists this alongside creating the
    BuildBatch/ImageBuild rows atomically.
    """
    major, minor, patch = compute_next_version(version.major, version.minor, version.patch, bump_type)
    version.major = major
    version.minor = minor
    version.patch = patch

    datetime_string = format_datetime_string(now or datetime.utcnow())
    return build_full_version_string(version.version_type.name, major, minor, patch, datetime_string)
