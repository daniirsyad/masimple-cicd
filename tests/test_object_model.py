from app.extensions import db
from app.models import Object


class TestObjectResolve:
    def test_resolves_existing_ids(self, app):
        with app.app_context():
            obj = Object(name="checkout-flow")
            db.session.add(obj)
            db.session.commit()
            obj_id = obj.id

            resolved = Object.resolve([str(obj_id)], [])
            assert [o.name for o in resolved] == ["checkout-flow"]

    def test_ignores_unknown_or_malformed_ids(self, app):
        with app.app_context():
            resolved = Object.resolve(["00000000-0000-0000-0000-000000000000", "not-a-uuid", ""], [])
            assert resolved == []

    def test_creates_a_new_object_for_an_unseen_name(self, app):
        with app.app_context():
            resolved = Object.resolve([], ["backend"])
            assert [o.name for o in resolved] == ["backend"]
            assert Object.query.filter_by(name="backend").count() == 1

    def test_reuses_an_existing_object_instead_of_creating_a_duplicate(self, app):
        with app.app_context():
            obj = Object(name="backend")
            db.session.add(obj)
            db.session.commit()

            resolved = Object.resolve([], ["backend"])
            assert [o.id for o in resolved] == [obj.id]
            assert Object.query.filter_by(name="backend").count() == 1

    def test_blank_and_whitespace_only_new_names_are_skipped(self, app):
        with app.app_context():
            resolved = Object.resolve([], ["", "   ", None])
            assert resolved == []

    def test_dedupes_across_ids_and_names_pointing_at_the_same_object(self, app):
        with app.app_context():
            obj = Object(name="checkout-flow")
            db.session.add(obj)
            db.session.commit()
            obj_id = obj.id

            resolved = Object.resolve([str(obj_id)], ["checkout-flow"])
            assert len(resolved) == 1
            assert resolved[0].id == obj_id

    def test_combines_multiple_existing_and_multiple_new(self, app):
        with app.app_context():
            existing_a = Object(name="a")
            existing_b = Object(name="b")
            db.session.add_all([existing_a, existing_b])
            db.session.commit()
            ids = [str(existing_a.id), str(existing_b.id)]

            resolved = Object.resolve(ids, ["c", "d"])
            assert {o.name for o in resolved} == {"a", "b", "c", "d"}
