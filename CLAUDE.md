# CLAUDE.md

Guidance for AI agents working in this repository.

## Tech Stack

- **Backend**: Flask (application factory pattern), Python 3.12
- **ORM/Migrations**: Flask-SQLAlchemy + Flask-Migrate (Alembic)
- **Database**: PostgreSQL (UUID primary keys everywhere)
- **Auth**: Flask-Login (session-based) + Flask-WTF (CSRF) + Werkzeug password hashing
- **Frontend**: Jinja2 templates + TailwindCSS 3 + daisyUI 4 (no JS framework); vanilla JS + vendored SortableJS for drag-and-drop. Vendored htmx with `hx-boost` on `<body>` makes links and forms load without a full page reload (server-rendered pages unchanged)
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
- **Hide actions the user can't use**: wrap each create/edit/delete/run button *and* its modal in `{% if current_user.has_permission("<code>") %}`, using the same code as the route's `@permission_required`. A page someone can only view must not offer actions that would 403. Where reading the data is still useful (Deployment Manifests' YAML), show a read-only "View" modal (`<fieldset disabled>`) instead. `tests/test_view_only_pages.py` + the `perm_client(*codes)` fixture cover this
- A 403 never shows Flask's bare page: the app-wide handler in `app/__init__.py` renders `errors/403.html` (reason + Back button, status stays 403), or returns `{"ok": false, "error": ...}` JSON for `fetch()` calls (`/api/` paths, JSON bodies, or `Sec-Fetch-Mode` other than `navigate`). `permission_required` puts a readable reason in it, built from the `Permission.description`
- Templates extend `base.html`; use daisyUI component classes (`alert alert-error`, `badge-success`, etc.) and Tailwind utilities
- **Jinja gotcha**: never write `{% if %}...{% endif %}` inline inside an HTML tag's attribute list — the workspace's HTML formatter corrupts it. Use a single expression instead: `{{ 'selected' if cond else '' }}`
- **Jinja gotcha**: the body of a `{% call %}` block (and other literal template text) is raw HTML, not autoescaped — a placeholder like `<app-deployment>` inside a `<pre>` is parsed as a tag and vanishes. Write `&lt;...&gt;`
- **daisyUI gotcha**: `.collapse` is `display: grid; overflow: hidden`, so wide content (a long `<pre>`) grows the column and gets clipped instead of scrolling. Add `grid-cols-1` (`minmax(0, 1fr)`) to the collapse element
- Flash categories map directly to daisyUI alert classes (`success`, `error`, `info`)
- **Page scripts under hx-boost** ([app/static/js/page.js](app/static/js/page.js)): htmx swaps `<body>`, so `DOMContentLoaded` fires once per real load and timers/listeners outlive their page. Start page JS with `onPageReady(() => {...})`, never `DOMContentLoaded`; poll with `pageInterval(fn, ms)`; give window/document listeners and polling `fetch()` `{ signal: pageSignal() }`; stop anything else (EventSource, chained `setTimeout`) with `onPageLeave(fn)`; re-fetch the page with `refreshPage()`, not `location.reload()`. Scripts loaded once in `<head>` (sidebar/theme/modal-form) use `onEveryPage(fn)`. A page's `{% block extra_head %}` is not loaded on htmx navigation, so shared CSS goes in `base.html`'s head. A form whose submit JS handles itself (`preventDefault` + fetch) needs `hx-boost="false"`, since htmx submits it anyway; for the same reason, don't sync a field into a form on `submit` (the YAML editor saves on every change instead). Modal-on-validation-error uses `<main data-open-modal>`, not `<body>`

## Business Rules / Domain Logic

- **No public signup** — users are only created by an admin via User Management; the one initial Super Admin comes from `seeds/seed_admin.py` (`is_system=True` role, cannot be deleted)
- **Menus have max nesting depth of 1**: a top-level menu can have children, but a child cannot have children of its own. Enforced server-side in [app/blueprints/menus/routes.py](app/blueprints/menus/routes.py) (`edit_menu`, `reorder_menus`) — do not remove this check, and any new menu-creation code must respect it
- A menu with no `permission_code` is visible to everyone; otherwise the viewer needs that permission (see `_visible()` in [app/utils/menu_builder.py](app/utils/menu_builder.py))
- A menu that still has children cannot be deleted, and cannot be re-parented (must stay top-level)
- Roles with `is_system=True` are built-in and must not be deletable
- Every login/logout/failed-login and every create/update/delete on users/roles/menus should write an `ActivityLog` row
- **Helm manifests** (`DeploymentManifest.source_type == "helm"`, [app/services/deployment/helm.py](app/services/deployment/helm.py)): `yaml_content` is the values file. Stop/Restart/status act on the release recorded on the execution (`helm_release_name`/`helm_namespace`), never the manifest's current fields; the form blocks changing type/release/namespace while deployed. Every `helm` call runs in a fresh temp HELM_*_HOME with passwords over stdin. Only `kube` servers can run Helm. Chart source is a repo or an uploaded `.tgz` (`HelmChartUpload`, bytes in Postgres, every version kept, unique per manifest+version and never replaced with different contents); an upload-source manifest deploys the upload whose `chart_version` equals its `helm_chart_version` (no FK back). Uploads are checked by `read_chart_archive()` (in memory, never extracted) and deleted with their manifest
- **List search** (Deployment Manifests, Builders, Workflows): client-side, `list_search` macro in `partials/_macros.html` + `js/list-search.js`. It matches each row's lowercase `data-search` text (not the row's visible text, which includes button labels) and hides `[data-search-card]` cards with no matches; a new searchable field goes into that row's `data-search`
- **A build's git changes are captured at build time, never re-read later** (a force-push can make the old range unreadable): the worker saves `ImageBuild.commit_sha`, the `ImageBuildCommit` rows and `ImageBuild.changed_files` right after the repo sync, and writes the same data into the build log (`_record_commit_history`, `_format_changes` in [app/services/build/worker.py](app/services/build/worker.py)). All of it is best-effort: a git read failing must not fail the build. Pages that show a build's changes (Documentation, Images) get them from `commit_range_for_image()` in [app/services/build/history.py](app/services/build/history.py); `changed_files` is `None` for builds made before it existed, so templates must handle "not recorded"

