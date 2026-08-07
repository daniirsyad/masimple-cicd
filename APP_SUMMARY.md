# Hamilton — Application Summary

Written to hand to a separate AI conversation (that has no access to this
codebase) as context for planning a new feature. It's self-contained: tech
stack, architecture, full feature list, data model, and the conventions a
plan should respect. It does not cover implementation history — that lives
in this repo's own `AI_CONTEXT.md`/`SESSION_START.md`, not needed here.

## What it is

Hamilton is an internal Flask web app with two halves:

1. A general-purpose **admin/RBAC foundation** — users, roles, granular
   permissions, a database-driven sidebar/navbar menu, activity logging, and
   error logging.
2. A **Docker Image Builder module** built on top of it — register Git repos
   and container registries, define reusable "Builder" configs, trigger
   versioned builds (single or batched), push images, and auto-generate
   changelog-style documentation for each successful build (with an AI
   assist).

It's a single-tenant internal tool (one deployment, multiple named users with
different roles) — not a SaaS product with per-customer isolation.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Flask (application factory pattern), Python 3.12 |
| ORM / Migrations | Flask-SQLAlchemy + Flask-Migrate (Alembic) |
| Database | PostgreSQL, UUID primary keys everywhere |
| Auth | Flask-Login (session-based) + Flask-WTF (CSRF) + Werkzeug password hashing |
| Frontend | Jinja2 + Tailwind CSS 3 + daisyUI 4 — **no JS framework/SPA**; vanilla JS per page, vendored SortableJS for drag-and-drop |
| Background work | Python `threading`/`queue` — a single in-process worker thread, no Celery/Redis |
| Deployment | Docker (multi-stage: Node build for CSS, then Python/gunicorn), Docker Compose (`web` + `db`) |
| Git integration | GitPython, provider-abstracted (`GitProvider` → `GitHubProvider`) |
| Registry integration | docker-py against Docker Hub, provider-abstracted (`RegistryProvider` → `DockerHubProvider`) |
| Image builds | shells out to `docker buildx build` or a `kaniko-executor` binary, provider-abstracted (`BuildEngine`) |
| AI description generation | provider-abstracted (`AIProvider`); Qwen implemented, Claude/Gemini/Custom API are stubs |
| Credential encryption | `cryptography` Fernet, key from `SECRET_ENCRYPTION_KEY` env var |
| Tests | pytest against a **real** Postgres test DB (not sqlite/mocked), ~290 tests |

## Architecture conventions

- App factory in `app/__init__.py` (`create_app`); extensions (`db`,
  `migrate`, `login_manager`, `csrf`) instantiated in `app/extensions.py`.
- **One blueprint per feature**, package name `<name>_bp`, each with its own
  `routes.py` + `forms.py` (Flask-WTF): `auth`, `main`, `users`, `roles`,
  `permissions`, `menus`, `logs`, `ai_settings`, `git_sources`, `registries`,
  `versions`, `builders`, `images`, `documentation`, `system_config`.
- **One model file per table** under `app/models/`.
- All model PKs are `UUID` (`sqlalchemy.dialects.postgresql.UUID`,
  `default=uuid.uuid4`) — never integer autoincrement.
- Routes are protected by **permission code**, not role name, via
  `@permission_required("resource.action")` (`app/utils/decorators.py`) —
  unauthenticated requests redirect to login, authenticated-but-lacking-permission
  gets a 403.
- `log_activity(action=..., target_type=..., target_id=..., description=...)`
  (`app/utils/logger.py`) is called after every create/update/delete/login/logout.
- Sidebar/navbar menus are fully **database-driven** (`Menu` model,
  `app/utils/menu_builder.py`), not hardcoded in templates — max nesting
  depth of 1 (a top-level item can have children; a child cannot).
- Provider-agnostic abstractions with a factory/strategy pattern for
  everything pluggable: Git hosting, container registry, build engine, AI
  provider — new implementations are a new class + a new config row, no
  changes to calling code.
- Only one build runs system-wide at a time (`app/services/build/worker.py`,
  a background thread pulling from a queue) — a "batch" just means several
  jobs queued back-to-back under one shared version string, not parallel.
- Templates: Tailwind/daisyUI utility classes; a handful of pages have their
  own small vanilla-JS file (`app/static/js/<page>.js`) for
  cascading dropdowns, searchable-history comboboxes, live-status polling,
  or client-built markup.

## Data model (by area)

