from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import ImageBuild, ImageBuildCommit


def get_last_built_commit(builder_id, branch):
    """The `since_ref` to diff new commits against for this (builder, branch)
    pair: the most recent successful ImageBuild's recorded commit_sha, or
    None if it's never been built successfully yet (or was built before
    commit tracking existed, so has no commit_sha to offer).

    Deliberately its own module rather than living in worker.py — worker.py
    imports from app.services.ai.context, and context.py needs this same
    helper, which would otherwise be a circular import.
    """
    build = (
        ImageBuild.query.filter_by(builder_id=builder_id, branch_used=branch, status="success")
        .filter(ImageBuild.commit_sha.isnot(None))
        .order_by(ImageBuild.created_at.desc())
        .first()
    )
    return build.commit_sha if build else None


def commit_range_for_image(image_build):
    """(from_sha, to_sha, commits) for one ImageBuild's row in "Images in
    this Batch" and the Images page: `to_sha` is this build's own recorded commit
    (ImageBuild.commit_sha), `from_sha` is the previous successful build's
    commit for the same (builder, branch) pair as of when *this* build ran
    (None if this was the first build on that pair), and `commits` is the
    full ImageBuildCommit list captured at build time (see
    worker._record_commit_history), and `files` the [status, path] list
    saved alongside them (None if not recorded). All fields are None/empty for a build
    that predates commit tracking — the template shows "not tracked", not
    an error, for those.

    Deliberately looks up the build that was previous *as of this one*
    (created_at < image_build.created_at) rather than the pair's current
    most-recent build (get_last_built_commit) — viewing documentation for
    an older batch must show the range that was true then, not one that's
    since moved on because of newer builds.
    """
    return commit_ranges_for_images([image_build])[image_build.id]


def commit_ranges_for_images(image_builds):
    """{image_build.id: commit_range_for_image(...)} for many builds in two
    queries (previous commit + commits), not three per build: list pages
    show dozens of them."""
    ids = [image.id for image in image_builds]
    if not ids:
        return {}

    previous = aliased(ImageBuild)
    previous_sha = (
        select(previous.commit_sha)
        .where(
            previous.builder_id == ImageBuild.builder_id,
            previous.branch_used == ImageBuild.branch_used,
            previous.status == "success",
            previous.created_at < ImageBuild.created_at,
        )
        .order_by(previous.created_at.desc())
        .limit(1)
        .correlate(ImageBuild)
        .scalar_subquery()
    )
    from_shas = dict(db.session.query(ImageBuild.id, previous_sha).filter(ImageBuild.id.in_(ids)))

    commits = {image_id: [] for image_id in ids}
    for commit in (
        ImageBuildCommit.query.filter(ImageBuildCommit.image_build_id.in_(ids))
        .order_by(ImageBuildCommit.committed_at)
    ):
        commits[commit.image_build_id].append(commit)

    return {
        image.id: {
            "from_sha": from_shas.get(image.id),
            "to_sha": image.commit_sha,
            "commits": commits[image.id],
            "files": image.changed_files,
        }
        for image in image_builds
    }
