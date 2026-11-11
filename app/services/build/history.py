from app.models import ImageBuild


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
    worker._record_commit_history). All fields are None/empty for a build
    that predates commit tracking — the template shows "not tracked", not
    an error, for those.

    Deliberately looks up the build that was previous *as of this one*
    (created_at < image_build.created_at) rather than the pair's current
    most-recent build (get_last_built_commit) — viewing documentation for
    an older batch must show the range that was true then, not one that's
    since moved on because of newer builds.
    """
    previous = (
        ImageBuild.query.filter(
            ImageBuild.builder_id == image_build.builder_id,
            ImageBuild.branch_used == image_build.branch_used,
            ImageBuild.status == "success",
            ImageBuild.created_at < image_build.created_at,
        )
        .order_by(ImageBuild.created_at.desc())
        .first()
    )
    return {
        "from_sha": previous.commit_sha if previous else None,
        "to_sha": image_build.commit_sha,
        "commits": list(image_build.commits),
    }