## Do NOT Touch

- `app/static/dist/` — generated Tailwind output (built by `npm run build:css` / Docker asset stage), not hand-edited
- `app/static/vendor/sortablejs/Sortable.min.js` — vendored third-party file, copied from `node_modules`, not edited directly
- `app/static/vendor/htmx/htmx.min.js` — vendored htmx 2.0.11, copied from `node_modules/htmx.org/dist`
- `app/static/vendor/codemirror/` (incl. `addon/`) — vendored CodeMirror 5.65.21 core + addons, copied from `node_modules/codemirror`; keep addon versions matching the core
- `migrations/versions/*` — Alembic-generated migrations; create new ones via `flask db migrate`, don't hand-edit existing ones
- `migrations/env.py`, `alembic.ini`, `script.py.mako` — Alembic scaffolding
- `.env` — real secrets/DB credentials, never commit or print its contents

## Constraints & Gotchas

- Test DB is a **separate real Postgres database** (`TEST_DATABASE_URL`, `TestingConfig`), not sqlite — tests require a reachable Postgres instance
- Run tests: `source .venv/bin/activate && python -m pytest tests/ -v` (needs `requirements-dev.txt`)
- A long-running `flask run` dev server does not always reload when _new_ blueprint modules are added (only edits to already-imported files trigger the reloader) — a full restart may be needed after adding a new blueprint
- CSRF protection (`Flask-WTF`) is active on all POST endpoints except under `TestingConfig`, where it's disabled
- Test fakes for the git provider (`_FakeGitProvider` in `tests/test_worker.py`, `_FakePreviewGitProvider` in `tests/test_builders.py`) are duck-typed, not `GitProvider` subclasses: when you add a method to `GitProvider` that the worker or preview calls, add it to the fakes too, or the tests silently take the best-effort "git failed" path
- `flask db migrate` compares against the local dev database, so that database must be at head first (`flask db current`). A new migration isn't applied to the dev database until `flask db upgrade`; the Docker entrypoint runs it on startup
- `reorder_menus()` expects a specific JSON shape (`{"top_level": [...], "children": {parent_id: [child_id, ...]}}`) and rejects payloads that would create depth > 1
- **Always update `SESSION_START.md`'s "Current state" section (and `APP_SUMMARY.md` if the change affects current-state architecture/features) at the end of any substantive session** — new feature, bug fix, architecture change, or DB/manifest edits made directly outside git — without waiting to be asked. Follow the existing style: a numbered list under a "This session's work" bullet, with prior sessions' bullets relabeled down a tier (see the file's own history for the cascade)

