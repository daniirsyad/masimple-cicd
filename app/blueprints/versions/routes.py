import uuid

from flask import flash, redirect, render_template, url_for

from app.blueprints.versions import versions_bp
from app.blueprints.versions.forms import VersionForm
from app.extensions import db
from app.models import Builder, BuildBatch, Version, VersionType
from app.models.version import version_version_links
from app.utils.decorators import permission_required
from app.utils.logger import log_activity

CREATE_PREFIX = "create-version-"


def _edit_prefix(version_id):
    return f"version-{version_id}-"


def _linked_version_choices(version_id=None, include_versions=None):
    """Active versions only (a disabled one shouldn't be newly linkable) —
    plus, if given, `include_versions` (a version's own *currently* linked
    versions) so that re-submitting its edit form doesn't fail WTForms'
    choice validation, or silently drop the link, just because one of them
    has since been disabled. Same "keep the current value selectable in its
    own edit form" fallback as builders.routes._version_choices.
    """
    query = Version.query.filter_by(is_active=True)
    if version_id is not None:
        query = query.filter(Version.id != version_id)
    choices = [(str(v.id), v.name) for v in query.order_by(Version.name).all()]
    if include_versions:
        existing_ids = {choice_id for choice_id, _ in choices}
        for version in include_versions:
            if str(version.id) not in existing_ids:
                choices.append((str(version.id), version.name))
    return choices


def _sync_linked_versions(version, selected_ids):
    """One-directional: writes only (version, other) rows for every selected
    id, after clearing `version`'s own prior outgoing links — never touches
    (other, version) rows, since those are a *different* Version's own
    outgoing links, owned and edited from that Version's own form. Linking
    QAS to DEV (from QAS's edit form) lets QAS's batches offer DEV's batches
    as Linked Batches; it does not also let DEV offer QAS's — that direction
    only exists if DEV's own picker is separately edited to include QAS.
    """
    db.session.execute(
        version_version_links.delete().where(version_version_links.c.version_id == version.id)
    )
    for raw_id in selected_ids:
        try:
            other_id = uuid.UUID(raw_id)
        except (TypeError, ValueError):
            continue
        if other_id == version.id:
            continue
        db.session.execute(version_version_links.insert().values(version_id=version.id, linked_version_id=other_id))


def _version_type_names():
    """Distinct active VersionType names, for the Version Type field's
    autocomplete datalist — the field itself is now free text, not a select,
    so typing an unseen name creates a new VersionType on save.
    """
    return [
        vt.name
        for vt in VersionType.query.filter_by(is_active=True).order_by(VersionType.name).all()
    ]


def _get_or_create_version_type(name):
    version_type = VersionType.query.filter(db.func.lower(VersionType.name) == name.lower()).first()
    if version_type is None:
        version_type = VersionType(name=name)
        db.session.add(version_type)
        db.session.flush()
    return version_type


def _latest_batch_by_version():
    """version_id -> its most recent BuildBatch (or absent if none yet).

    BuildBatch already carries version_id directly, so this is a simple
    query against BuildBatch alone — no join through ImageBuild needed (that
    join is for /builders, where a Builder has no direct FK to BuildBatch).
    """
    latest = {}
    for batch in BuildBatch.query.order_by(BuildBatch.created_at.desc()).all():
        latest.setdefault(batch.version_id, batch)
    return latest


def _render_index(create_form=None, open_modal=None, invalid_edit=None):
    if create_form is None:
        create_form = VersionForm(prefix=CREATE_PREFIX)
    create_form.linked_version_ids.choices = _linked_version_choices()

    versions = Version.query.filter_by(is_active=True).order_by(Version.name).all()
    latest_batches = _latest_batch_by_version()
    # Same guard delete_version itself checks before rejecting the request —
    # computed here so the button can be disabled up front.
    delete_reasons = {}
    for version in versions:
        builder_count = Builder.query.filter_by(version_id=version.id).count()
        delete_reasons[version.id] = f"{builder_count} Builder(s) still reference it." if builder_count else None

    invalid_id, invalid_form = invalid_edit or (None, None)
    edit_forms = {}
    for version in versions:
        if version.id == invalid_id:
            invalid_form.linked_version_ids.choices = _linked_version_choices(
                version.id, include_versions=version.linked_versions
            )
            edit_forms[version.id] = invalid_form
        else:
            form = VersionForm(obj=version, prefix=_edit_prefix(version.id))
            form.version_type.data = version.version_type.name
            form.linked_version_ids.choices = _linked_version_choices(
                version.id, include_versions=version.linked_versions
            )
            form.linked_version_ids.data = [str(v.id) for v in version.linked_versions]
            edit_forms[version.id] = form

    return render_template(
        "versions/list.html",
        versions=versions,
        latest_batches=latest_batches,
        delete_reasons=delete_reasons,
        create_form=create_form,
        edit_forms=edit_forms,
        version_type_names=_version_type_names(),
        open_modal=open_modal,
    )


