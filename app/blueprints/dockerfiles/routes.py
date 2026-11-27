from flask import flash, redirect, render_template, url_for

from app.blueprints.dockerfiles import dockerfiles_bp
from app.blueprints.dockerfiles.forms import DockerfileForm
from app.extensions import db
from app.models import Builder, Dockerfile
from app.utils.decorators import permission_required
from app.utils.deletion import builder_impact, builders_using, unlink_builders
from app.utils.logger import log_activity

CREATE_PREFIX = "create-dockerfile-"


def _edit_prefix(dockerfile_id):
    return f"dockerfile-{dockerfile_id}-"


def _render_index(create_form=None, open_modal=None, invalid_edit=None):
    if create_form is None:
        create_form = DockerfileForm(prefix=CREATE_PREFIX)

    dockerfiles = Dockerfile.query.filter_by(is_active=True).order_by(Dockerfile.name).all()

    # What deleting each one would affect, shown in its delete confirm.
    delete_impacts = {
        dockerfile.id: builder_impact(builders_using(Builder.managed_dockerfile_id, dockerfile.id), "Dockerfile")
        for dockerfile in dockerfiles
    }

    invalid_id, invalid_form = invalid_edit or (None, None)
    edit_forms = {}
    for dockerfile in dockerfiles:
        if dockerfile.id == invalid_id:
            edit_forms[dockerfile.id] = invalid_form
        else:
            edit_forms[dockerfile.id] = DockerfileForm(obj=dockerfile, prefix=_edit_prefix(dockerfile.id))

    return render_template(
        "dockerfiles/index.html",
        dockerfiles=dockerfiles,
        delete_impacts=delete_impacts,
        create_form=create_form,
        edit_forms=edit_forms,
        open_modal=open_modal,
    )


def _render_archived():
    dockerfiles = Dockerfile.query.filter_by(is_active=False).order_by(Dockerfile.name).all()
    return render_template("dockerfiles/archived.html", dockerfiles=dockerfiles)


@dockerfiles_bp.route("/")
@permission_required("dockerfile.manage")
def index():
    return _render_index()


@dockerfiles_bp.route("/create", methods=["POST"])
@permission_required("dockerfile.manage")
def create():
    form = DockerfileForm(prefix=CREATE_PREFIX)

    if form.validate_on_submit():
        dockerfile = Dockerfile(name=form.name.data, content=form.content.data)
        db.session.add(dockerfile)
        db.session.commit()

        log_activity(
            action="CREATE_DOCKERFILE",
            target_type="dockerfile",
            target_id=str(dockerfile.id),
            description=f"Added Dockerfile '{dockerfile.name}'",
        )

        flash(f"Dockerfile '{dockerfile.name}' added.", "success")
        return redirect(url_for("dockerfiles.index"))

    return _render_index(create_form=form, open_modal="create-dockerfile-modal")


@dockerfiles_bp.route("/<uuid:dockerfile_id>/edit", methods=["POST"])
@permission_required("dockerfile.manage")
def edit(dockerfile_id):
    dockerfile = Dockerfile.query.get_or_404(dockerfile_id)
    form = DockerfileForm(prefix=_edit_prefix(dockerfile_id))

    if form.validate_on_submit():
        dockerfile.name = form.name.data
        dockerfile.content = form.content.data
        db.session.commit()

        log_activity(
            action="UPDATE_DOCKERFILE",
            target_type="dockerfile",
            target_id=str(dockerfile.id),
            description=f"Updated Dockerfile '{dockerfile.name}'",
        )

        flash(f"Dockerfile '{dockerfile.name}' updated.", "success")
        return redirect(url_for("dockerfiles.index"))

    return _render_index(open_modal=f"edit-dockerfile-modal-{dockerfile_id}", invalid_edit=(dockerfile_id, form))


@dockerfiles_bp.route("/<uuid:dockerfile_id>/delete", methods=["POST"])
@permission_required("dockerfile.manage")
def delete(dockerfile_id):
    dockerfile = Dockerfile.query.get_or_404(dockerfile_id)

    name = dockerfile.name
    unlinked = unlink_builders(Builder.managed_dockerfile_id, dockerfile.id, "Dockerfile", name)
    dockerfile_id_str = str(dockerfile.id)
    db.session.delete(dockerfile)
    db.session.commit()

    log_activity(
        action="DELETE_DOCKERFILE",
        target_type="dockerfile",
        target_id=dockerfile_id_str,
        description=f"Deleted Dockerfile '{name}'" + (f" ({unlinked} Builder(s) unlinked)" if unlinked else ""),
    )

    flash(f"Dockerfile '{name}' deleted." + (f" {unlinked} Builder(s) need a new Dockerfile." if unlinked else ""), "success")
    return redirect(url_for("dockerfiles.index"))


@dockerfiles_bp.route("/archived")
@permission_required("dockerfile.manage")
def archived():
    return _render_archived()


@dockerfiles_bp.route("/<uuid:dockerfile_id>/archive", methods=["POST"])
@permission_required("dockerfile.manage")
def archive(dockerfile_id):
    dockerfile = Dockerfile.query.get_or_404(dockerfile_id)
    dockerfile.is_active = False
    db.session.commit()

    log_activity(
        action="ARCHIVE_DOCKERFILE",
        target_type="dockerfile",
        target_id=str(dockerfile.id),
        description=f"Archived Dockerfile '{dockerfile.name}'",
    )

    flash(f"'{dockerfile.name}' archived.", "info")
    return redirect(url_for("dockerfiles.index"))


@dockerfiles_bp.route("/<uuid:dockerfile_id>/restore", methods=["POST"])
@permission_required("dockerfile.manage")
def restore(dockerfile_id):
    dockerfile = Dockerfile.query.get_or_404(dockerfile_id)
    dockerfile.is_active = True
    db.session.commit()

    log_activity(
        action="RESTORE_DOCKERFILE",
        target_type="dockerfile",
        target_id=str(dockerfile.id),
        description=f"Restored Dockerfile '{dockerfile.name}'",
    )

    flash(f"'{dockerfile.name}' restored.", "success")
    return redirect(url_for("dockerfiles.archived"))
