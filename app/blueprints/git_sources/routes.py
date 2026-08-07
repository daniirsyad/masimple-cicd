import os
import shutil
import uuid
from datetime import datetime

from flask import current_app, flash, redirect, render_template, request, url_for

from app.blueprints.git_sources import git_sources_bp
from app.blueprints.git_sources.forms import GitSourceForm
from app.extensions import db
from app.models import Builder, GitSource, ImageBuild, Repository
from app.services.git.helpers import provider_for_git_source
from app.utils.crypto import encrypt
from app.utils.decorators import permission_required
from app.utils.error_logger import log_error
from app.utils.logger import log_activity

CREATE_CONNECTION_PREFIX = "create-connection-"


def _edit_prefix(source_id):
    return f"connection-{source_id}-"


def _repo_clone_root():
    return current_app.config["REPO_CLONE_ROOT"]


def _provider_for(git_source):
    return provider_for_git_source(git_source)


def _list_repos_for(git_source):
    """Live list_repos() call for one connection, tolerant of a bad/expired
    token so one broken connection doesn't take down the whole page.
    """
    try:
        return _provider_for(git_source).list_repos(), None
    except Exception as exc:
        log_error(
            source="git_sources.list_repos",
            exc=exc,
            description=f"Failed to list repositories for connection '{git_source.name}': {exc}",
        )
        return [], str(exc)


def _parse_uuid(value):
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _render_index(create_form=None, open_modal=None, invalid_edit=None):
    if create_form is None:
        create_form = GitSourceForm(prefix=CREATE_CONNECTION_PREFIX)

    sources = GitSource.query.order_by(GitSource.name).all()
    registered_full_names = {(repo.git_source_id, repo.full_name) for repo in Repository.query.all()}

    invalid_id, invalid_form = invalid_edit or (None, None)

    connections = []
    for source in sources:
        available, list_error = _list_repos_for(source)
        unregistered = [
            repo for repo in available if (source.id, repo["full_name"]) not in registered_full_names
        ]

        if source.id == invalid_id:
            edit_form = invalid_form
        else:
            edit_form = GitSourceForm(obj=source, prefix=_edit_prefix(source.id))
            edit_form.token.data = ""  # never re-populate the encrypted value into the form

        connections.append(
            {
                "source": source,
                "unregistered_repos": unregistered,
                "list_error": list_error,
                "repositories": Repository.query.filter_by(git_source_id=source.id)
                .order_by(Repository.full_name)
                .all(),
                "edit_form": edit_form,
            }
        )

    return render_template(
        "git_sources/index.html",
        connections=connections,
        create_form=create_form,
        open_modal=open_modal,
    )


@git_sources_bp.route("/")
@permission_required("gitsource.manage")
def index():
    return _render_index()


@git_sources_bp.route("/connections/create", methods=["POST"])
@permission_required("gitsource.manage")
def create_connection():
    form = GitSourceForm(prefix=CREATE_CONNECTION_PREFIX)

    if form.validate_on_submit():
        if not form.token.data:
            flash("A token is required to add a new connection.", "error")
            return _render_index(create_form=form, open_modal="create-connection-modal")

        source = GitSource(
            name=form.name.data,
            provider_type="github",
            encrypted_token=encrypt(form.token.data),
        )
        db.session.add(source)
        db.session.commit()

        log_activity(
            action="CREATE_GIT_SOURCE",
            target_type="git_source",
            target_id=str(source.id),
            description=f"Added GitHub connection '{source.name}'",
        )

        flash(f"Connection '{source.name}' added.", "success")
        return redirect(url_for("git_sources.index"))

    return _render_index(create_form=form, open_modal="create-connection-modal")


@git_sources_bp.route("/connections/<uuid:source_id>/edit", methods=["POST"])
@permission_required("gitsource.manage")
def edit_connection(source_id):
    source = GitSource.query.get_or_404(source_id)
    form = GitSourceForm(prefix=_edit_prefix(source_id))

    if form.validate_on_submit():
        source.name = form.name.data
        if form.token.data:
            source.encrypted_token = encrypt(form.token.data)
        db.session.commit()

        log_activity(
            action="UPDATE_GIT_SOURCE",
            target_type="git_source",
            target_id=str(source.id),
            description=f"Updated GitHub connection '{source.name}'",
        )

        flash(f"Connection '{source.name}' updated.", "success")
        return redirect(url_for("git_sources.index"))

    return _render_index(open_modal=f"edit-connection-modal-{source_id}", invalid_edit=(source_id, form))


@git_sources_bp.route("/connections/<uuid:source_id>/delete", methods=["POST"])
@permission_required("gitsource.manage")
def delete_connection(source_id):
    source = GitSource.query.get_or_404(source_id)

    repo_count = Repository.query.filter_by(git_source_id=source.id).count()
    if repo_count:
        flash(
            f"Cannot delete '{source.name}' — {repo_count} repositor"
            f"{'y is' if repo_count == 1 else 'ies are'} still registered under it. Remove "
            "them first.",
            "error",
        )
        return redirect(url_for("git_sources.index"))

    name = source.name
    source_id_str = str(source.id)
    db.session.delete(source)
    db.session.commit()

    log_activity(
        action="DELETE_GIT_SOURCE",
        target_type="git_source",
        target_id=source_id_str,
        description=f"Deleted GitHub connection '{name}'",
    )

    flash(f"Connection '{name}' deleted.", "success")
    return redirect(url_for("git_sources.index"))


