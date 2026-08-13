import uuid

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class Object(db.Model):
    """A reusable "Object" label (what a build/documentation entry is about,
    e.g. "checkout-flow") — same extensible-lookup shape as ChangeType/
    VersionType: pick from the existing list, or get-or-create a new row for
    an unseen name. A BuildBatch/VersionDocumentation can reference several
    at once (see build_batch_objects/version_documentation_objects below).
    """

    __tablename__ = "objects"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    @staticmethod
    def resolve(object_ids, new_names):
        """Turn submitted existing-object ids + free-typed new names into a
        deduped list of real Object rows, get-or-creating any name that
        doesn't already exist yet (exact-match, same convention as
        VersionType's own get-or-create-on-unseen-text combobox) — shared by
        every Object-picker call site so two slightly different
        normalizations never create near-duplicate rows for the same name.
        """
        resolved = {}
        for raw_id in object_ids:
            try:
                object_id = uuid.UUID(raw_id)
            except (TypeError, ValueError):
                continue
            obj = Object.query.get(object_id)
            if obj is not None:
                resolved[obj.id] = obj

        for raw_name in new_names:
            name = (raw_name or "").strip()
            if not name:
                continue
            obj = Object.query.filter_by(name=name).first()
            if obj is None:
                obj = Object(name=name)
                db.session.add(obj)
                db.session.flush()
            resolved[obj.id] = obj

        return list(resolved.values())
