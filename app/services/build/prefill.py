from datetime import datetime

from app.extensions import db
from app.models import ChangeType, Object
from app.services.ai.build_prefill import suggest_metadata
from app.services.build.bump_heuristic import suggest_bump_type
from app.services.build.history import get_last_built_commit
from app.services.git.helpers import provider_for_git_source
from app.utils.error_logger import log_error
from app.utils.system_config import get_system_config


def compute_build_prefill(builder_branches, additional_description=None):
    """Syncs each (Builder, branch)'s repo, reads every commit since that
    pair's last successful build, and returns a heuristic Bump Type guess
    (Conventional Commits) plus an AI-assisted Object/Change Type/
    Description draft — the single source of truth behind both the Image
    Builder trigger modal's "Preview from Git" button
    (builders.routes.build_preview) and the Workflow build-step authoring
    modal's own auto-preview (workflows.routes.build_step_preview).

    Deliberately non-destructive: `new_object_names` are returned as raw
    strings, not created as real Object rows here — same "never save raw AI
    output unseen" rule as the rest of the AI-assist features. Only the
    caller that actually persists a batch/step (via Object.resolve()) turns
    an accepted new name into a real row.

    A git-read failure for one builder is swallowed (logged, contributes no
    commits) rather than failing the whole preview — same best-effort
    philosophy as the build worker's own commit recording
    (worker._record_commit_history).

    Returns {"bump_type", "matched_objects" (list of already-existing Object
    rows), "new_object_names" (list of str), "change_type_id" (uuid or
    None), "description", "commit_count"}.
    """
    commit_log_limit = get_system_config().commit_log_limit

    all_messages = []
    for builder, branch in builder_branches:
        if not branch:
            continue
        repository = builder.repository
        try:
            git_provider = provider_for_git_source(repository.git_source)
            git_provider.sync_repo(repository.local_path, branch, repo_name=repository.full_name)
            repository.status = "ready"
            repository.last_synced_at = datetime.utcnow()
            db.session.commit()

            since_ref = get_last_built_commit(builder.id, branch)
            commits = git_provider.get_commits(
                repository.local_path, since_ref=since_ref, until_ref=branch, limit=commit_log_limit
            )
        except Exception as exc:
            db.session.rollback()
            log_error(
                source="build_prefill.compute_build_prefill",
                exc=exc,
                description=f"Preview-from-git failed for builder '{builder.name}': {exc}",
            )
            commits = []
        all_messages.extend(commit["message"] for commit in commits)

    bump_type = suggest_bump_type(all_messages)
    metadata = suggest_metadata(all_messages, additional_description=additional_description)

    matched_objects = (
        Object.query.filter(Object.name.in_(metadata["matched_object_names"])).all()
        if metadata["matched_object_names"]
        else []
    )

    change_type_id = None
    if metadata["change_type_name"]:
        change_type = ChangeType.query.filter_by(name=metadata["change_type_name"], is_active=True).first()
        change_type_id = change_type.id if change_type else None

    return {
        "bump_type": bump_type,
        "matched_objects": matched_objects,
        "new_object_names": metadata["new_object_names"],
        "change_type_id": change_type_id,
        "description": metadata["description"],
        "commit_count": len(all_messages),
    }
