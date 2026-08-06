# CLAUDE.md

Guidance for AI agents working in this repository.

## Tech Stack

- **Backend**: Flask (application factory pattern), Python 3.12
- **ORM/Migrations**: Flask-SQLAlchemy + Flask-Migrate (Alembic)
- **Database**: PostgreSQL (UUID primary keys everywhere)
- **Auth**: Flask-Login (session-based) + Flask-WTF (CSRF) + Werkzeug password hashing
- **Frontend**: Jinja2 templates + TailwindCSS 3 + daisyUI 4 (no JS framework/SPA); vanilla JS + vendored SortableJS for drag-and-drop
- **Deployment**: Docker (multi-stage: Node build for CSS, then Python/gunicorn), Docker Compose (`web` + `db` services)
- **Config/env**: `python-dotenv`, `.env` file
- **Tests**: pytest, hitting a real Postgres test DB (not sqlite/mocked)

## Architecture & Folder Structure

Flask **application factory** in [app/**init**.py](app/__init__.py) (`create_app`), extensions instantiated in [app/extensions.py](app/extensions.py) (`db`, `migrate`, `login_manager`, `csrf`).

```
app/
  blueprints/<name>/{routes.py, forms.py, __init__.py}   # one blueprint per feature: auth, main, users, roles, permissions, menus, logs
  models/                                                 # one file per table: user, role, permission, menu, activity_log
  utils/
    decorators.py     # @permission_required(code), @role_required(name)
    menu_builder.py   # builds sidebar/navbar tree, injected via context_processor into every template
    logger.py         # log_activity(...) writes to ActivityLog
  templates/<blueprint>/*.html, templates/partials/{navbar,sidebar}.html, templates/base.html
  static/src/input.css   -> built into static/dist/output.css (Tailwind/daisyUI) — dist/ is generated, not committed
  static/vendor/sortablejs/Sortable.min.js  # manually vendored copy of the npm package
migrations/          # Alembic, managed by `flask db migrate/upgrade`
seeds/                # seed_admin.py (creates the one Super Admin), seed_menu.py (default menu tree)
tests/                # pytest, one file per feature area
config.py             # Development/Production/Testing config classes, selected via FLASK_ENV
```

RBAC model: `User` → `Role` → many-to-many `Permission` (via `role_permissions` table). Permissions are string codes like `user.create`, `menu.edit`, `logs.view` — routes are protected by permission code, not role name.

Menus (sidebar/navbar) are DB-driven (`Menu` model), rendered dynamically per user via `menu_builder()`, **not** hardcoded in templates.

## Entry Points

- [run.py](run.py) — dev entry point, calls `create_app()` from [app/**init**.py](app/__init__.py)
- [app/**init**.py](app/__init__.py) `create_app()` — registers all extensions and blueprints; this is where new blueprints/extensions must be wired in
- [entrypoint.sh](entrypoint.sh) — container startup: wait for DB → `flask db upgrade` → run seed scripts → `gunicorn run:app`
- Blueprints are registered with explicit `url_prefix`s (e.g. `/users`, `/roles`, `/menus`) except `auth` and `main`

## Conventions

- Blueprint package = `<name>_bp`, one `routes.py` + `forms.py` (Flask-WTF) per blueprint
- All model PKs are `UUID` (`sqlalchemy.dialects.postgresql.UUID`), generated with `uuid.uuid4` default — not integer autoincrement
- Route handlers: guard with `@permission_required("<resource>.<action>")` from [app/utils/decorators.py](app/utils/decorators.py); call `log_activity(action=..., target_type=..., target_id=..., description=...)` from [app/utils/logger.py](app/utils/logger.py) after any create/update/delete/login/logout
- Users support both a soft-delete toggle (`is_active=False`, via `toggle_active`) and a hard delete (`delete_user`). Hard delete blocks self-deletion and nulls out `activity_logs.user_id` / other users' `created_by` referencing the deleted user first, so activity log history and attribution survive the row's removal instead of failing on the FK or cascading
- Templates extend `base.html`; use daisyUI component classes (`alert alert-error`, `badge-success`, etc.) and Tailwind utilities
- **Jinja gotcha**: never write `{% if %}...{% endif %}` inline inside an HTML tag's attribute list — the workspace's HTML formatter corrupts it. Use a single expression instead: `{{ 'selected' if cond else '' }}`
- Flash categories map directly to daisyUI alert classes (`success`, `error`, `info`)

## Business Rules / Domain Logic

- **No public signup** — users are only created by an admin via User Management; the one initial Super Admin comes from `seeds/seed_admin.py` (`is_system=True` role, cannot be deleted)
- **Menus have max nesting depth of 1**: a top-level menu can have children, but a child cannot have children of its own. Enforced server-side in [app/blueprints/menus/routes.py](app/blueprints/menus/routes.py) (`edit_menu`, `reorder_menus`) — do not remove this check, and any new menu-creation code must respect it
- A menu with no `permission_code` is visible to everyone; otherwise the viewer needs that permission (see `_visible()` in [app/utils/menu_builder.py](app/utils/menu_builder.py))
- A menu that still has children cannot be deleted, and cannot be re-parented (must stay top-level)
- Roles with `is_system=True` are built-in and must not be deletable
- Every login/logout/failed-login and every create/update/delete on users/roles/menus should write an `ActivityLog` row

## Do NOT Touch

- `app/static/dist/` — generated Tailwind output (built by `npm run build:css` / Docker asset stage), not hand-edited
- `app/static/vendor/sortablejs/Sortable.min.js` — vendored third-party file, copied from `node_modules`, not edited directly
- `migrations/versions/*` — Alembic-generated migrations; create new ones via `flask db migrate`, don't hand-edit existing ones
- `migrations/env.py`, `alembic.ini`, `script.py.mako` — Alembic scaffolding
- `.env` — real secrets/DB credentials, never commit or print its contents

## Constraints & Gotchas

- Test DB is a **separate real Postgres database** (`TEST_DATABASE_URL`, `TestingConfig`), not sqlite — tests require a reachable Postgres instance
- Run tests: `source .venv/bin/activate && python -m pytest tests/ -v` (needs `requirements-dev.txt`)
- A long-running `flask run` dev server does not always reload when _new_ blueprint modules are added (only edits to already-imported files trigger the reloader) — a full restart may be needed after adding a new blueprint
- CSRF protection (`Flask-WTF`) is active on all POST endpoints except under `TestingConfig`, where it's disabled
- `reorder_menus()` expects a specific JSON shape (`{"top_level": [...], "children": {parent_id: [child_id, ...]}}`) and rejects payloads that would create depth > 1