## Planned: Delete anything + Archive instead of Disable (NOT STARTED)

Agreed with the user 2026-11-26, to execute later. Until it ships, the current delete rules above still apply. Before starting, confirm the three **open decisions** at the bottom with the user. When done, update this file's business rules (system-role, menu-children and other delete blocks change) and remove this section.

**Goal**: every item can be deleted whether or not it's in use; "Disable" becomes "Archive". Deleting never loses history: history rows keep a snapshot of the deleted item's name and their link to it is set to NULL, the same as `DeploymentExecution.manifest_id` already does for a deleted manifest. Items that *used* the deleted item are unlinked, not deleted: they show "pick a new X" and refuse to run until fixed.

**Phases**
1. **Archive** (no migration): on the 8 pages with Disable (builders, versions, registries, git_sources, dockerfiles, deployment_servers, deployment_manifests, workflows incl. `workflows/view.html`) rename Disable/Enable to Archive/Restore, routes `/disable`/`/enable` to `/archive`/`/restore`, flash text, and activity-log actions for new entries. Keep `is_active` as the column.
2. **Migration**: make nullable `image_builds.builder_id`, `build_batches.version_id`, `builders.version_id`/`repository_id`/`registry_target_id`, `deployment_executions.server_id`, `workflow_runs.workflow_id`, `workflow_step_runs.workflow_step_id`. Add name snapshots where history displays the name (e.g. `builder_name`, `version_name`, `server_name`, `manifest_name`, `workflow_name`, step label), backfilled from current names.
3. **Delete per item**, lowest risk first: Dockerfile, Registry, Git connection (its repositories are removed), Deployment server (removed from manifests' targets), Manifest (removed from workflow steps; cluster left as is), Workflow + steps, Builder (its manifest version bindings are removed), Version, Role (its users get no role), Permission (removed from roles; warn that a code the app checks, e.g. `user.view`, locks that page until recreated), Menu (sub-items deleted with it). Each delete modal shows a server-computed impact summary ("Used by 3 Builders, which will need a new registry; 41 past builds keep the name"). Delete buttons are always enabled except the safety exceptions below.
4. **Handle "deleted" everywhere**: every reader of these links copes with NULL by showing the snapshot name + "(deleted)": Images, Documentation, deployment runs, workflow runs, Telegram/Discord bots, build/deploy/workflow workers, AI prefill. Unlinked dependents explain what to pick instead of erroring. For each item, a test deletes a *used* one and checks every page and bot command that shows its history.
5. Full suite, browser click-through, update SESSION_START.md/APP_SUMMARY.md, and the business rules above.

**Open decisions** (recommended defaults in bold; confirm with the user):
- Safety exceptions: **keep** (a) can't delete an item a build/deploy/workflow run is using *right now*, (b) can't delete the built-in Super Admin role, your own account, or the last admin, (c) deleting a deployed manifest/server leaves cluster resources running, and the confirm dialog says so.
- Dependents: **unlink** them (vs. delete them along with the item).
- Users: **keep Deactivate** (login on/off) rather than renaming it to Archive; users are already deletable.
