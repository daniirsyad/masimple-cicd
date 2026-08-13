from app.services.build.history import get_last_built_commit
from app.services.git.helpers import provider_for_git_source


def gather_ai_context(builder_branches):
    """Branch names + concatenated commit messages for a list of (Builder,
    branch) tuples, for substitution into the AI description prompt template.

    Reads straight from each Builder's already-synced local clone via
    `get_commit_messages(local_path, since_ref=<last successful build's
    commit_sha>)` — a true since-last-build diff whenever this Builder+branch
    has one recorded (see `ImageBuild.commit_sha` / `_record_commit_history`
    in the build worker); falls back to the bounded-recent-log behavior
    otherwise (first build on this branch, or a build that predates commit
    tracking). A Builder whose repo can't be read (never synced, clone
    removed, etc.) is skipped rather than failing the whole batch's context
    gathering.

    Shared by both AI-assist call sites: the build-trigger form (no
    `BuildBatch` exists yet — just the selected Builders and their chosen
    branches) and the documentation page (`gather_batch_ai_context` below,
    once a batch's `ImageBuild`s already record which branch was used).
    """
    branch_names = []
    commit_blocks = []

    for builder, branch in builder_branches:
        if branch not in branch_names:
            branch_names.append(branch)

        try:
            provider = provider_for_git_source(builder.repository.git_source)
            since_ref = get_last_built_commit(builder.id, branch)
            messages = provider.get_commit_messages(builder.repository.local_path, since_ref=since_ref)
        except Exception:
            messages = []

        if messages:
            commit_blocks.append(f"[{builder.name} @ {branch}]\n" + "\n".join(messages))

    return {
        "branch_names": ", ".join(branch_names),
        "commit_messages": "\n\n".join(commit_blocks),
    }


def gather_batch_ai_context(batch):
    """`gather_ai_context`, sourcing (Builder, branch) pairs from an already-
    created BuildBatch's ImageBuilds instead of a not-yet-submitted selection.
    """
    builder_branches = [(image_build.builder, image_build.branch_used) for image_build in batch.image_builds]
    return gather_ai_context(builder_branches)
