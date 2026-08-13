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