**RBAC / core:**
`User` → `Role` → many-to-many `Permission` (`role_permissions`); `Menu`
(self-referential parent/child, `permission_code`, `show_in_navbar`/`show_in_sidebar`);
`ActivityLog`; `ErrorLog` (source, message, traceback, request context — fed
by a global `got_request_exception` handler plus explicit `log_error()` calls
in exception-swallowing code paths).

**Image Builder module:**
- `GitSource` (a saved token connection, e.g. one GitHub PAT) → `Repository`
  (a *registered*, locally-cloned repo under that source — repos must be
  registered/cloned before they're usable, not typed in ad hoc).
- `RegistryTarget` (a container registry credential, e.g. Docker Hub).
- `VersionType` (lookup: DEV/STAGING/PROD/…, extensible) → `Version` (a
  named, persistent major.minor.patch counter the user creates ahead of
  time, e.g. "backend-service").
- `Builder` — a reusable build config: one `Repository` + one `Version` +
  branch + Dockerfile path + one `RegistryTarget` + build args + optional
  `group_name` (purely organizational grouping for the UI) + optional custom
  `image_name` + `allowed_roles` (many-to-many `Role` — see access control
  below).
- `BuildBatch` — one build-trigger action, covering one or more `Builder`s
  that share the same `Version`. Bumps that Version's number **exactly
  once** and produces one shared version string
  (`{TYPE}.{MAJOR}.{MINOR}.{PATCH}.{DATETIME}`) all its images share. Has
  `bump_type` (major/minor/patch), aggregate `status`
  (queued/running/success/partial_failure/failed), and staged
  `object`/`change_type_id`/`additional_description` fields captured at
  trigger time.
- `ImageBuild` — one `Builder`'s execution inside a `BuildBatch`: branch
  used, status, image tag/size, build log, timestamps.
- `ChangeType` (lookup: New Program/Update/Bug Fix/…, extensible).
- `VersionDocumentation` — one-to-one with a **successfully completed**
  `BuildBatch` only (created automatically the moment a batch's last image
  succeeds, never up front and never for a failed batch): built-by, object,
  change type, an AI-generated draft description (`ai_description` +
  `ai_provider_used`, kept separately from the final editable
  `description`), `is_complete` = whether a change type has been set.
- `VersionLink` — many-to-many batch-to-batch, for a documentation record to
  say "this batch bundles/incorporates these other prior batches."
- `AIProviderConfig` (provider type, model name, API key, is_default,
  is_active) and `PromptTemplate` (editable prompt text with
  `{{branch_names}}`/`{{object}}`/`{{commit_messages}}`/`{{additional_description}}`
  placeholders, plain regex substitution not Jinja).
- `SystemConfig` — a **singleton** row of app-wide settings: timezone
  (applied to every displayed timestamp via a `localtime` Jinja filter),
  session timeout minutes, build engine choice (`docker`/`kaniko`), and a UI
  toggle (`hide_navbar_title_when_sidebar_open`).

## Feature list (by page)

- **`/` Dashboard** — active users/roles counts; permission-gated Image
  Builder stat cards (Builders/Versions/Images Built/Documentation Pending,
  each only shown if the viewer has that resource's view permission); a
  build-engine busy/idle indicator; a recent-errors count; a recent-builds
  table; recent activity log.
- **`/users`, `/roles`, `/permissions`** — standard RBAC CRUD. The Roles
  page's permission picker groups the ~26 permissions under friendly
  resource headings (Users, Builders, AI Providers, …) with human
  descriptions, not raw codes. Users support both soft-delete
  (`is_active=False`) and hard delete (blocks self-deletion, reassigns
  `activity_logs.user_id`/`created_by` references first).
- **`/menus`** — sidebar/navbar tree management, drag-and-drop reorder
  (SortableJS), depth-1 enforcement.
- **`/logs`** (Activity) and **`/logs/errors`** (Error) — filterable
  (user/action/date range for Activity; source/date range for Errors), both
  behind the same `logs.view` permission, tab-switcher between the two.
- **`/ai-settings`** — configure AI providers (API key, model, active
  flag) and the editable prompt template used for description generation.
- **`/github`** — GitHub token connections; per-connection repo listing via
  the API; **Register** (mandatory immediate clone into a persistent local
  directory, `Repository.status` cloning→ready/error), **Re-sync**, **Remove
  registration** (blocked if any Builder still references it).
- **`/registries`** — container registry targets, credentials validated on
  save.
- **`/versions`** — Version entity CRUD; Version Type field is a
  searchable-history combobox (free text, autocompletes from prior values,
  creates a new `VersionType` row if unseen).
- **`/builders`** — Builder CRUD, grouped by `group_name` (grouped builders
  only build as a group; ungrouped builders build individually); per-builder
  **role-based access control** (a Builder with no `allowed_roles` is
  visible/buildable only to users with `builder.manage`; otherwise limited to
  users whose role is in the allow-list); Group and Object fields are both
  searchable-history comboboses. The build-trigger modal requires Bump Type,
  Object, and Change Type (no silent defaults) and does **not** allow a
  branch override at trigger time — branch is always the Builder's own
  configured default, changeable only via Edit Builder. Live build-status
  widget with a spinner + real progress bar while a build runs.
- **`/images`** — build run history grouped by `BuildBatch`, filterable by
  status, with per-image registry links and human-readable sizes; running
  items show a spinner.
- **`/documentation`** — list of every fully-successful (and thus
  documented) batch, with a full multi-field filter (Version, Version
  String substring, Change Type, Object substring, Built By, Documented/Pending
  status, date range, all AND-combined, preserved across pagination).
- **`/documentation/<batch_id>`** — per-batch documentation detail/edit:
  change type, object, description, an AI-assist panel (shows the filled-in
  prompt, lets you regenerate or edit before saving — never saves raw AI
  output unseen), and a linked-batches picker.
- **`/config`** — System Configuration (`system.manage`): timezone, session
  timeout, build engine, duplicate-title toggle.

## Conventions a new feature should follow

- New protected routes: `@permission_required("resource.action")`, add the
  permission code to `seeds/seed_admin.py`'s `BASE_PERMISSIONS`, call
  `log_activity(...)` after mutations.
- New tables: UUID PK via `sqlalchemy.dialects.postgresql.UUID`, generate
  the migration with `flask db migrate` (never hand-edit existing migration
  files), add a `server_default` if adding a NOT NULL column to a table that
  can already have rows (e.g. a singleton config table).
- New sidebar/navbar entries go in `Menu` rows (via `/menus` or
  `seeds/seed_menu.py`), not hardcoded in templates.
- Forms: Flask-WTF, one `forms.py` per blueprint. A free-text field that
  should suggest prior values (like Object/Group/Version Type) uses the
  established pattern: a plain `<input>` + a same-page `<ul class="...
  -dropdown">`, suggestions passed via a `<script type="application/json">`
  block (never a `data-*` HTML attribute — `tojson`'s escaping targets
  `<script>` content, not quoted attributes, and breaks on any value
  containing a literal double quote), wired up by a small
  `setupSearchDropdown()` helper duplicated per page's own JS file.
- **Jinja gotcha**: never write `{% if %}...{% endif %}` inline inside an
  HTML tag's attribute list — use `{{ 'x' if cond else '' }}` instead.
- CSS changes require `npm run build:css` to actually appear (compiled
  output isn't hand-edited or auto-rebuilt by the dev server).
- Respect `app/static/dist/`, `app/static/vendor/sortablejs/`,
  `migrations/versions/*`, and `migrations/env.py`/`alembic.ini` as
  generated/vendored — don't hand-edit them.
- The app supports light/dark mode (daisyUI themes, toggle in the navbar,
  `localStorage`-persisted) — any new SVG icon markup pasted into `Menu.icon`
  or added in templates should use `fill="currentColor"`/`stroke="currentColor"`
  so it isn't stuck black in dark mode.
- A currently-in-progress item (build running, etc.) should get a small
  `loading loading-spinner loading-xs` next to its status badge — the
  established "in progress" visual language across this app.

## Known gaps / things a new feature might need to account for

- `REPO_CLONE_ROOT` (`<app>/data/repos`) has no persistent volume mounted in
  the current Docker Compose setup — registered repos' local clones are lost
  on container restart. The build worker and manual Re-sync self-heal
  (re-clone on demand); the Builder create/edit branch/Dockerfile pickers and
  `/github`'s unregistered-repo listing do not.
- Claude/Gemini/Custom-API `AIProvider` implementations and
  GHCR/Harbor/ECR `RegistryProvider` implementations are unimplemented stubs
  — only Qwen and Docker Hub actually work today.
- Everything runs through a single in-process worker thread — there is no
  distributed/multi-instance deployment story; a feature that needs
  horizontal scaling would need to introduce a real broker first.