@git_sources_bp.route("/repos/register", methods=["POST"])
@permission_required("gitsource.manage")
def register_repo():
    source_id = _parse_uuid(request.form.get("git_source_id"))
    if source_id is None:
        flash("Invalid connection.", "error")
        return redirect(url_for("git_sources.index"))
    source = GitSource.query.get_or_404(source_id)

    full_name = (request.form.get("full_name") or "").strip()
    if not full_name:
        flash("Missing repository name.", "error")
        return redirect(url_for("git_sources.index"))

    existing = Repository.query.filter_by(git_source_id=source.id, full_name=full_name).first()
    if existing is not None:
        flash(f"'{full_name}' is already registered.", "error")
        return redirect(url_for("git_sources.index"))

    repository = Repository(
        git_source_id=source.id,
        full_name=full_name,
        local_path="",
        status="cloning",
    )
    db.session.add(repository)
    db.session.flush()  # assign repository.id so the clone path can use it
    os.makedirs(_repo_clone_root(), exist_ok=True)
    repository.local_path = os.path.join(_repo_clone_root(), str(repository.id))
    db.session.commit()

    try:
        default_branch = _provider_for(source).clone_repo(full_name, repository.local_path)
    except Exception as exc:
        repository.status = "error"
        db.session.commit()
        log_error(
            source="git_sources.register_repo",
            exc=exc,
            description=f"Failed to register '{full_name}' under connection '{source.name}': {exc}",
        )
        flash(f"Failed to register '{full_name}': {exc}", "error")
        return redirect(url_for("git_sources.index"))

    repository.status = "ready"
    repository.default_branch = default_branch
    repository.last_synced_at = datetime.utcnow()
    db.session.commit()

    log_activity(
        action="REGISTER_REPOSITORY",
        target_type="repository",
        target_id=str(repository.id),
        description=f"Registered repository '{full_name}' under connection '{source.name}'",
    )

    flash(f"Repository '{full_name}' registered and cloned.", "success")
    return redirect(url_for("git_sources.index"))


@git_sources_bp.route("/repos/<uuid:repository_id>/resync", methods=["POST"])
@permission_required("gitsource.manage")
def resync_repo(repository_id):
    repository = Repository.query.get_or_404(repository_id)

    # Re-sync must not run concurrently with a build touching the same local
    # clone. This shares the same status='running' signal the build worker
    # itself uses to serialize builds (see the partial unique index on
    # ImageBuild) — Step 10's worker rewrite reuses this exact check.
    if ImageBuild.query.filter_by(status="running").first() is not None:
        flash("A build is currently running — try Re-sync again once it finishes.", "error")
        return redirect(url_for("git_sources.index"))

    try:
        _provider_for(repository.git_source).sync_repo(
            repository.local_path, repository.default_branch, repo_name=repository.full_name
        )
    except Exception as exc:
        repository.status = "error"
        db.session.commit()
        log_error(
            source="git_sources.resync_repo",
            exc=exc,
            description=f"Re-sync failed for '{repository.full_name}': {exc}",
        )
        flash(f"Re-sync failed: {exc}", "error")
        return redirect(url_for("git_sources.index"))

    repository.status = "ready"
    repository.last_synced_at = datetime.utcnow()
    db.session.commit()

    log_activity(
        action="RESYNC_REPOSITORY",
        target_type="repository",
        target_id=str(repository.id),
        description=f"Re-synced repository '{repository.full_name}'",
    )

    flash(f"Repository '{repository.full_name}' re-synced.", "success")
    return redirect(url_for("git_sources.index"))


@git_sources_bp.route("/repos/<uuid:repository_id>/remove", methods=["POST"])
@permission_required("gitsource.manage")
def remove_repo(repository_id):
    repository = Repository.query.get_or_404(repository_id)

    builder_count = Builder.query.filter_by(repository_id=repository.id).count()
    if builder_count:
        flash(
            f"Cannot remove '{repository.full_name}' — {builder_count} Builder(s) still reference it.",
            "error",
        )
        return redirect(url_for("git_sources.index"))

    full_name = repository.full_name
    repository_id_str = str(repository.id)
    shutil.rmtree(repository.local_path, ignore_errors=True)
    db.session.delete(repository)
    db.session.commit()

    log_activity(
        action="REMOVE_REPOSITORY_REGISTRATION",
        target_type="repository",
        target_id=repository_id_str,
        description=f"Removed registration for repository '{full_name}'",
    )

    flash(f"Registration for '{full_name}' removed.", "success")
    return redirect(url_for("git_sources.index"))
