"""Shared steps for delete routes: anything can be deleted, so whatever used
the deleted item is unlinked (never deleted with it) and must be pointed at
a new one before it can run again — see Builder.missing_links().
"""
from app.models import Builder, ImageBuild
from app.services.build.worker import fail_queued_builds


def builders_using(column, item_id):
    return Builder.query.filter(column == item_id).count()


def builder_impact(count, what):
    """The delete confirm's line for an item Builders point at, or None."""
    if not count:
        return None
    return f"Used by {count} Builder(s), which will need a new {what} before they can build."


def unlink_builders(column, item_id, what, item_name):
    """Fails the queued builds of every Builder whose `column` points at the
    item being deleted, then empties that link. Returns how many Builders."""
    builder_ids = [row.id for row in Builder.query.with_entities(Builder.id).filter(column == item_id)]
    if not builder_ids:
        return 0
    fail_queued_builds(
        ImageBuild.builder_id.in_(builder_ids), f"{what} '{item_name}' was deleted before this build ran."
    )
    Builder.query.filter(Builder.id.in_(builder_ids)).update({column: None}, synchronize_session=False)
    return len(builder_ids)
