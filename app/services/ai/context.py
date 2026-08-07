from app.services.git.helpers import provider_for_git_source


def gather_ai_context(builder_branches):
    """Branch names + concatenated commit messages for a list of (Builder,
    branch) tuples, for substitution into the AI description prompt template.

    Reads straight from each Builder's already-synced local clone via
    `get_commit_messages(local_path, since_ref=None)` — the bounded-recent-log
    fallback, since ImageBuild has no per-build commit-SHA to diff from (a
    deliberate simplification vs. the old spec's since-last-build diffing).
    A Builder whose repo can't be read (never synced, clone removed, etc.)
    is skipped rather than failing the whole batch's context gathering.

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
            messages = provider.get_commit_messages(builder.repository.local_path, since_ref=None)
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
