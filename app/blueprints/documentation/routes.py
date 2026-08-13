import uuid
from datetime import datetime, timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.blueprints.documentation import documentation_bp
from app.blueprints.documentation.forms import DocumentationForm
from app.extensions import db
from app.models import (
    AIProviderConfig,
    BuildBatch,
    ChangeType,
    ImageBuild,
    Object,
    User,
    Version,
    VersionDocumentation,
    VersionLink,
)
from app.services.ai.context import gather_batch_ai_context
from app.services.ai.factory import default_provider_type, get_ai_provider
from app.services.ai.prompt import render_default_prompt
from app.utils.decorators import permission_required
from app.utils.error_logger import log_error
from app.utils.logger import log_activity


def _parse_uuid(value):
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _change_type_choices():
    return [("", "— None —")] + [
        (str(ct.id), ct.name)
        for ct in ChangeType.query.filter_by(is_active=True).order_by(ChangeType.name).all()
    ]


def _linked_batch_choices(batch):
    # Only successful batches ever get documented (see worker
    # ._create_documentation_for_successful_batch), so a failed/partial
    # batch's version string doesn't correspond to anything real to link to.
    #
    # Scoped to batches from the same Version (always allowed — bundling
    # several batches under one Version together needs no extra config) plus
    # batches from any Version explicitly linked to this one on the Versions
    # page (Version.linked_versions, e.g. "QAS" linked with "DEV") — not
    # every successful batch in the system regardless of Version, like
    # before this was added.
    allowed_version_ids = {batch.version_id} | {v.id for v in batch.version.linked_versions}
    return [
        (str(b.id), b.full_version_string)
        for b in BuildBatch.query.filter(
            BuildBatch.id != batch.id,
            BuildBatch.status == "success",
            BuildBatch.version_id.in_(allowed_version_ids),
        )
        .order_by(BuildBatch.created_at.desc())
        .all()
    ]


def _object_suggestions():
    return [
        {"id": str(obj.id), "name": obj.name}
        for obj in Object.query.filter_by(is_active=True).order_by(Object.name).all()
    ]


def _branches_used(batch):
    seen = []
    for image_build in batch.image_builds:
        if image_build.branch_used not in seen:
            seen.append(image_build.branch_used)
    return ", ".join(seen)


