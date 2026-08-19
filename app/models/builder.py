import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db

builder_roles = db.Table(
    "builder_roles",
    db.Column("builder_id", UUID(as_uuid=True), db.ForeignKey("builders.id"), primary_key=True),
    db.Column("role_id", UUID(as_uuid=True), db.ForeignKey("roles.id"), primary_key=True),
)


class Builder(db.Model):
    __tablename__ = "builders"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, nullable=False)
    version_id = db.Column(UUID(as_uuid=True), db.ForeignKey("versions.id"), nullable=False)
    repository_id = db.Column(UUID(as_uuid=True), db.ForeignKey("repositories.id"), nullable=False)
    # This Builder's own default branch — may differ from the Repository's own
    # default_branch (e.g. a Builder that always builds "release" even though
    # the repo's default is "main").
    default_branch = db.Column(db.String, nullable=True)
    # Purely organizational — powers the /builders page's "Build Group"
    # button grouping (see builders.routes._builder_groups). Independent of
    # version_id: a group's members aren't required to share a Version, but
    # POST /builders/build still rejects a build request that mixes them.
    group_name = db.Column(db.String, nullable=True)
    # Overrides the auto-derived Docker repository name (which is otherwise
    # just the last path segment of the git repo's full_name — see
    # worker._derive_image_name) — e.g. to push under a different name than
    # the source repo, or include an explicit registry namespace ("org/name").
    image_name = db.Column(db.String, nullable=True)
    # "repo" (default, existing behavior): dockerfile_path is a path inside
    # the cloned repository. "managed": build from managed_dockerfile's
    # content instead — dockerfile_path is ignored in that case, kept
    # populated with its "Dockerfile" default so switching back to "repo"
    # doesn't land on an empty path.
    dockerfile_source = db.Column(db.String, default="repo", nullable=False)
    dockerfile_path = db.Column(db.String, default="Dockerfile", nullable=False)
    managed_dockerfile_id = db.Column(UUID(as_uuid=True), db.ForeignKey("dockerfiles.id"), nullable=True)
    registry_target_id = db.Column(UUID(as_uuid=True), db.ForeignKey("registry_targets.id"), nullable=False)
    default_build_args = db.Column(db.JSON, nullable=True)
    # Disabled (archived) Builders drop off the main list onto a separate
    # Archived page, and stop being buildable/selectable — see
    # builders.routes.disable/enable/build, and
    # app/services/workflow/resolver.py's resolve_builders_from_selection(),
    # which drops a disabled Builder from group resolution on every future
    # workflow run, not just new step authoring.
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    version = db.relationship("Version", backref=db.backref("builders", lazy="dynamic"))
    repository = db.relationship("Repository", backref=db.backref("builders", lazy="dynamic"))
    registry_target = db.relationship("RegistryTarget")
    managed_dockerfile = db.relationship("Dockerfile", backref=db.backref("builders", lazy="dynamic"))
    # Roles allowed to view/build this Builder when the current user lacks
    # builder.manage (which always grants full access to every Builder). No
    # roles assigned means restricted to builder.manage users only — see
    # is_accessible_to() below.
    allowed_roles = db.relationship("Role", secondary=builder_roles, backref=db.backref("allowed_builders", lazy="dynamic"))

    def is_accessible_to(self, user):
        if user.has_permission("builder.manage"):
            return True
        return user.role is not None and user.role in self.allowed_roles
