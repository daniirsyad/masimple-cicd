import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class ImageBuild(db.Model):
    __tablename__ = "image_builds"
    __table_args__ = (
        db.Index(
            "ix_image_builds_single_running",
            "status",
            unique=True,
            postgresql_where=db.text("status = 'running'"),
        ),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = db.Column(UUID(as_uuid=True), db.ForeignKey("build_batches.id"), nullable=False)
    builder_id = db.Column(UUID(as_uuid=True), db.ForeignKey("builders.id"), nullable=False)
    branch_used = db.Column(db.String, nullable=False)
    commit_sha = db.Column(db.String, nullable=True)
    status = db.Column(db.String, default="queued", nullable=False)
    registry_name = db.Column(db.String, nullable=True)
    image_tag = db.Column(db.String, nullable=True)
    image_size = db.Column(db.BigInteger, nullable=True)
    build_log = db.Column(db.Text, nullable=True)
    queue_position = db.Column(db.Integer, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    heartbeat_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # Set by the worker (app/services/build/worker.py's _run_build) alongside
    # status='failed', pointing at the ErrorLog row it logged for that same
    # failure — lets /images link straight to the full traceback/log instead
    # of making someone go search Error Logs for the matching entry.
    # ondelete="SET NULL": nothing currently deletes ErrorLog rows, but if a
    # purge feature is ever added, a build's own failure record shouldn't
    # become unreachable/erroring just because its error log aged out.
    error_log_id = db.Column(
        UUID(as_uuid=True), db.ForeignKey("error_logs.id", ondelete="SET NULL"), nullable=True
    )

    batch = db.relationship("BuildBatch", backref=db.backref("image_builds", lazy="dynamic"))
    builder = db.relationship("Builder", backref=db.backref("image_builds", lazy="dynamic"))
    error_log = db.relationship("ErrorLog")
