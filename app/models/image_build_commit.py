import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class ImageBuildCommit(db.Model):
    """One commit that landed between the previous successful build of this
    ImageBuild's (builder, branch) pair and this one — captured at build time
    (via GitProvider.get_commits) rather than reconstructed later, since a
    force-push/rebase could make the range unreadable after the fact.
    """

    __tablename__ = "image_build_commits"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    image_build_id = db.Column(UUID(as_uuid=True), db.ForeignKey("image_builds.id"), nullable=False)
    sha = db.Column(db.String, nullable=False)
    author_name = db.Column(db.String, nullable=True)
    author_email = db.Column(db.String, nullable=True)
    message = db.Column(db.Text, nullable=False)
    committed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    image_build = db.relationship(
        "ImageBuild",
        backref=db.backref("commits", lazy="dynamic", order_by="ImageBuildCommit.committed_at"),
    )