def _commit_range_for_image(image_build):
    """(from_sha, to_sha, commits) for one ImageBuild's row in "Images in
    this Batch": `to_sha` is this build's own recorded commit
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


def _rendered_prompt(batch, objects):
    object_value = ", ".join(obj.name for obj in objects)
    return render_default_prompt(gather_batch_ai_context(batch), object_value, batch.additional_description)


def _ai_provider_choices():
    return [
        (config.provider_type, config.provider_type)
        for config in AIProviderConfig.query.filter_by(is_active=True)
        .order_by(AIProviderConfig.provider_type)
        .all()
    ]


DOC_PER_PAGE = 20


def _version_filter_choices():
    return [(str(v.id), v.name) for v in Version.query.order_by(Version.name).all()]


def _built_by_choices():
    """Only users who've actually had a batch attributed to them — not every
    user in the system — to keep this dropdown relevant."""
    user_ids = [
        row[0]
        for row in VersionDocumentation.query.with_entities(VersionDocumentation.built_by)
        .filter(VersionDocumentation.built_by.isnot(None))
        .distinct()
    ]
    return [
        (str(user.id), user.username)
        for user in User.query.filter(User.id.in_(user_ids)).order_by(User.username).all()
    ]


def _documented_batches_query(filters):
    """Every batch that's ever had documentation auto-created for it — i.e.
    every fully successful batch (see worker._create_documentation_for_successful_batch,
    the only place a VersionDocumentation row is ever created) — narrowed by
    whichever of the /documentation filters were supplied.

    Every successful batch always has exactly one VersionDocumentation row,
    created atomically alongside the status flip to "success" (see
    _update_batch_status), so an inner join here is always safe.
    """
    query = BuildBatch.query.join(VersionDocumentation).filter(BuildBatch.status == "success")

    if filters.get("version_id"):
        query = query.filter(BuildBatch.version_id == filters["version_id"])

    if filters.get("change_type_id"):
        query = query.filter(VersionDocumentation.change_type_id == filters["change_type_id"])

    if filters.get("built_by"):
        query = query.filter(VersionDocumentation.built_by == filters["built_by"])

    status = filters.get("status")
    if status == "documented":
        query = query.filter(VersionDocumentation.change_type_id.isnot(None))
    elif status == "pending":
        query = query.filter(VersionDocumentation.change_type_id.is_(None))

    if filters.get("object_ids"):
        query = query.filter(VersionDocumentation.objects.any(Object.id.in_(filters["object_ids"])))

    if filters.get("version_string"):
        query = query.filter(BuildBatch.full_version_string.ilike(f"%{filters['version_string']}%"))

    if filters.get("date_from"):
        query = query.filter(BuildBatch.created_at >= filters["date_from"])

    if filters.get("date_to"):
        query = query.filter(BuildBatch.created_at < filters["date_to"] + timedelta(days=1))

    return query.order_by(BuildBatch.created_at.desc())


def _render_view(batch, doc, form=None):
    if form is None:
        form = DocumentationForm(obj=doc)
        form.change_type_id.data = str(doc.change_type_id) if doc.change_type_id else ""
        form.linked_batch_ids.data = [
            str(link.linked_batch_id)
            for link in VersionLink.query.filter_by(batch_id=batch.id).all()
        ]

    form.change_type_id.choices = _change_type_choices()
    form.linked_batch_ids.choices = _linked_batch_choices(batch)

    return render_template(
        "documentation/view.html",
        batch=batch,
        doc=doc,
        form=form,
        branches_used=_branches_used(batch),
        object_suggestions=_object_suggestions(),
        doc_objects=[{"id": str(obj.id), "name": obj.name} for obj in doc.objects],
        rendered_prompt=_rendered_prompt(batch, doc.objects),
        provider_choices=_ai_provider_choices(),
        default_provider_type=default_provider_type(),
        commit_ranges={str(image.id): _commit_range_for_image(image) for image in batch.image_builds},
    )


@documentation_bp.route("/")
@permission_required("documentation.edit")
def list_documentation():
    version_id = _parse_uuid(request.args.get("version_id") or "")
    change_type_id = _parse_uuid(request.args.get("change_type_id") or "")
    built_by = _parse_uuid(request.args.get("built_by") or "")
    object_ids = [oid for oid in (_parse_uuid(raw) for raw in request.args.getlist("object_id")) if oid is not None]
    version_string = (request.args.get("version_string") or "").strip()
    status = request.args.get("status") or ""

    date_from_raw = request.args.get("date_from") or ""
    date_from = None
    if date_from_raw:
        try:
            date_from = datetime.strptime(date_from_raw, "%Y-%m-%d")
        except ValueError:
            date_from_raw = ""

    date_to_raw = request.args.get("date_to") or ""
    date_to = None
    if date_to_raw:
        try:
            date_to = datetime.strptime(date_to_raw, "%Y-%m-%d")
        except ValueError:
            date_to_raw = ""

    filters = {
        "version_id": version_id,
        "change_type_id": change_type_id,
        "built_by": built_by,
        "object_ids": object_ids,
        "version_string": version_string,
        "status": status if status in ("documented", "pending") else "",
        "date_from": date_from,
        "date_to": date_to,
    }

    page = request.args.get("page", 1, type=int)
    pagination = _documented_batches_query(filters).paginate(page=page, per_page=DOC_PER_PAGE, error_out=False)

    selected_objects = (
        [{"id": str(obj.id), "name": obj.name} for obj in Object.query.filter(Object.id.in_(object_ids)).order_by(Object.name).all()]
        if object_ids
        else []
    )

    return render_template(
        "documentation/index.html",
        pagination=pagination,
        batches=pagination.items,
        versions=_version_filter_choices(),
        change_type_choices=_change_type_choices()[1:],  # drop the "— None —" entry, not meaningful as a filter
        built_by_choices=_built_by_choices(),
        object_suggestions=_object_suggestions(),
        selected_version_id=str(version_id) if version_id else "",
        selected_change_type_id=str(change_type_id) if change_type_id else "",
        selected_built_by=str(built_by) if built_by else "",
        selected_object_ids=[str(oid) for oid in object_ids],
        selected_objects=selected_objects,
        selected_version_string=version_string,
        selected_status=filters["status"],
        date_from=date_from_raw,
        date_to=date_to_raw,
        has_filters=any(
            [version_id, change_type_id, built_by, object_ids, version_string, filters["status"], date_from_raw, date_to_raw]
        ),
    )


@documentation_bp.route("/<uuid:batch_id>", methods=["GET", "POST"])
@permission_required("documentation.edit")
def view_documentation(batch_id):
    batch = BuildBatch.query.get_or_404(batch_id)
    doc = batch.documentation
    if doc is None:
        # Only a fully successful batch ever gets a VersionDocumentation row
        # (see worker._create_documentation_for_successful_batch) — a failed
        # or still-running batch has nothing here to view or edit.
        flash("This batch isn't documented — only fully successful batches are.", "error")
        return redirect(url_for("documentation.list_documentation"))

    if request.method == "POST":
        form = DocumentationForm()
        form.change_type_id.choices = _change_type_choices()
        form.linked_batch_ids.choices = _linked_batch_choices(batch)

        if form.validate_on_submit():
            doc.change_type_id = uuid.UUID(form.change_type_id.data) if form.change_type_id.data else None
            doc.objects = Object.resolve(
                request.form.getlist("object_ids"), request.form.getlist("new_object_names")
            )
            doc.description = form.description.data.strip() if form.description.data else None
            if form.ai_description.data:
                doc.ai_description = form.ai_description.data
                doc.ai_provider_used = form.ai_provider_used.data or None

            desired_ids = {uuid.UUID(raw) for raw in form.linked_batch_ids.data}
            existing_links = {
                link.linked_batch_id: link
                for link in VersionLink.query.filter_by(batch_id=batch.id).all()
            }
            for linked_id, link in existing_links.items():
                if linked_id not in desired_ids:
                    db.session.delete(link)
            for linked_id in desired_ids - existing_links.keys():
                db.session.add(VersionLink(batch_id=batch.id, linked_batch_id=linked_id))

            db.session.commit()

            log_activity(
                action="UPDATE_VERSION_DOCUMENTATION",
                target_type="version_documentation",
                target_id=str(doc.id),
                description=f"Updated documentation for batch {batch.full_version_string}",
            )

            flash("Documentation saved.", "success")
            return redirect(url_for("documentation.view_documentation", batch_id=batch.id))

        return _render_view(batch, doc, form=form)

    return _render_view(batch, doc)


@documentation_bp.route("/<uuid:batch_id>/generate", methods=["POST"])
@permission_required("documentation.edit")
def generate_description(batch_id):
    BuildBatch.query.get_or_404(batch_id)

    payload = request.get_json(silent=True) or {}
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Prompt cannot be empty."}), 400

    provider_type = payload.get("provider_type") or default_provider_type()

    try:
        provider = get_ai_provider(provider_type)
        description = provider.generate_description(prompt)
    except Exception as exc:
        entry = log_error(
            source="documentation.generate_description",
            exc=exc,
            description=f"AI description generation failed (provider '{provider_type}'): {exc}",
        )
        return jsonify({"error": str(exc), "error_log_url": url_for("logs.error_detail", error_id=entry.id)}), 400

    return jsonify({"description": description, "provider": provider_type})
