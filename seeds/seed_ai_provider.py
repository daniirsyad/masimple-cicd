"""Seeds a default Qwen AI provider config (set as default) and a default
prompt template for the version-documentation description assistant.

Safe to re-run: if any AIProviderConfig or PromptTemplate rows already exist,
this does nothing (it only ever creates the very first default of each).

Usage:
    python seeds/seed_ai_provider.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import AIProviderConfig, PromptTemplate

DEFAULT_QWEN_MODEL = "qwen-plus"

DEFAULT_TEMPLATE_TEXT = """Write a concise, professional changelog description for this build.

Branches: {{branch_names}}
Object: {{object}}
Additional notes from the requester: {{additional_description}}

Commit messages:
{{commit_messages}}

Write 1-3 sentences summarizing what changed, suitable for release notes.
Incorporate the additional notes above if any were given."""


def run():
    app = create_app()
    with app.app_context():
        if AIProviderConfig.query.count() == 0:
            provider = AIProviderConfig(
                provider_type="qwen",
                model_name=DEFAULT_QWEN_MODEL,
                is_default=True,
                is_active=True,
            )
            db.session.add(provider)
            print("Created default Qwen AI provider config")

        if PromptTemplate.query.count() == 0:
            template = PromptTemplate(
                name="Default Description Prompt",
                template_text=DEFAULT_TEMPLATE_TEXT,
                is_default=True,
                is_active=True,
            )
            db.session.add(template)
            print("Created default prompt template")

        db.session.commit()


if __name__ == "__main__":
    run()