def _render_archived():
    versions = Version.query.filter_by(is_active=False).order_by(Version.name).all()
    return render_template("versions/archived.html", versions=versions)


@versions_bp.route("/")
@permission_required("version.view")
def list_versions():
    return _render_index()


@versions_bp.route("/create", methods=["POST"])
@permission_required("version.manage")
def create_version():
    form = VersionForm(prefix=CREATE_PREFIX)
    form.linked_version_ids.choices = _linked_version_choices()

    if form.validate_on_submit():
        if Version.query.filter_by(name=form.name.data).first() is not None:
            flash(f"A version named '{form.name.data}' already exists.", "error")
            return _render_index(create_form=form, open_modal="create-version-modal")

        version_type = _get_or_create_version_type(form.version_type.data.strip())

        version = Version(
            name=form.name.data,
            version_type_id=version_type.id,
            major=form.major.data or 0,
            minor=form.minor.data or 0,
            patch=form.patch.data or 0,
        )
        db.session.add(version)
        db.session.flush()  # assign version.id so _sync_linked_versions can reference it
        _sync_linked_versions(version, form.linked_version_ids.data)
        db.session.commit()

        log_activity(
            action="CREATE_VERSION",
            target_type="version",
            target_id=str(version.id),
            description=f"Created version '{version.name}'",
        )

        flash(f"Version '{version.name}' created.", "success")
        return redirect(url_for("versions.list_versions"))

    return _render_index(create_form=form, open_modal="create-version-modal")


@versions_bp.route("/<uuid:version_id>/edit", methods=["POST"])
@permission_required("version.manage")
def edit_version(version_id):
    version = Version.query.get_or_404(version_id)
    form = VersionForm(prefix=_edit_prefix(version_id))
    form.linked_version_ids.choices = _linked_version_choices(version_id, include_versions=version.linked_versions)

    if form.validate_on_submit():
        duplicate = Version.query.filter(
            Version.name == form.name.data, Version.id != version.id
        ).first()
        if duplicate is not None:
            flash(f"A version named '{form.name.data}' already exists.", "error")
            return _render_index(
                open_modal=f"edit-version-modal-{version_id}", invalid_edit=(version_id, form)
            )

        version.version_type = _get_or_create_version_type(form.version_type.data.strip())
        version.name = form.name.data
        version.major = form.major.data or 0
        version.minor = form.minor.data or 0
        version.patch = form.patch.data or 0
        _sync_linked_versions(version, form.linked_version_ids.data)
        db.session.commit()

        log_activity(
            action="UPDATE_VERSION",
            target_type="version",
            target_id=str(version.id),
            description=f"Updated version '{version.name}'",
        )

        flash(f"Version '{version.name}' updated.", "success")
        return redirect(url_for("versions.list_versions"))

    return _render_index(open_modal=f"edit-version-modal-{version_id}", invalid_edit=(version_id, form))


@versions_bp.route("/<uuid:version_id>/delete", methods=["POST"])
@permission_required("version.manage")
def delete_version(version_id):
    version = Version.query.get_or_404(version_id)

    builder_count = Builder.query.filter_by(version_id=version.id).count()
    if builder_count:
        flash(
            f"Cannot delete '{version.name}' — {builder_count} Builder(s) still reference it.", "error"
        )
        return redirect(url_for("versions.list_versions"))

    name = version.name
    version_id_str = str(version.id)
    db.session.delete(version)
    db.session.commit()

    log_activity(
        action="DELETE_VERSION",
        target_type="version",
        target_id=version_id_str,
        description=f"Deleted version '{name}'",
    )

    flash(f"Version '{name}' deleted.", "success")
    return redirect(url_for("versions.list_versions"))


@versions_bp.route("/archived")
@permission_required("version.view")
def archived():
    return _render_archived()


@versions_bp.route("/<uuid:version_id>/disable", methods=["POST"])
@permission_required("version.manage")
def disable_version(version_id):
    version = Version.query.get_or_404(version_id)
    version.is_active = False
    db.session.commit()

    log_activity(
        action="DISABLE_VERSION",
        target_type="version",
        target_id=str(version.id),
        description=f"Disabled version '{version.name}'",
    )

    flash(f"'{version.name}' disabled — moved to Archived.", "info")
    return redirect(url_for("versions.list_versions"))


@versions_bp.route("/<uuid:version_id>/enable", methods=["POST"])
@permission_required("version.manage")
def enable_version(version_id):
    version = Version.query.get_or_404(version_id)
    version.is_active = True
    db.session.commit()

    log_activity(
        action="ENABLE_VERSION",
        target_type="version",
        target_id=str(version.id),
        description=f"Re-enabled version '{version.name}'",
    )

    flash(f"'{version.name}' re-enabled.", "success")
    return redirect(url_for("versions.archived"))
