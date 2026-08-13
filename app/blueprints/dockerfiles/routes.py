from flask import flash, redirect, render_template, url_for

from app.blueprints.dockerfiles import dockerfiles_bp
from app.blueprints.dockerfiles.forms import DockerfileForm
from app.extensions import db
from app.models import Builder, Dockerfile
from app.utils.decorators import permission_required
from app.utils.logger import log_activity

CREATE_PREFIX = "create-dockerfile-"


def _edit_prefix(dockerfile_id):
    return f"dockerfile-{dockerfile_id}-"


def _render_index(create_form=None, open_modal=None, invalid_edit=None):
    if create_form is None:
        create_form = DockerfileForm(prefix=CREATE_PREFIX)

    dockerfiles = Dockerfile.query.order_by(Dockerfile.name).all()

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
        create_form=create_form,
        edit_forms=edit_forms,
        open_modal=open_modal,
    )


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

    builder_count = Builder.query.filter_by(managed_dockerfile_id=dockerfile.id).count()
    if builder_count:
        flash(
            f"Cannot delete '{dockerfile.name}' — {builder_count} Builder(s) still reference it.", "error"
        )
        return redirect(url_for("dockerfiles.index"))

    name = dockerfile.name
    dockerfile_id_str = str(dockerfile.id)
    db.session.delete(dockerfile)
    db.session.commit()

    log_activity(
        action="DELETE_DOCKERFILE",
        target_type="dockerfile",
        target_id=dockerfile_id_str,
        description=f"Deleted Dockerfile '{name}'",
    )

    flash(f"Dockerfile '{name}' deleted.", "success")
    return redirect(url_for("dockerfiles.index"))
