import json

from app.extensions import db
from app.models import ChangeType, Object
from app.services.ai.build_prefill import suggest_metadata


class _FakeProvider:
    def __init__(self, response):
        self.response = response
        self.received_prompt = None

    def generate_description(self, prompt):
        self.received_prompt = prompt
        return self.response


def _patch_provider(monkeypatch, response, provider_type="claude"):
    fake = _FakeProvider(response)
    monkeypatch.setattr("app.services.ai.build_prefill.default_provider_type", lambda: provider_type)
    monkeypatch.setattr("app.services.ai.build_prefill.get_ai_provider", lambda pt: fake)
    return fake


class TestSuggestMetadata:
    def test_returns_empty_result_for_no_commit_messages(self, app):
        with app.app_context():
            result = suggest_metadata([])
            assert result == {
                "matched_object_names": [],
                "new_object_names": [],
                "change_type_name": None,
                "description": "",
            }

    def test_parses_a_well_formed_json_response(self, app, monkeypatch):
        with app.app_context():
            db.session.add(Object(name="checkout-flow"))
            db.session.add(ChangeType(name="Bug Fix"))
            db.session.commit()

            response = json.dumps(
                {
                    "matched_objects": ["checkout-flow"],
                    "new_objects": ["payments"],
                    "change_type": "Bug Fix",
                    "description": "Fixed the checkout button.",
                }
            )
            _patch_provider(monkeypatch, response)

            result = suggest_metadata(["fix: checkout button"])
            assert result == {
                "matched_object_names": ["checkout-flow"],
                "new_object_names": ["payments"],
                "change_type_name": "Bug Fix",
                "description": "Fixed the checkout button.",
            }

    def test_tolerates_markdown_fenced_json(self, app, monkeypatch):
        with app.app_context():
            response = "Here you go:\n```json\n" + json.dumps(
                {"matched_objects": [], "new_objects": ["backend"], "change_type": None, "description": "x"}
            ) + "\n```"
            _patch_provider(monkeypatch, response)

            result = suggest_metadata(["feat: add thing"])
            assert result["new_object_names"] == ["backend"]

    def test_folds_a_new_object_that_actually_matches_an_existing_one_case_insensitively(self, app, monkeypatch):
        with app.app_context():
            db.session.add(Object(name="Checkout-Flow"))
            db.session.commit()

            response = json.dumps(
                {"matched_objects": [], "new_objects": ["checkout-flow"], "change_type": None, "description": ""}
            )
            _patch_provider(monkeypatch, response)

            result = suggest_metadata(["fix: x"])
            assert result["matched_object_names"] == ["Checkout-Flow"]
            assert result["new_object_names"] == []

    def test_unknown_change_type_name_is_dropped(self, app, monkeypatch):
        with app.app_context():
            response = json.dumps(
                {"matched_objects": [], "new_objects": [], "change_type": "Nonexistent", "description": ""}
            )
            _patch_provider(monkeypatch, response)

            result = suggest_metadata(["fix: x"])
            assert result["change_type_name"] is None

    def test_malformed_json_returns_empty_result_without_raising(self, app, monkeypatch):
        with app.app_context():
            _patch_provider(monkeypatch, "not json at all")
            result = suggest_metadata(["fix: x"])
            assert result["matched_object_names"] == []
            assert result["description"] == ""

    def test_provider_exception_returns_empty_result_without_raising(self, app, monkeypatch):
        with app.app_context():
            def _raise(pt):
                raise RuntimeError("provider down")

            monkeypatch.setattr("app.services.ai.build_prefill.default_provider_type", lambda: "claude")
            monkeypatch.setattr("app.services.ai.build_prefill.get_ai_provider", _raise)

            result = suggest_metadata(["fix: x"])
            assert result["description"] == ""

    def test_prompt_includes_existing_objects_and_change_types(self, app, monkeypatch):
        with app.app_context():
            db.session.add(Object(name="checkout-flow"))
            db.session.add(ChangeType(name="Bug Fix"))
            db.session.commit()

            response = json.dumps(
                {"matched_objects": [], "new_objects": [], "change_type": None, "description": ""}
            )
            fake = _patch_provider(monkeypatch, response)

            suggest_metadata(["fix: x"], additional_description="manual notes")
            assert "checkout-flow" in fake.received_prompt
            assert "Bug Fix" in fake.received_prompt
            assert "manual notes" in fake.received_prompt
