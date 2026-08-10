# AI Context — MASIMPLE CICD

Consolidated background/reference material for AI agents working in this repo.
This single file replaces three previously separate root-level files —
`ai-plan.md`, `image-builder-prompt.md`, and `image-builder-progress.md` —
plus historical sections that used to live directly inside `SESSION_START.md`
(archived here each time that file gets reset back to lean/current-only).
None of those are present individually anymore. **`CLAUDE.md`** (auto-loaded
project conventions), **`SESSION_START.md`** (session handoff / current
state), and **`APP_SUMMARY.md`** (current-state application overview, no
history) remain separate files in the repo root — none of them are merged
in here.

## Reading order

This file is historical, in chronological order — for what's actually true
*right now*, read `APP_SUMMARY.md` and `SESSION_START.md` instead; either can
supersede anything below that it explicitly says was reworked.

1. **Part 1** — the original foundation spec (was `ai-plan.md`): the base
   RBAC app (users/roles/permissions/menus/logs) before the Image Builder
   module existed. Fully built; kept for historical context only.
2. **Part 2** — the Image Builder module's spec (was `image-builder-prompt.md`),
   which the migration logged in Part 3 implemented.
3. **Part 3** — the Image Builder migration's progress log (was
   `image-builder-progress.md`). Its own "How to resume" section notes that
   several early follow-up items were later reworked further — see Parts 4–5
   and then `SESSION_START.md` for what's actually current.
4. **Part 4** — the post-migration follow-up sessions that used to be written
   directly into `SESSION_START.md` (System Configuration, Kaniko, the
   version-bump-at-claim-time rework, error logging, the BuildKit rewrite).
5. **Part 5** — the next round of `SESSION_START.md` content archived the
   same way (session-expiry fix, per-Builder role access, the searchable
   history-dropdown pattern, documentation filters, the dashboard overhaul,
   dark mode, and the rest of that session's UI polish pass).
6. **Part 6** — the Deployment module's full arc: built from scratch
   (`DeploymentServer`/`DeploymentManifest`/`DeploymentRun`/
   `DeploymentExecution`), then extended over two more sessions (Stop,
   live-status polling, the Pods/Namespaces/Secrets/ConfigMaps/Workloads
   K8s browser under `/deployment-pods`, Update/Restart, per-manifest user
   access, and finally merging `/deployment-pods` into `/deployment-servers`).
7. **Part 7** — a cross-cutting feature from the same session as the end of
   Part 6: every error notification across the app (flash messages, inline
   banners, JSON responses) now links directly to its `ErrorLog` row, which
   is itself independently shareable.

---

# Part 1: Original Foundation Spec

## Project Plan: MASIMPLE CICD — Build & Deployment Application (Flask Foundation)

This document is a **plan/spec** you can use as context for GitHub Copilot (Copilot Chat / Copilot Workspace) so the AI builds the application foundation with a consistent structure.

---

### 1. Tech Stack

| Layer      | Technology                                                 |
| ---------- | ---------------------------------------------------------- |
| Backend    | Flask (Application Factory Pattern)                        |
| ORM        | SQLAlchemy + Flask-Migrate (Alembic)                       |
| Database   | PostgreSQL                                                 |
| Auth       | Flask-Login + Flask-WTF (CSRF) + Werkzeug password hashing |
| Frontend   | Jinja2 + TailwindCSS + daisyUI                             |
| Deployment | Docker + Docker Compose                                    |
| Env config | python-dotenv                                              |

---

### 2. Folder Structure (recommended)

```
masimple-cicd/
├── app/
│   ├── __init__.py            # App factory
│   ├── extensions.py          # db, login_manager, migrate, csrf
│   ├── models/
│   │   ├── user.py
│   │   ├── role.py
│   │   ├── permission.py
│   │   ├── menu.py            # sidebar/navbar menu items
│   │   └── activity_log.py
│   ├── blueprints/
│   │   ├── auth/
│   │   │   ├── routes.py
│   │   │   └── forms.py
│   │   ├── main/               # Home page
│   │   │   └── routes.py
│   │   ├── users/               # User management + edit-user page
│   │   │   ├── routes.py
│   │   │   └── forms.py
│   │   ├── roles/               # Role management
│   │   │   ├── routes.py
│   │   │   └── forms.py
│   │   └── logs/                # Activity logs viewer
│   │       └── routes.py
│   ├── utils/
│   │   ├── decorators.py       # @permission_required, @role_required
│   │   ├── menu_builder.py     # builds sidebar/navbar tree from Menu table
│   │   └── logger.py           # helper to record activity log
│   ├── templates/
│   │   ├── base.html            # layout: navbar + sidebar wrapper
│   │   ├── partials/
│   │   │   ├── navbar.html
│   │   │   └── sidebar.html
│   │   ├── auth/
│   │   ├── main/
│   │   ├── users/
│   │   │   ├── list.html
│   │   │   └── edit.html        # dedicated edit-user page per user
│   │   ├── roles/
│   │   └── logs/
│   └── static/
│       └── src/
│           └── input.css       # TailwindCSS source
├── migrations/
├── seeds/
│   ├── seed_admin.py           # creates 1 initial super admin (no signup flow)
│   └── seed_menu.py            # seeds default sidebar/navbar menu tree
├── config.py
├── requirements.txt
├── package.json                # for tailwindcss + daisyUI build
├── tailwind.config.js
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .dockerignore
├── .gitignore
└── run.py
```

---

### 3. Database Schema (PostgreSQL)

Since roles are **dynamic** (admin can create/edit roles), use an **RBAC structure with a separate permission table** instead of a hardcoded enum.

#### `users`

| Column        | Type                     | Notes                    |
| ------------- | ------------------------ | ------------------------ |
| id            | UUID / Integer PK        |                          |
| username      | String, unique, not null | used for login           |
| password_hash | String, not null         |                          |
| full_name     | String                   |                          |
| is_active     | Boolean, default True    | for suspending a user    |
| role_id       | FK → roles.id            |                          |
| created_by    | FK → users.id (nullable) | who created this account |
| created_at    | DateTime                 |                          |
| last_login_at | DateTime, nullable       |                          |

#### `roles`

| Column      | Type                   | Notes                                         |
| ----------- | ---------------------- | --------------------------------------------- |
| id          | PK                     |                                               |
| name        | String, unique         | e.g. "Admin", "Developer", "Viewer"           |
| description | Text, nullable         |                                               |
| is_system   | Boolean, default False | built-in role (Super Admin) cannot be deleted |
| created_at  | DateTime               |                                               |

#### `permissions`

| Column      | Type           | Notes                                        |
| ----------- | -------------- | -------------------------------------------- |
| id          | PK             |                                              |
| code        | String, unique | e.g. `user.create`, `role.edit`, `logs.view` |
| description | String         |                                              |

#### `role_permissions` (many-to-many)

| Column        | Type                |
| ------------- | ------------------- |
| role_id       | FK → roles.id       |
| permission_id | FK → permissions.id |

#### `menus` (sidebar & navbar items)

| Column          | Type                    | Notes                                                                                                                           |
| --------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| id              | PK                      |                                                                                                                                 |
| label           | String                  | display name, e.g. "Management"                                                                                                 |
| icon            | String, nullable        | icon class/name                                                                                                                 |
| url             | String, nullable        | route path; nullable if it's a parent-only group                                                                                |
| parent_id       | FK → menus.id, nullable | **max depth: 1 level** — a menu whose `parent_id` is not null cannot have children of its own (enforce in app logic / DB check) |
| permission_code | String, nullable        | FK-like reference to `permissions.code`; menu item hidden if user lacks this permission                                         |
| order           | Integer, default 0      | sort order within its level                                                                                                     |
| show_in_navbar  | Boolean, default False  | also show as a quick-access item in the navbar                                                                                  |
| show_in_sidebar | Boolean, default True   |                                                                                                                                 |

#### `activity_logs`

| Column      | Type                    | Notes                                      |
| ----------- | ----------------------- | ------------------------------------------ |
| id          | PK                      |                                            |
| user_id     | FK → users.id, nullable | nullable if performed by the system        |
| action      | String                  | e.g. `LOGIN`, `CREATE_USER`, `UPDATE_ROLE` |
| target_type | String, nullable        | e.g. `user`, `role`                        |
| target_id   | String, nullable        |                                            |
| description | Text                    | human-readable detail                      |
| ip_address  | String, nullable        |                                            |
| created_at  | DateTime                |                                            |

> Note: since "other features will be built independently", the tables above are intentionally generic/extensible (permission-based, menu-driven) so future modules can add new `permission.code` and `menu` entries without touching the core structure.

---

### 4. Authentication System (No Signup)

- No public register page/route.
- Accounts can only be created by an Admin via the **User Management** page.
- On first project setup, run `seeds/seed_admin.py` to create 1 default **Super Admin** account (role `is_system=True`, cannot be deleted).
- Login uses **username + password**.
- Use `Flask-Login` for session management, `werkzeug.security` (`generate_password_hash` / `check_password_hash`) for hashing.
- Add simple rate-limiting / lockout after repeated failed logins (optional, e.g. `Flask-Limiter`).
- Every successful/failed login is recorded in `activity_logs`.

---

### 5. Role & Permission Management

- Role CRUD (create, edit, delete — except `is_system` roles).
- When creating/editing a role, admin selects which permissions to assign (checkbox list from the `permissions` table).
- Build a `@permission_required("user.create")` decorator to protect routes based on permission, not role name — keeping things flexible since roles are dynamic.
- Seed an initial set of base permissions, e.g.:
  - `user.view`, `user.create`, `user.edit`, `user.delete`
  - `role.view`, `role.create`, `role.edit`, `role.delete`
  - `logs.view`
  - `menu.view`, `menu.edit` (if menu management is also admin-editable)

---

### 6. User Management

- User list (with search & filter by role/status).
- Create user (admin sets username, initial password, selects role).
- **Dedicated Edit User page** (`/users/<id>/edit`) per user — update full name, role, active/inactive status, reset password. Linked from the user list row (e.g. an "Edit" button/icon).
- Avoid hard delete — use soft-delete (`is_active=False`) so activity log history stays valid.

---

### 7. Home Page

- Lightweight dashboard after login: greeting, summary counts (active users, roles), and a few recent activity log entries.
- Also serves as the "foundation" for the other features you'll build yourself later (build & deployment for MASIMPLE CICD).

---

### 8. Activity Logs

- All key actions (login, logout, create/edit/delete user, create/edit/delete role, menu changes) are recorded automatically via a `log_activity(user, action, target_type, target_id, description)` helper.
- **Logs** page (behind `logs.view` permission) shows a table of logs filterable by user, action, and date.

---

### 9. Layout: Sidebar + Navbar (TailwindCSS + daisyUI)

**Layout requirements:**

- Every authenticated page uses a shared layout (`base.html`) with a **fixed Navbar** on top and a **Sidebar** on the left (daisyUI `drawer` component).
- **Sidebar** contains the main navigation, including a **"Management" menu** (grouping Users, Roles, Logs, etc.).
- **Menus can be nested, max 1 level deep** (parent → child only, no grandchildren). Example:
  - `Management` (parent, group only, no direct link)
    - `Users` (child → `/users`)
    - `Roles` (child → `/roles`)
    - `Activity Logs` (child → `/logs`)
  - `Home` (single-level item, no children)
- Menu items are **driven by the `menus` table** (see schema in section 3) so admins can reorder/rename/hide items without code changes; each item respects `permission_code` visibility.
- **Menu items can also appear in the Navbar** — controlled per item via `show_in_navbar` (e.g. quick links to "Users" or a profile dropdown), independent from whether it's shown in the sidebar.
- Frontend build: npm with `tailwindcss` + `daisyui` plugin; `tailwind.config.js` scans `app/templates/**/*.html`.
- Suggested daisyUI components: `navbar`, `drawer` (sidebar), `menu` (nested sidebar list with a collapsible submenu for 1-level children), `table`, `modal` (quick create/edit forms), `alert` (flash messages), `badge` (active/inactive status, role name).
- Build command: `npm run build:css` (watch mode during development).

---

### 10. Docker & Deployment

- `Dockerfile`: multi-stage — build Tailwind assets, then copy into the final Python image (gunicorn).
- `docker-compose.yml` with 2 minimal services:
  - `web` (Flask + gunicorn)
  - `db` (postgres:16, with a persistent volume)
- `.env` for `DATABASE_URL`, `SECRET_KEY`, `FLASK_ENV`.
- Database migrations run automatically on container start (`flask db upgrade`) or via an entrypoint script.

---

### 11. Step-by-Step Prompts for GitHub Copilot

Follow this order so Copilot builds incrementally and keeps context clear:

1. **Scaffold the project** — create the folder structure above + `config.py` (dev/prod config) + app factory `create_app()`.
2. **Set up extensions** — `db`, `login_manager`, `migrate`, `csrf` in `extensions.py`, registered in the app factory.
3. **Create models** — `User`, `Role`, `Permission`, `role_permissions` (association table), `Menu`, `ActivityLog`.
4. **Set up Flask-Migrate** — init migrations, generate the first migration.
5. **Create seed scripts** — Super Admin + base permissions + Admin role + default menu tree (`seed_menu.py`).
6. **Auth blueprint** — `login`/`logout` routes only (no register), login form with Flask-WTF.
7. **Decorators & helpers** — `@permission_required`, `@role_required`, `log_activity()`, `menu_builder()` (builds the nested-menu tree, max 1 level, filtered by user permissions).
8. **Base layout** — `base.html` with Navbar + Sidebar partials, wired to `menu_builder()`.
9. **Main blueprint** — home page/dashboard.
10. **Users blueprint** — list, create, **dedicated edit-user page**, toggle active, reset password.
11. **Roles blueprint** — role CRUD + permission assignment.
12. **Logs blueprint** — activity log view page + filters.
13. **TailwindCSS + daisyUI setup** — `package.json`, `tailwind.config.js`, navbar/sidebar templates with nested menu rendering.
14. **Dockerize** — `Dockerfile`, `docker-compose.yml`, `.env.example`.
15. **Basic tests** — smoke test for login and permission-gated page access.

---

### 12. Things to Decide Before Coding

- [ ] Database name & default credentials for `.env.example`
- [ ] Integer autoincrement `id` vs `UUID` for primary keys (UUID recommended if endpoints are public-facing)
- [ ] Force password change on first login (since accounts start with an admin-set password)?
- [ ] Password reset policy: admin sets it manually, or an email-based reset link later?
- [ ] Exact default menu tree (which items go under "Management", which are top-level)

---

_This plan is meant as context/prompt material for GitHub Copilot. You can copy-paste sections (e.g. the folder structure + step-by-step prompts) into Copilot Chat as initial instructions._

---

# Part 2: Image Builder Module Spec

## Project Brief: Docker Image Builder Module for Flask App

### Context

I have an existing Flask application. I want to add a new module: a Docker image
builder tool that automates the pipeline from source code to a versioned, pushed
container image. Before writing any code, please explore the existing project
structure (routes, models, templates, auth system, config pattern) and follow its
existing conventions where possible. Ask me clarifying questions if something about
the current codebase is ambiguous.

### Migration note (read first)

This document has been revised more than once. If earlier steps were already
implemented under an older version of this spec, refactor the existing models/routes
to match what's described below — most importantly the `BuildBatch` concept
introduced in this revision (see "Core Concepts"). If real data already exists in the
affected tables, write a migration; otherwise it's fine to drop and recreate.

### Core Concepts

- **Version** — a persistent, user-created identity that I set up ahead of time, not
  generated automatically at build time. It has a unique id/name, a version `type`
  (e.g. DEV, STAGING, PROD), and its own current `major`/`minor`/`patch` numbers. I
  can create several of these.
- **Builder** — a saved, reusable build configuration: a **registered** repo
  (cloned locally, not just a typed URL — see "Repo registration & persistent
  clones"), branch, Dockerfile path, registry target, build args, and exactly one
  attached `Version`. Builders are created once and reused many times — they're
  what shows up in the build list.
- **Build Batch** — one build-trigger action. It can cover a single Builder or
  several at once — but every Builder included in the same batch must share the same
  attached Version. A batch bumps that Version's number exactly once and produces
  exactly one full version string, which every image built in that batch shares.
- **Image Build** — one Builder's execution inside a Build Batch. Produces a single
  image, tagged with the batch's shared version string.

### Core Pipeline

1. I add a GitHub token connection on `/github`, then **register** the specific
   repo(s) I want to use — registering triggers an immediate, mandatory clone into
   a persistent local directory (see "Repo registration & persistent clones" below).
2. I create one or more Versions ahead of time (unique id/name, type, starting
   major.minor.patch — e.g. 0.0.0 or wherever I want to start).
3. I create one or more Builders, each attached to exactly one Version, pointing to
   a registered repo, with its Dockerfile path and registry target configured.
4. To build, I go to the Builder list and select one or more Builders to build
   together — the UI only allows selecting Builders that share the same attached
   Version (group/filter by Version so this can't be done wrong).
5. Clicking "Build" opens a form asking for:
   - **Bump type**: major / minor / patch — applied once, to the shared Version
     (see "Version bump semantics" below)
   - **Branch** — for each selected Builder, an optional override; defaults to that
     Builder's configured branch. With a single Builder selected this is just one field.
   - **Object** — manual input, shared across the whole batch, with autocomplete
     suggested from existing values
   - **Description** — manual text, shared across the whole batch, optionally
     drafted first with the AI Description Assistant (see below) and then edited
6. On submit: bump the shared Version once → compute one full version string →
   create one `BuildBatch` record → enqueue one `ImageBuild` job per selected
   Builder (each tagged with that same version string) into the existing
   single-worker queue → jobs run one after another, each syncing its Builder's
   already-registered repo (fetch + checkout + pull) rather than cloning fresh →
   the batch's overall status aggregates as each job finishes → the
   `VersionDocumentation` record for the batch is saved using what was collected in
   step 5.

### Version bump semantics

- Bumping **major** resets minor and patch to 0.
- Bumping **minor** resets patch to 0, leaves major unchanged.
- Bumping **patch** only increments patch.
- The bump happens exactly **once per Build Batch**, never once per image — that's
  what makes multiple images share the same version string.
- The datetime segment uses `%d%m%y%H%M%S` (day, month, year, hour, minute, second —
  12 digits, e.g. `220726105433` for 22 July 2026, 10:54:33), generated once per
  batch (not once per image). Seconds precision is enough for uniqueness here since
  the concurrency queue only ever runs one build at a time — no two batches can be
  created in the exact same second.
- Full version string format stays: `{TYPE}.{MAJOR}.{MINOR}.{PATCH}.{UNIQUE_DATETIME}`
  (e.g. `DEV.7.14.27.220726105433`), where `{TYPE}` comes from the attached Version's type.

### A note on "type" — three different things, keep them clearly separate

This project has three unrelated fields all called "type" in conversation. Use
distinct names in code and UI labels so they're never confused:

1. **Version Type** — DEV / STAGING / PROD (extensible), an attribute of the
   `Version` entity, forms the first segment of the version string.
2. **Bump Type** — major / minor / patch, chosen once per Build Batch, controls how
   the Version's number changes.
3. **Change Type** — New Program / Update / Bug Fix / Hot Fix / Cherry Pick
   (extensible), a documentation classification field on `VersionDocumentation`.

### Architecture Requirements (must be provider-agnostic)

- Define a `GitProvider` abstract base class with a `GitHubProvider` implementation.
  Required methods: `list_repos()` (via the API — the only method that runs before a
  repo is registered, used to populate `/github`), `clone_repo(repo_name, local_path)`
  (full clone, called once at repo registration time — see "Repo registration &
  persistent clones" below), and, operating on an already-registered repo's local
  clone rather than the API: `sync_repo(local_path, branch)` (fetch + checkout +
  pull, called at build time), `list_branches(local_path)`, `list_tags(local_path)`,
  `get_latest_commit(local_path, branch)`, and `list_files(local_path)` (used to
  power the Dockerfile picker and the branch picker described below).
- Define a `RegistryProvider` abstract base class with a `DockerHubProvider`
  implementation, built against the Docker Registry HTTP API v2 conventions so
  GHCR/Harbor/ECR can be added later without changing calling code.
  Required methods: `authenticate()`, `push_image()`, `list_tags()`, and
  `validate_credentials()` (used to sanity-check a registry entry when it's saved on
  the `/registries` page, before it can be picked in a Builder).
- Use a factory/strategy pattern so the active provider is chosen via configuration,
  never hardcoded into business logic.
- Wrap the build step behind a `BuildEngine` interface, implemented first with the
  official `docker` Python SDK (docker-py), so it could later be swapped for
  Buildah or Kaniko (e.g. for daemonless/rootless builds). The engine still runs one
  image build at a time, even within a batch — batches just mean several `ImageBuild`
  jobs queued back-to-back under one shared version string.
- Define an `AIProvider` abstract base class for the description assistant (see "AI
  Description Assistant" below), with one implementation per supported provider:
  `ClaudeProvider`, `QwenProvider`, `GeminiProvider`, and a `CustomAPIProvider` for
  any other OpenAI-compatible endpoint. Required method: `generate_description(context) -> str`.
  Selected via the same factory/config pattern as the Git and registry providers —
  never hardcoded to one AI vendor.

### Repo registration & persistent clones (for this testing phase)

Repos are no longer cloned fresh per build. Instead:

- On `/github`, after a token connection is saved, repos discovered via
  `list_repos()` show up with a **Register** action. Registering a repo is
  mandatory-clone: it immediately runs `GitProvider.clone_repo()` into a
  persistent local directory (e.g. `/data/repos/<repository_id>/`) — not optional,
  not skippable. This creates a `Repository` record pointing at that local path.
- Only **registered** repos (with a local clone already on disk) appear in the
  Builder creation form's repo picker — not the raw, un-cloned API list.
- At actual build time, the pipeline no longer clones from scratch. It runs
  `GitProvider.sync_repo(local_path, branch)` — fetch + checkout + pull — against
  the existing persistent directory, then builds from there. This is safe from race
  conditions because the concurrency design already guarantees only one build (and
  therefore only one sync) runs at a time system-wide.
- Add a manual **Re-sync** action on `/github` per registered repo, for pulling
  latest without triggering a full build — useful for testing/inspection. Re-sync
  must acquire the same lock used by the build queue (see "Concurrency Handling"
  below) before touching a repo's local clone — otherwise a manual re-sync and an
  in-progress build could run `git` commands against the same directory at once.
  If the engine is busy, Re-sync simply waits its turn like a build would.
- Since clones now persist, disk usage grows with each registered repo and never
  shrinks automatically. Add a **Remove registration** action that deletes the local
  clone and the `Repository` record — but block it (or warn clearly) if any Builder
  still references that repo, since removing it would break that Builder.
- Enforce a uniqueness constraint on `Repository` (`git_source_id` + `full_name`) so
  the same repo can't be registered twice under the same connection — that would
  otherwise produce two different local clones of the same repo with no clear way
  to tell Builders apart.

This trade-off (persistent, always-on-disk clones vs. the earlier ephemeral,
clone-per-build design) is fine for a testing phase; if this moves toward
production with many repos, it's worth revisiting since disk usage and staleness
between syncs become real concerns at that point.

### Pages / Routes

1. `/github` — manage GitHub connections used as build sources:
   - Add a connection: **Name** (label, e.g. "org-main-token"), **Token** (a GitHub
     personal access token or fine-grained token; masked input, never re-displayed
     in full after saving).
   - Once a token is saved, call `GitProvider.list_repos()` and show the list of
     repositories it has access to (name, default branch, private/public), each with
     a **Register** action.
   - Registering a repo is mandatory-clone: it triggers `GitProvider.clone_repo()`
     into a persistent local directory right away (see "Repo registration &
     persistent clones" above) and creates a `Repository` record. Show status while
     cloning (`cloning` → `ready`/`error`).
   - Once registered, show the repo's local path, last synced time, and give
     **Re-sync** and **Remove registration** actions.
   - Only registered repos are selectable later in the Builder creation form.
   - Support multiple connections (e.g. different tokens for different orgs), each
     shown with its own repo list.
2. `/registries` — manage target container registries:
   - Add an entry: **Name** (label), **Provider type** (Docker Hub now; the dropdown
     should already list GHCR/Harbor/ECR/Custom so adding them later is just
     implementing the provider class, no schema change), **Username**, **Token/password**
     (masked input, never re-displayed in full), and a **Registry URL** field (only
     required for non-Docker-Hub or self-hosted targets).
   - Run `RegistryProvider.validate_credentials()` when saving, so a bad token is
     caught immediately instead of failing on the next build.
   - This is the list a Builder's "target registry" field picks from.
3. `/versions` — list of Version entities (id/name, type, current major.minor.patch);
   for each, also show the full version string and timestamp of its most recent
   batch (join `ImageBuild` → `BuildBatch`, latest by `created_at`, if any exist yet).
   Includes a create/edit form for making new Versions, with these inputs:
   - **Name** — unique, manual text (e.g. "backend-service")
   - **Version Type** — select from the `VersionType` lookup (DEV / STAGING / PROD)
   - **Starting major / minor / patch** — numeric inputs, default `0.0.0`, editable
     in case an existing numbering scheme needs to be carried over
     A Version is created standalone — it isn't attached to a Builder here; that
     happens the other way around, when a Builder is created and picks a Version.
4. `/builders` — list of Builder entities, grouped or filterable by their attached
   Version. Each has a checkbox for multi-select (only enabled across Builders
   sharing the same Version) plus a "Build Selected" action — works the same for one
   Builder or several. Underneath each Builder, show its last build's result at a
   glance: shared version string, status, and when it ran (latest `ImageBuild` for
   that `builder_id`, via its batch). Also includes a create/edit form for Builders:
   attach a Version, pick a repo from the **registered** repos on `/github`
   (dropdown, not a typed URL — unregistered repos won't appear here), pick a
   default branch (dropdown populated from `GitProvider.list_branches(local_path)`),
   pick a Dockerfile (see "Choosing a Dockerfile" below), pick a registry from
   `/registries` (dropdown), build args.

#### Choosing a Dockerfile

A repo can have more than one Dockerfile (different services, different
environments, etc.), so this shouldn't be a blind text field:

- Once a repo is selected in the Builder form (must already be registered — see
  "Repo registration & persistent clones" above), call
  `GitProvider.list_files(local_path)` to walk the local clone and filter for
  filenames that are `Dockerfile` or match `Dockerfile.*` / `*.dockerfile`
  (case-insensitive), at any depth.
- Show the matches as a dropdown/searchable list of paths (e.g. `Dockerfile`,
  `services/api/Dockerfile`, `services/worker/Dockerfile.worker`) for the user to
  pick from, instead of typing a path from memory.
- If nothing matches (e.g. a private path pattern this project doesn't expect), fall
  back to a manual text input so the user isn't blocked.
- If the user later picks a non-default branch for a build, that's fine — the
  Dockerfile list shown here is just to help pick a starting path; the actual file
  used at build time comes from whatever branch was synced.

5. Build-trigger form — shown as a modal or dedicated small page when "Build
   Selected" is clicked: bump type (once), branch override (per selected Builder),
   object (once), description (once) — see Core Pipeline step 5.
6. `/images` — build run history: individual `ImageBuild` rows (one per image),
   each showing which Builder produced it and its own status, grouped/tagged by
   their shared `BuildBatch` (same version string, same documentation). Also shows
   the shared busy/queue status described in "Concurrency Handling" below.
7. `/documentation/<batch_id>` — full-detail documentation view/edit page for one
   Build Batch (see "Version Documentation" below), listing every image built in
   that batch (Builder name + status) alongside change type and linked versions.

### Version Documentation (mandatory for every Build Batch)

Every `BuildBatch` must have an accompanying `VersionDocumentation` record — one
documentation record covers every image built in that batch, since they share the
same version. Built by, version string, object, and description are already captured
at build-trigger time (Core Pipeline step 5) — so those fields are typically filled
in by the time the batch finishes. Change type and linked versions are filled in
afterward on the documentation detail page. Treat a record as incomplete until change
type is set — surface this with a visible "documentation pending" badge on `/images`
until it's done.

Fields on the documentation record:

- **Built by** — the user who triggered the batch. Auto-filled from the logged-in
  user/auth system.
- **Version** — the full version string shared by every image in the batch.
  Auto-filled, read-only.
- **Branches used** — display-only, derived from each `ImageBuild` in the batch
  (each image build records the branch that was actually used for it).
- **Object** — captured at build-trigger time; autocomplete sourced from distinct
  `object` values already used in prior documentation records.
- **Description** — captured at build-trigger time; can be drafted with the AI
  Description Assistant and then edited before or after the batch runs, never saved
  as raw AI output without the user seeing it.
- **Change Type** — manual selection from the `ChangeType` lookup table (New Program,
  Update, Bug Fix, Hot Fix, Cherry Pick — extensible without a code change), filled in
  on the documentation detail page.
- **Linked versions** — a documentation record can reference one or more other
  batches it incorporates. Example: `QAS.1.1.1.111111` links to `DEV.1.1.1.11111` and
  `DEV.1.1.2.111111` because that QAS batch bundles both of those DEV changes. This is
  a many-to-many relationship (batch-to-batch) and fully manual/configurable — let
  the user search and pick which prior batches to link on the documentation page;
  don't auto-infer it from git history.

### AI Description Assistant (provider-agnostic)

The description assistant must not be locked to one AI vendor. Support at least four
options, selectable through the UI, depending on configuration:

- **Qwen** — via Alibaba Cloud's official SDK/API for Qwen models. This is the
  provider to implement and wire up first, since it's the only API key currently
  available. Seed `AIProviderConfig` with Qwen configured and set as default.
- **Claude** — via the official `anthropic` Python SDK (Anthropic Messages API)
- **Gemini** — via Google's official Gemini API SDK
- **Custom API** — a generic option where the user supplies an endpoint URL, API key,
  and model name for any other OpenAI-compatible chat completions endpoint

Claude, Gemini, and Custom API can be left unconfigured/unimplemented for now — the
important part is that the `AIProvider` interface and the UI don't assume Qwen is the
only option, so adding the others later is just a new implementation class plus a new
`AIProviderConfig` row, no changes to calling code.

For all four: check each vendor's current documentation for package names and model
identifiers at implementation time rather than hardcoding them, since SDKs and model
lineups change over time.

Design notes:

- Add an **AI Provider Settings** area (its own page, or a section within existing
  app settings) where each provider can be configured: API key, model name, and
  (for Custom API) the endpoint URL. Mark one provider as the active/default choice.
- The assistant can be invoked in two places: inside the build-trigger form (to draft
  a first description before the batch even runs) and on the documentation detail
  page afterward (to refine it). Both use the same underlying call.
- Input to whichever provider is active: commit messages since each involved
  Builder's Version last built (concatenated if the batch has more than one Builder),
  the branch(es) involved, and the object field.
- Always show the AI draft in an editable text area — never save raw AI output as
  final without the user seeing and having a chance to edit it.
- Store both the raw AI draft (`ai_description`, for audit/traceability — also record
  which provider generated it) and the final saved `description` separately.

#### Editable prompt template

The prompt sent to the AI provider must not be hardcoded — make it editable in the UI:

- Store the instruction/prompt template in the database (see `PromptTemplate` model
  below), with placeholders such as `{{branch_names}}`, `{{object}}`, and
  `{{commit_messages}}` that get substituted before the call is made.
- Add a simple editor for this template in the AI Provider Settings area.
- Wherever the assistant is invoked (build-trigger form or documentation page), show
  the filled-in prompt before generating and let the user tweak it for that one
  generation without changing the saved template.

### Concurrency Handling (single build at a time, no external broker)

Keep this lightweight — no Celery/Redis for now. Instead:

- Only one `ImageBuild` executes at a time, system-wide — this stays true even for
  batches. A batch of 3 images means 3 jobs queued back-to-back, run one after
  another, not in parallel. Use a simple mechanism to enforce this, e.g. a Python
  `threading.Lock` plus a background worker thread that pulls jobs one-by-one from a
  `queue.Queue` (standard library, no external broker needed).
- When a user clicks "Build Selected":
  - All jobs from that batch are added to the queue together, in a stable order.
  - If the engine is idle, the first job starts immediately; the rest wait their turn.
  - If a build is already running (from this batch or another user's), show the
    user their batch's overall queue position and which builder/version is
    currently building, if that info is available.
- Persist queue/build state in the database (not just in memory) so status survives
  a page refresh and is visible to every user looking at `/builders` or `/images`.
- The frontend should poll a status endpoint every 2-3 seconds to update: current
  build progress/log tail, queue position, and a notification once the user's own
  queued jobs start or finish — including a batch-level "2 of 3 images built" indicator.
- Design this concurrency layer behind a small interface so it can be swapped for
  Celery/RQ later without touching the pipeline logic itself, in case build volume grows.

### Suggested Data Models

- `GitSource`: id, name, provider_type (`github`, extensible), token (encrypted at
  rest — see Security Requirements), created_at, updated_at
- `Repository`: id, git_source_id (FK), full_name (e.g. `org/repo`), local_path,
  default_branch, status (`cloning` / `ready` / `error`), last_synced_at,
  created_at, updated_at — unique on (`git_source_id`, `full_name`)
- `RegistryTarget`: id, name, provider_type (`dockerhub` / `ghcr` / `harbor` / `ecr` /
  `custom`), username, token (encrypted at rest), registry_url (only used for
  non-Docker-Hub/self-hosted), created_at, updated_at
- `Version`: id, name (unique), version_type_id (FK), major, minor, patch, created_at, updated_at
- `VersionType` (lookup, extensible): id, name, is_active — seed with DEV, STAGING, PROD
- `Builder`: id, name, version_id (FK), repository_id (FK), default_branch (this
  Builder's own default — can differ from the Repository's default_branch, e.g. a
  Builder that always builds from "release" even though the repo's default is
  "main"), dockerfile_path, registry_target_id (FK), default_build_args, created_at, updated_at
- `BuildBatch`: id, version_id (FK), full_version_string, bump_type (`major` /
  `minor` / `patch`), requested_by, status (aggregate: `queued` / `running` /
  `success` / `partial_failure` / `failed`), created_at, updated_at
- `ImageBuild`: id, batch_id (FK), builder_id (FK), branch_used, status (`queued` /
  `running` / `success` / `failed`), registry_name, image_tag, image_size, build_log,
  queue_position, started_at, finished_at
- `ChangeType` (lookup, extensible): id, name, is_active — seed with New Program,
  Update, Bug Fix, Hot Fix, Cherry Pick
- `VersionDocumentation`: id, batch_id (FK, one-to-one with `BuildBatch`), built_by,
  change_type_id (FK), object, ai_description, ai_provider_used, description,
  created_at, updated_at
- `VersionLink`: id, batch_id (FK — the batch being documented), linked_batch_id
  (FK — a prior batch it incorporates), note (optional), created_at
- `AIProviderConfig`: id, provider_type (`claude` / `qwen` / `gemini` / `custom`),
  model_name, endpoint_url (only used for `custom`), is_default, is_active
- `PromptTemplate`: id, name, template_text, is_default, is_active, updated_at

No extra fields are needed to show "last version used" on `/versions` or `/builders`
— it's a query against `ImageBuild` joined to `BuildBatch` (latest row per
`version_id` or `builder_id`, ordered by `created_at` descending), not a new column.
Add indexes on `ImageBuild.batch_id`, `ImageBuild.builder_id`, and
`BuildBatch.version_id` if this project doesn't already index foreign keys by default.

Adjust field names/types to match this project's existing model conventions.

### Security Requirements

- Tokens entered on `/github` and `/registries` are stored in the database (they
  need to be, since there can be several of them) — but never in plaintext. Encrypt
  them at the application level before saving (e.g. with Python's `cryptography`
  `Fernet`), using a master key that itself lives in an environment variable (e.g.
  `SECRET_ENCRYPTION_KEY`) — never hardcoded, never checked into version control.
- After saving, never re-display a stored token/password in full — show it masked
  (e.g. last 4 characters only) if it needs to appear in the UI at all.
- Encrypt any other credential that must be persisted, following the same pattern.
- Persistent repo clones under `/data/repos/...` (or wherever this project's
  equivalent path is) may contain private source code — keep this path outside any
  publicly-served static directory and restrict OS-level file permissions to the
  app's own user.
- Reuse the app's existing auth/role system to gate who can add/edit Git connections,
  registries, create Builders, trigger builds, and edit documentation, if one
  already exists.
- Store the API key for each configured AI provider (Claude, Qwen, Gemini, or Custom
  API) using the same encrypted-at-rest pattern as Git/registry tokens.

### Suggested Tech Stack

- Backend: Flask, SQLAlchemy
- Credential encryption: `cryptography` (Fernet symmetric encryption) for tokens
  stored on `/github`, `/registries`, and AI provider configs
- Concurrency: Python standard library (`threading`, `queue`) — no external broker
  needed at this stage
- Git integration: GitPython or PyGithub
- Docker integration: `docker` (Docker SDK for Python)
- AI description generation (pick per configured provider): `anthropic` SDK (Claude),
  Alibaba's Qwen SDK/API, Google's Gemini API SDK, and a generic HTTP client
  (`requests` or `httpx`) for the Custom API option
- Status updates: simple polling endpoint (JS `fetch` on an interval); Flask-SocketIO
  only if it's already used elsewhere in this project
- Frontend: match this project's existing templating/styling approach (Jinja2 or
  existing JS framework — check before introducing a new one)

### Suggested Implementation Order

1. Refactor/migrate data models to the structure above — `GitSource`,
   `Repository`, `RegistryTarget`, `Version`, `VersionType`, `Builder`,
   `BuildBatch`, `ImageBuild`, `ChangeType`, `VersionDocumentation`, `VersionLink`,
   `AIProviderConfig`, `PromptTemplate` — replacing whatever earlier steps produced
   under older versions of this spec.
2. Encryption helper (`cryptography`/Fernet) for storing/retrieving tokens, backed
   by `SECRET_ENCRYPTION_KEY` — needed before the credential-holding pages below.
3. `GitProvider` / `RegistryProvider` / `AIProvider` abstractions + one implementation
   each (GitHub, Docker Hub, and Qwen for AI, since that's the only API key available
   now — add Claude/Gemini/Custom API later, the abstraction already supports it).
4. `/github` page: add/list Git connections, list available repos via
   `list_repos()`, and the Register action that clones into a persistent directory
   and creates a `Repository` row (plus Re-sync / Remove registration).
5. `/registries` page: add/list registry targets, validate on save.
6. Version bump function: given a `Version`, a bump type, and the version type
   prefix, compute the new major/minor/patch and full version string. Called once per
   batch. Pure, unit-testable.
7. `BuildEngine` wrapper around docker-py.
8. `/versions` page: create/list Versions.
9. `/builders` page: create/list Builders (each attached to a Version, a
   registered Repository, and a saved registry target), with the Dockerfile picker
   reading the local clone, and multi-select constrained to Builders sharing the
   same Version.
10. Build-trigger form (bump type once, per-builder branch override, object once,
    description once) + worker thread/queue from "Concurrency Handling" that creates
    one `BuildBatch` and its `ImageBuild` jobs, runs them one after another (each
    syncing its repo rather than cloning fresh), and updates status (including
    batch-level "X of Y built") as it progresses.
11. `/images` page with live status/queue display (polling endpoint, or SocketIO if
    already used in this project), grouped by batch.
12. `/documentation/<batch_id>` page: change type, linked versions, list of images in
    the batch, and any further edits to object/description.
13. AI Provider Settings page (Qwen configured now, others later) with the editable
    prompt template, wired into both the build-trigger form and the documentation page.
14. Wire up auth/permission checks across Git connections, registries, Builders,
    build triggers, and documentation edits.

### Deliverable

Please implement this incrementally, committing logically grouped changes, and
summarize what was added/changed at the end along with any manual setup steps I need
to take (e.g. environment variables, database migration commands).

---

# Part 3: Image Builder Migration Progress Log

## Image Builder Migration — Progress Notes

> **Scope note:** this file started as progress notes for the 14-step Image Builder migration (Sections 1–14 below). That migration is done. Everything under **"Post-migration follow-up work"** further down is a separate log of feature requests/bug fixes that landed afterward in the same working session — some inside the image builder module, some general-purpose app features (Error Logs). Read this whole file top to bottom when resuming; the "How to resume" section at the very bottom is the current entry point.

**Task:** Migrating this repo's Docker Image Builder module from an older spec to a revised one (`image-builder-prompt.md`) built around a `BuildBatch` concept — one build-trigger action can cover several `Builder`s sharing one `Version`, bumping that Version's number once and producing one shared version string across all images in the batch.

Full gap analysis and 14-step plan are written to `/home/daniirsyad/.claude/plans/read-image-builder-prompt-md-in-this-immutable-valiant.md` — **read that file first** when resuming. It has the complete context: migration decisions, permission naming scheme, critical files, verification approach, etc.

**Working style:** one numbered step at a time, testing after each, stopping for go-ahead between steps (though steps have recently been batched multiple-per-turn — most recently "6 to 8" then "9 to 12").

### Completed (Steps 1–14 of 14 — migration finished)

1. **Data models & migration** — new `GitSource`, `Repository`, `RegistryTarget`, `VersionType`, `Version`, `Builder`, `BuildBatch`; rewrote `ImageBuild`/`VersionDocumentation`/`VersionLink` around batches; dropped `BuildVersion`/`BuilderConfig`. One migration applied to dev+test DBs.
2. **Encryption helper** moved `app/services/ai/crypto.py` → `app/utils/crypto.py`.
3. **Provider abstractions** — `GitProvider`/`GitHubProvider` rewritten stateless-per-call (`list_repos()`, `clone_repo(repo_name, local_path)`, `sync_repo()`, `list_branches(local_path)` etc., plus a shared `list_files()` on the base class for the Dockerfile picker). `RegistryProvider`/`DockerHubProvider` gained `validate_credentials()`.
4. **`/github` page** — `git_sources_bp`: connections, live repo listing, Register/Re-sync/Remove.
5. **`/registries` page** — `registries_bp`: `RegistryTarget` CRUD with credential validation.
6. **Version bump function** — `app/services/build/versioning.py` rewritten: `bump_version(version, bump_type)` mutates a `Version` row in place, new `%d%m%y%H%M%S` datetime format.
7. **`BuildEngine`** — verified needs no changes (model-agnostic).
8. **`/versions` page** — `versions_bp` rewritten as `Version`-entity CRUD (dropped old per-version documentation route/template).
9. **`/builders` page** — new `builders_bp`: Builder CRUD with cascading repo→branch/dockerfile AJAX lookups (`app/static/js/builders.js`), same-Version multi-select "Build Selected" UI.
10. **Build-trigger + worker rewrite** — `app/services/build/worker.py` fully rewritten (`enqueue_build_batch`, `_run_build` now syncs instead of clones, `_update_batch_status` aggregates batch status, `get_batch_progress` for "X of Y built"). Build-trigger modal lives on `/builders`, posts to `/builders/build`. Worker re-enabled in `app/__init__.py`.
11. **`/images` page** — `images_bp` rewritten to group `ImageBuild` rows by `BuildBatch`: each batch shows its shared version string, bump type, aggregate status, requester, and a "Documented"/"Documentation Pending" badge (keyed off `VersionDocumentation.is_complete`, i.e. `change_type_id` being set); nested table of that batch's individual `ImageBuild` rows (builder, branch, status, image tag/size/registry link, timestamps). New `/images/status` JSON endpoint + `app/static/js/images-status.js` poll for the live busy/queue indicator. `/builders/build`'s success redirect now correctly points to `images.list_images`.

Old `app/blueprints/builder/` (singular, `BuilderConfig`-preset based) and its templates/JS were deleted, fully replaced by `builders_bp`. Old `tests/test_builder_routes.py` deleted, fully replaced by `test_versions.py`/`test_builders.py`/`test_images.py`.

**Permissions/menu added through Step 11:** `gitsource.manage`, `registry.manage`, `version.manage`, `builder.view`, `builder.manage`, `builder.build`, `image.view` (pre-existing, reused); menu entries GitHub/Registries/Builders under "Image Builder". (`version.edit` from the old spec was still present but orphaned at this point — removed in Step 14, see below.)

12. **`/documentation/<batch_id>` page** — new `documentation_bp` (perm `documentation.edit`, added to `seed_admin.py`). `VersionDocumentation` rows are already created up front by `enqueue_build_batch()` (Step 10), so the route is view/edit only — `_get_or_create_doc()` is a defensive fallback for hand-built batches, not the normal path. Shows: images in the batch (builder + status), read-only Built by/Version/Branches used, an Object field (autocomplete from prior distinct values), a Change Type select (from `ChangeType` lookup), a Description textarea, an AI-assist panel (reused pre-existing `app/static/js/ai-generate.js`, wired to `#ai-generate`/`#ai-provider-select`/`#ai-prompt-preview`/`#ai-draft-*` DOM hooks that were already built to that contract), and a Linked Batches checkbox picker (writes/prunes `VersionLink` rows, excludes self from choices). New `app/services/ai/context.py::gather_batch_ai_context(batch)` concatenates commit messages per involved Builder/branch via `get_commit_messages(local_path, since_ref=None)` (bounded-recent-log fallback — the new schema has no per-build commit-SHA field, a deliberate simplification vs. the old spec), skipping any Builder whose repo can't be read rather than failing the whole page. Pulled forward from Step 13: `seed_ai_provider.py`'s default `PromptTemplate` text now uses `{{branch_names}}`/`{{object}}`/`{{commit_messages}}` (dropped `{{type}}`) since Step 12's rendering call needed the new names to work — note this only affects *new* seeds, an already-seeded dev DB row keeps its old text until manually edited. `images/list.html`'s "Documentation Pending"/"Documented" badge is now a real link to `/documentation/<batch_id>`.

Tests: `tests/test_version_documentation.py` fully rewritten against `BuildBatch`/`VersionDocumentation`/`VersionLink` (24 tests, including 2 for `gather_batch_ai_context` directly). 161 passing total.

13. **AI Provider Settings + build-trigger AI wiring** — `ai_settings_bp` needed no changes (confirmed already spec-compliant). The real work was wiring the AI Description Assistant into the `/builders` build-trigger modal, which didn't have one yet — but that modal has no `BuildBatch` at the point a draft would be generated (Builder/branch selection is assembled client-side in `builders.js`, before submit), unlike the documentation page which already has one. Refactored `app/services/ai/context.py` to split out a lower-level `gather_ai_context(builder_branches)` (list of `(Builder, branch)` tuples) with `gather_batch_ai_context(batch)` now a thin wrapper over it — reused by both call sites. Added `render_default_prompt(context, object_value)` to `app/services/ai/prompt.py` and `default_provider_type()` to `app/services/ai/factory.py`, and refactored `documentation/routes.py` to use both instead of its own local copies (removes duplication between the two blueprints' generate/preview logic). New `builders_bp` routes: `POST /builders/prompt-preview` (AJAX-rendered prompt from a not-yet-submitted builder/branch/object selection) and `POST /builders/generate` (same shape as `documentation`'s generate endpoint). `builders.js` fetches `/builders/prompt-preview` when the trigger modal opens and whenever a branch-override or Object field changes; the modal reuses the same `ai-generate.js` module and DOM contract as the documentation page. `enqueue_build_batch()` gained optional `ai_description`/`ai_provider_used` kwargs, threaded through from the trigger form's hidden fields into the batch's `VersionDocumentation` row at creation time. Tests: 12 new (`tests/test_builders.py` — AI draft passthrough on `/builders/build`, `TestPromptPreview`, `TestGenerateDescription`, permission gating; `tests/test_worker.py` — `TestEnqueueBuildBatch` for the new kwargs).

14. **Permission/menu/log_activity audit** — audited every `permission_required(...)` call site against `seed_admin.py`'s `BASE_PERMISSIONS`: every permission actually referenced in code was already seeded, with exactly one exception — `version.edit`, orphaned since Step 12 replaced its old "version documentation" meaning with `documentation.edit`. Removed `version.edit` from `BASE_PERMISSIONS` (no code referenced it). Menu audit: `seed_menu.py` already had every current top-level page (GitHub/Registries/Versions/Images/Builders/AI Settings) — no changes needed; `/documentation/<batch_id>` deliberately has no sidebar entry (per-batch, reached via the `/images` badge link, not a listing page). `log_activity(...)` audit: every create/update/delete/register/re-sync/remove/trigger route across all blueprints already calls it (confirmed by grep, no gaps found — this had been kept current step-by-step rather than left for a final pass).

**173 tests passing total.** App boots clean with all 14 blueprints registered (`python -c "from app import create_app; create_app('testing')"`).

### Migration complete

All 14 steps of the plan at `/home/daniirsyad/.claude/plans/read-image-builder-prompt-md-in-this-immutable-valiant.md` are done. Nothing outstanding from the plan itself. Possible follow-ups if picked up later (not required by the spec):
- Claude/Gemini/Custom API `AIProvider` implementations are still stubs (`aiprovider.manage` UI lists them as "not yet implemented") — spec explicitly allows leaving these unconfigured.
- GHCR/Harbor/ECR `RegistryProvider` implementations are similarly unimplemented stubs, per spec.
- An already-seeded dev DB's `PromptTemplate` row still has the pre-Step-12 `{{branch_name}}`/`{{type}}` placeholder text, since seeding is idempotent (create-if-missing) — only fresh databases get the new default text automatically; edit the existing row by hand via `/ai-settings` if picking up this environment.

### Post-migration follow-up work (this session, after Step 14)

Six follow-up items, done in order, each tested and passing before moving to the next:

1. **Versions page: delete + wider modals.** `POST /versions/<id>/delete` (perm `version.manage`), blocked if any `Builder` still references the version (`Builder(s) still reference it` flash) — same guard pattern as the pre-existing git-source/registry delete blocks. Delete button + confirmation modal added next to Edit in `versions/list.html`. Both create/edit modals widened `max-w-lg` → `max-w-2xl` (form felt cramped with the Version Type text field + 3 number inputs). 5 new tests in `tests/test_versions.py`.

2. **GitHub connections: edit + delete.** `POST /github/connections/<id>/edit` and `POST /github/connections/<id>/delete` (perm `gitsource.manage`, matching the existing registries edit/delete pattern). Edit lets you rename and optionally rotate the token (blank = keep current — mirrors `RegistryTargetForm`). Delete is blocked if any `Repository` is still registered under that connection. `GitSourceForm.token` changed from `DataRequired` to `Optional`; the "a token is required to add a new connection" check now lives explicitly in `create_connection()` instead of the form validator, since the same form class is shared with edit. 8 new tests in `tests/test_git_sources.py`.

3. **`REPO_CLONE_ROOT` moved inside the app directory, per explicit instruction.** Was `/data/repos` (top-level path, backed by a docker-compose named volume `repo_clones`). Now defaults to `<app>/data/repos` — `os.path.join(basedir, "data", "repos")` in `config.py`. The `repo_clones` named volume and its mount were **removed from `docker-compose.yml`** — the user will mount their own persistent storage onto that directory later; right now it's just a plain directory inside the container's writable layer, wiped on every recreate. Updated the `.env.example` comment and added `/data/` to `.gitignore` (so local non-Docker runs don't accidentally track cloned repos in git).
   - **Known, only-partially-mitigated consequence:** without a real mount, every container restart loses every registered repo's local clone. Item 6 below (self-healing sync) covers Re-sync and the build pipeline, but the Builder create/edit form's branch/Dockerfile pickers (`GET /builders/api/repo-info/<id>`, and `_repo_info()` used when rendering `/builders`) and the `/github` page's unregistered-repo listing do **not** self-heal — they just show empty results (now via `log_error`, not a crash) until the next successful `sync_repo()` call repopulates the clone.

4. **Error Logs feature — new, general-purpose (not image-builder-specific).** Prompted by: "theres no error or accident logs in this apps, add this new page and make all feature when got error to save into this log."
   - New `ErrorLog` model (migration `6de15d4c3766_add_error_logs_table.py`): `source`, `message`, `traceback`, `user_id`, `method`, `path`, `ip_address`, `created_at`.
   - `app/utils/error_logger.py::log_error(source, exc=None, description=None)` — shared logging utility. Doesn't force-rollback the caller's db session up front (would risk discarding real pending writes the caller still needs); only rolls back and retries once if the insert itself fails because the session was already broken.
   - **Global safety net:** any genuinely unhandled exception anywhere in the app is now logged automatically via Flask's `got_request_exception` signal, connected inside `create_app()` in `app/__init__.py`. Normal `abort(403)`/`abort(404)` flow (e.g. `permission_required`) never reaches this signal, so it doesn't spam routine access-control denials as "errors".
     - **Gotcha hit and fixed:** the signal connection must use `weak=False`. The handler is a closure local to `create_app()`; blinker's default *weak* reference let it get garbage-collected the instant `create_app()` returned, silently disabling the whole feature. Caught by writing a test that actually triggers an unhandled exception through the test client and asserts an `ErrorLog` row exists — the assumption "the signal fires" turned out to be wrong until that test was added.
   - Explicitly wired `log_error(...)` into every pre-existing `except Exception` swallow site so detail isn't lost silently: `git_sources` (`_list_repos_for`, `register_repo`, `resync_repo`), `registries` (`_validate_credentials`), `builders` (`_repo_info`, `repo_info` AJAX route, `generate_description`), `documentation` (`generate_description`), and — most importantly — **both** failure paths inside the background build worker's `_run_build` (the "Docker build itself failed" branch and the outer catch-all), since that thread has no HTTP request to hang an error off of otherwise; that's the one `log_error` call that has to work with `has_request_context() == False`.
   - New `/logs/errors` page (perm `logs.view` — reused the existing Activity Logs permission rather than adding a new one, since it's the same "who can see system logs" concern) with source/date-range filtering and an expandable `<details>` traceback per row. Added a tab switcher between "Activity"/"Errors" on both `/logs` and `/logs/errors`. New "Error Logs" menu entry seeded under "Logging" in `seed_menu.py` (already run against the dev DB).
   - 9 new tests in `tests/test_error_logs.py`, plus `ErrorLog`-row assertions added into the relevant existing tests in `test_worker.py`, `test_git_sources.py`, `test_builders.py`, `test_version_documentation.py`.

5. **Copy-to-clipboard button on tracebacks.** Each traceback on `/logs/errors` now has a small square button next to it — two inline SVG icons (clipboard outline, swaps to a checkmark for 1.5s on click via the Clipboard API). New `app/static/js/copy-to-clipboard.js`, following the same per-page vanilla-JS-file convention as `ai-generate.js`/`builders.js`/`images-status.js`. 2 new tests (button + `data-copy-target` render when a traceback exists; absent when it doesn't).

6. **Bug fix: registered-repo sync crashed hard when the local clone was missing.** Direct fallout of item 3 — this is the actual bug the user pasted a traceback for (`git.exc.NoSuchPathError` from `resync_repo`), found via the brand-new Error Logs page. `git.Repo(local_path)` raises `NoSuchPathError` (not `InvalidGitRepositoryError`) when the directory doesn't exist at all; `GitHubProvider._open_repo()` only caught the latter, so the raw GitPython exception propagated all the way up. Fixed two ways:
   - `_open_repo()` now catches `NoSuchPathError` too → a clean `RuntimeError`, never a raw crash.
   - **Self-healing:** `GitProvider.sync_repo()` gained an optional `repo_name` parameter (abstract signature updated in `app/services/git/base.py`). When given, if the local clone is missing or broken (not a valid git repo), `sync_repo()` `rmtree`s whatever's there and re-clones fresh into the same path before syncing, instead of failing. Both call sites now pass it: `git_sources.resync_repo` (`repo_name=repository.full_name`) and `worker._run_build` (same). This is the practical mitigation for item 3's "no persistent mount yet" gap — Re-sync and real builds now recover on their own; only the passive pickers noted in item 3's callout still don't self-heal preemptively.
   - 10 new tests: `test_git_provider.py` (`TestSyncRepoSelfHeals`, `TestOpenRepoErrors` — direct provider-level tests using local bare git repos, no network access, following the file's existing convention), `test_git_sources.py` (route passes `repo_name` through), `test_worker.py` (worker passes `repo_name` through; `ErrorLog` assertions on both failure branches).

**204 tests passing total** (was 173 right after Step 14; +31 from these six items). Full suite: `FLASK_ENV=testing python -m pytest tests/ -q` (env setup at the bottom of this file).

**Nothing in this repo is committed yet** — `git log` still shows only the original "first commit" from before the migration started. Everything described in this entire file (the full 14-step migration *and* all six follow-up items) is uncommitted working-tree state. Worth flagging to the user before any destructive git operation, and worth committing in sensible chunks whenever they're ready — not done proactively since committing wasn't asked for.

### How to resume

Read this whole file first — there's no separate plan file to consult anymore (the original plan at `/home/daniirsyad/.claude/plans/read-image-builder-prompt-md-in-this-immutable-valiant.md` only covers Sections 1–14 above, not the six follow-up items). For new work, start fresh from the current code/tests. Useful jumping-off points if picking this back up:
- `app/utils/error_logger.py` + the `got_request_exception` wiring in `app/__init__.py` if extending error logging further (e.g. logging validation-flash failures too, not just exceptions — deliberately out of scope so far, see the "Log Error Utility" reasoning above).
- `app/services/git/github.py`'s `sync_repo`/`_repo_exists` if extending self-healing to the passive pickers noted in item 3.
- `config.py`'s `REPO_CLONE_ROOT` + `docker-compose.yml` if/when real persistent storage gets mounted — nothing else needs to change, the app already reads the path from config rather than hardcoding it anywhere.

#### Test/DB setup reminder

The sandbox can't reach the DB via raw TCP to `db:5432` (the Docker Compose hostname) — connect via the WSL2 gateway instead:
```bash
export DATABASE_URL=$(echo $DATABASE_URL | sed 's/@db:/@172.29.16.1:/')
export TEST_DATABASE_URL=$(echo $TEST_DATABASE_URL | sed 's/@db:/@172.29.16.1:/')
```
Run tests with `FLASK_ENV=testing python -m pytest tests/ -q` (after `source .venv/bin/activate` and `set -a && source .env && set +a`).

---

# Part 4: Post-Migration Follow-Up Sessions (was SESSION_START.md's historical sections)

This part covers the sessions that ran after Part 3's migration finished —
System Configuration, Kaniko build engine, version-bump-at-claim-time rework,
auto-documentation, log-viewer XSS/UX fixes, error-log detail capture, and the
BuildKit rewrite of `DockerBuildEngine`. It used to live directly in
`SESSION_START.md`; moved here since it's history, not current state — see
`SESSION_START.md` itself for what's true *now* and any work that came after it.

### 1. Important state to know before doing anything

- **255 tests passing**, run via:
  ```
  source .venv/bin/activate && set -a && source .env && set +a
  export DATABASE_URL=$(echo $DATABASE_URL | sed 's/@db:/@172.29.16.1:/')
  export TEST_DATABASE_URL=$(echo $TEST_DATABASE_URL | sed 's/@db:/@172.29.16.1:/')
  FLASK_ENV=testing python -m pytest tests/ -q
  ```
  (the sandbox can't reach the `db` Docker Compose hostname directly — that sed
  swaps it for the WSL2 gateway IP)
- **Nothing in this repo is committed yet** — `git log` only shows the original
  "first commit" from before any of this work started. Everything is
  uncommitted working-tree state. Don't run any destructive git command without
  checking with me first, and don't commit anything unless I explicitly ask.
- **Docker access now works** in this environment — earlier in the session that
  produced section 3 below, the sandbox host user lacked `docker` group
  membership (`PermissionError(13)` talking to `/var/run/docker.sock`), but a
  live build triggered later in that session completed successfully
  end-to-end (build + push), so that's since been resolved (outside this
  repo — a host-level group membership fix).
- **Deployment model (clarified in the section-4 session): the "docker" build
  engine runs bare-metal on the host**, not inside a container — the app
  process shells out to the host's own `docker` CLI over the mounted socket.
  The "kaniko" engine is the one meant to run containerized (daemonless, no
  docker.sock needed). Because of this, `buildx` (needed for the "docker"
  engine now that it uses BuildKit — see section 4 item 3) was installed
  directly on the dev host at `~/.docker/cli-plugins/docker-buildx` (v0.36.1,
  no root needed) — that's the thing that actually matters for local
  "docker"-engine builds to work, not the Dockerfile's own copy of buildx
  (kept only as an unused fallback for a containerized "docker" engine, per
  explicit choice not to remove it).
- A live smoke test during this session **actually pushed a real image** to the
  configured Docker Hub registry: `hamiltondev/hamilton-ai:DEV.2.0.1.070826025821`.
  The DB rows created for that test were cleaned up and the Version number was
  restored, but the pushed registry tag itself was not — delete it from Docker
  Hub if you don't want it there.
- REPO_CLONE_ROOT still has no real persistent storage mounted onto it —
  registered repo clones can still be lost on container restart. Re-sync and
  builds self-heal from this; a couple of passive UI pickers still don't (see
  image-builder-progress.md item 3 for exactly which ones — unchanged this
  session).

### 2. Quick orientation on new pages/permissions added this session

- `/config` — System Configuration (timezone, session timeout, build engine).
  Permission `system.manage`. Sidebar group "System".
- `/documentation` — new list page of all documented (= fully successful)
  build batches. Permission `documentation.edit` (existing). Sidebar entry
  added under "Image Builder".
- Builders page (`/builders`) now groups by an explicit `Builder.group_name`
  instead of checkboxes; every row has its own Build/Edit/Delete buttons.

### 3. Everything built in the session after image-builder-progress.md

Roughly in order:

1. **System Configuration page** (`/config`, `system.manage` perm) — new
   `SystemConfig` singleton model/table. Two settings beyond what's listed in
   section 2:
   - **Timezone**: every timestamp rendered anywhere in the app now goes
     through a new `localtime` Jinja filter (`app/utils/timezone.py`) instead
     of raw `.strftime()`, converting the stored naive-UTC datetime into the
     configured zone. Verified live (correct UTC+7 offset against a real clock
     check).
   - **Session Timeout (minutes)**: `session.permanent = True` set at login;
     `app.permanent_session_lifetime` refreshed from this DB value on every
     authenticated request (`before_request` in `app/__init__.py`), so idle
     sessions expire after N minutes and changes take effect without a
     restart.
   - Found and fixed a latent bug while building this: `tailwind.config.js`'s
     `content` glob only ever scanned `.html` templates, never `.js` files —
     so Tailwind classes used *only* inside JS-generated markup (log-viewer
     `<pre>` boxes on `/builders` and `/images`) were silently missing from
     the compiled CSS the whole time, in dev and in the Docker build. Fixed
     the glob (`app/static/js/**/*.js` added) and the Dockerfile's asset-stage
     `COPY` list.

2. **Kaniko build engine** — new `KanikoBuildEngine` (`app/services/build/engine.py`),
   selectable via `/config`'s Build Engine field (Docker stays default).
   Daemonless/rootless: shells out to a `kaniko-executor` binary, builds *and*
   pushes in one step (no separate `docker.sock` access needed at all), which
   was the whole motivation — this session started with a real
   `PermissionError(13)` hitting Docker's socket. Dockerfile now multi-stage
   `COPY`s the executor binary from `gcr.io/kaniko-project/executor:v1.23.2`.
   `app/services/build/factory.py::get_build_engine()` resolves which engine
   to use per-build from `SystemConfig.build_engine`.

3. **Bug fix: image builds succeeded but silently never pushed, no error shown.**
   Root cause was two compounding pre-existing bugs in `worker.py`/`dockerhub.py`
   (predating this session's other build-engine work): the image was built
   locally tagged as bare `image_name:version`, but the code then tried to
   push a *different*, never-built tag (`full_repository_name(image_name):version`)
   — and `DockerHubProvider.push_image()` never checked docker-py's push event
   stream for `{"error": ...}` entries, so the failure was swallowed silently.
   Fixed both: the image now builds directly under the exact tag that gets
   pushed, and `push_image()` raises on any push error (caught by the
   worker's existing error handling).

4. **Custom Docker image name** — new optional `Builder.image_name` column;
   overrides the auto-derived name (still just the repo's last path segment
   when left blank).

5. **Bug fix: couldn't pick a default branch when only one repo existed.**
   `builders.js`'s repo→branch cascade only populated on the repo `<select>`'s
   `change` event; with exactly one repo the browser auto-selects it and
   `change` never fires. Now also loads eagerly on init when a repo is
   selected but no branches are loaded yet.

6. **Builders page redesign** — replaced the checkbox multi-select +
   "Build Selected" flow with grouping by an explicit, free-text
   `Builder.group_name` (assigned on create/edit, autocomplete from existing
   names, independent of Version — groups *can* span Versions; a mixed-Version
   group just surfaces the existing "must share the same Version" error when
   actually built, per explicit choice). Ungrouped builders show separately,
   no group-level button. Every row now has its own **Build** (single-builder
   batch) and **Delete** button (new `POST /builders/<id>/delete`, blocked if
   the builder has any build history).

7. **Versioning/documentation rework** (the biggest chunk, several linked
   decisions confirmed with me along the way):
   - **Version bump deferred from click-time to worker-claim-time.**
     `enqueue_build_batch()` no longer bumps the Version or assigns
     `full_version_string` — that now happens in `_claim_next_job()`, the
     moment the batch's first image is actually claimed to run (safe to do
     unconditionally there since only one build ever runs system-wide at a
     time). `BuildBatch.full_version_string` is nullable now; displays as
     "pending" wherever it might not be assigned yet.
   - **Failed batches roll the version back**; `BuildBatch` gained
     `bumped_from_major/minor/patch` snapshotted at bump time,
     restored in `_update_batch_status()` if every image in the batch fails.
     A batch where *any* image succeeded keeps its bump (a real image is
     already pushed under that number).
   - **VersionDocumentation is only ever created once a batch fully succeeds**
     (in `_update_batch_status`) — never up front, never for a failed or
     partial batch. `object`/`change_type_id`/`additional_description` are
     staged directly on `BuildBatch` at trigger time and copied over once the
     doc is actually created.
   - **"Linked Batches" on the documentation page excludes non-successful
     batches** (`status == "success"` filter).
   - **New `/documentation` list page** (see section 2).
   - **Change Type is now chosen in the build-trigger modal** (still editable
     afterward on the documentation page too, in case it's skipped).
   - **Build-trigger modal**: "Description" renamed "Additional Description";
     the old AI prompt-preview/"Generate Draft" UI removed entirely from this
     modal. An AI description is now generated automatically (server-side, in
     the worker) the moment a batch succeeds, and combined with whatever
     "additional description" was typed in — still fully editable afterward
     on the documentation page. `/builders/prompt-preview` and
     `/builders/generate` routes were deleted (dead code once the modal's own
     AI widget was removed; the documentation page's own AI-assist widget is
     unchanged and still works standalone).
   - Found and fixed a **real concurrency bug** while testing this: in
     `_claim_next_job()`, accessing `build.batch`/`batch.version` (lazy
     relationships) between setting `build.status = "running"` and the
     explicit `db.session.commit()` triggered a premature SQLAlchemy
     autoflush of that pending change — which could raise an uncaught
     `IntegrityError` outside the function's own try/except when two builds
     were claimed in quick succession, instead of backing off cleanly. Fixed
     with `db.session.no_autoflush`.
   - Verified live end-to-end against the real dev DB and real Docker: queued
     with no version string → bumped only once claimed → built and pushed for
     real → `VersionDocumentation` auto-created combining a real AI-generated
     summary with a typed note. (This is the run that pushed the stray image
     tag mentioned above.)

8. **Build-status log viewers** (`/builders` and `/images`, both pollers) —
   previously replaced their entire `innerHTML` (including the log `<pre>`)
   on every 3s poll, which reset scroll position and any manual resize every
   time, and interpolated build-log/builder-name/version text directly into
   `innerHTML` unescaped (a stored-XSS-shaped gap if build output ever
   contained HTML-like text). Both now: only rebuild their DOM skeleton on an
   idle↔busy transition; always scroll to the bottom on refresh; are
   manually resizable (`resize-y`); use `textContent` instead of raw HTML
   interpolation for all dynamic values.

### 4. Follow-up fixes (most recent session — all bugs hit/reported, not planned work)

In order:

1. **Error Logs: build failures with no Python exception weren't capturing
   any detail.** `worker.py`'s `_run_build()` has two failure branches: a
   "clean" `BuildResult(success=False, ...)` returned by the build engine
   (no Python exception — e.g. Docker's build API returning
   `{"error": ...}`), and a genuine `except Exception`. Only the second ever
   passed `exc=` into `log_error()`, so the first left `ErrorLog.traceback`
   permanently `NULL` — confirmed against two real rows already in the dev
   DB from an actual `--mount`/BuildKit failure (see item 3). Fixed:
   `log_error()` gained an optional `detail` param, stored in the same
   `traceback` column when there's no exception object; `worker.py` now
   passes `detail=result.log` (the full build log) on that branch.

2. **`/logs/errors` per-row detail now opens in a modal**, replacing the old
   inline `<details>` expander — shows the full message, request context,
   and traceback/log (with the existing copy button) in one place.
   `errors.html` rewritten accordingly; `tests/test_error_logs.py` updated to
   match.

3. **BuildKit fix: `DockerBuildEngine` rewritten to shell out to
   `docker buildx build --load`** instead of docker-py's low-level
   `client.api.build()`. Root cause of a real failure: a group build failed
   with `the --mount option requires BuildKit` — docker-py's low-level build
   API only ever talks to Docker's legacy (non-BuildKit) builder; no env var
   or option makes it understand BuildKit-only Dockerfile syntax
   (`RUN --mount=...`, heredocs, ...). New engine shells out via
   `subprocess` (same pattern as the existing `KanikoBuildEngine`); `client`
   (docker-py) is kept only for inspecting the built image afterward and for
   the worker's separate registry-push step.
   - Follow-up bug caught immediately after, from a real "Dockerfile not
     found" error: `buildx build`'s `--file` flag resolves relative to the
     **process's cwd**, not the build context (unlike docker-py's old
     behavior, which always resolved it relative to `path`) — fixed by
     joining `dockerfile_path` against `context_dir` explicitly before
     passing it to `--file`.
   - `Dockerfile` updated to copy a buildx-capable `docker` CLI from the
     official `docker:27-cli` image (`/usr/local/bin/docker` + the
     `docker-buildx` cli-plugin) instead of apt's plain `docker.io` package,
     which doesn't bundle buildx — see section 1's deployment-model note for
     why this ended up being a fallback, not the actual fix.
   - `tests/test_build_engine.py`'s `DockerBuildEngine` tests rewritten
     around the subprocess call (argv assertions, stdout streaming, nonzero
     exit, missing binary, dockerfile-path-joining) instead of the old
     docker-py stream-chunk mocks.

4. **Two stale-status UI bugs fixed.** Both `/images` and `/builders` poll a
   `/status` JSON endpoint every 3s, but only ever updated a small "Build
   Status" widget — the actual batch/build table (and each builder row's
   "Last Build" column) is static server-rendered HTML from page load, so it
   never reflected a build finishing without a manual refresh. Both
   `images-status.js` and `builders.js` now reload the page on a busy→idle
   transition (a build that was running when the page loaded/polled just
   finished), so the table catches up automatically.

5. **Builders page: grouped builders no longer get an individual per-row
   "Build" button** — only the group's "Build Group" button remains for
   them, per explicit request (grouped builders should only ever be built as
   a group). Ungrouped builders keep their own per-row Build button,
   unchanged. `data-builder-id`/`-name`/`-default-branch` moved onto each
   row's `<tr>` itself (`class="builder-row"`) so `builders.js`'s "Build
   Group" handler can still read builder identity per row without the
   button being present.

**255 tests passing total** (was 254 at the start of this follow-up work).

#### Test/DB setup reminder

The sandbox can't reach the DB via raw TCP to `db:5432` (the Docker Compose
hostname) — connect via the WSL2 gateway instead:
```bash
export DATABASE_URL=$(echo $DATABASE_URL | sed 's/@db:/@172.29.16.1:/')
export TEST_DATABASE_URL=$(echo $TEST_DATABASE_URL | sed 's/@db:/@172.29.16.1:/')
```
Run tests with `FLASK_ENV=testing python -m pytest tests/ -q` (after
`source .venv/bin/activate` and `set -a && source .env && set +a`).

CSS changes need a rebuild to actually show up: `npm run build:css` (needs
`npm install` first if `node_modules/` isn't present).

---

# Part 5: Session Ending 2026-08-07 (was SESSION_START.md's "current" section)

This was the live, current-state section of `SESSION_START.md` as of the end
of the session that produced it — now archived here since a later session
moved past it. See `SESSION_START.md` itself for what's current now.

### 2. This session's changes (most recent — read after AI_CONTEXT.md)

In order:

1. **Session-expiry bug fix.** `@permission_required`/`@role_required`
   (`app/utils/decorators.py`) previously `abort(403)`'d for *any* unauthorized
   request, including a simply-expired/never-logged-in session — so almost
   every page in the app showed a 403 error instead of redirecting to login
   (only the two routes using Flask-Login's own `@login_required` redirected
   correctly). Fixed: both decorators now call
   `current_app.login_manager.unauthorized()` (same as `login_required`) when
   unauthenticated, and only `abort(403)` once authenticated-but-lacking-permission.

2. **Per-Builder role-based access control.** New `builder_roles` M2M table
   (`Builder.allowed_roles`) and `Builder.is_accessible_to(user)`: a user with
   `builder.manage` always sees/builds every Builder; everyone else only
   sees/builds a Builder if their Role is in its `allowed_roles` (no roles
   assigned = restricted to `builder.manage` users only). Enforced in
   `/builders`' list view and re-checked server-side in `POST /builders/build`
   (403 on a tampered request). New "Allowed Roles" checkbox picker in the
   Builder create/edit modal; an "Access" column on the builders table.

3. **Human-readable, grouped permission picker on `/roles`.** The role
   create/edit checkbox list used to show raw permission codes
   (`builder.manage`) in one flat grid. Now grouped under friendly resource
   headings ("Builders", "AI Providers", etc. — see `RESOURCE_LABELS` in
   `app/blueprints/roles/routes.py`) showing each permission's human
   description, with the raw code demoted to a hover tooltip.

4. **AI prompt now actually sees the "Additional Description" typed at
   build-trigger time.** `render_default_prompt()` gained an
   `additional_description` param (new `{{additional_description}}`
   placeholder), threaded through both the worker's automatic
   post-success generation and the documentation page's "Generate Draft"
   button. The raw text is still appended after the AI draft too (unchanged
   safety net — nothing typed in is ever lost even if the AI ignores it or no
   provider is configured). Also fixed the **live dev DB's** `PromptTemplate`
   row, which turned out to still have long-stale `{{branch_name}}`/`{{type}}`
   placeholders from before an even earlier rename — its rendered prompt was
   silently broken until now.

5. **Build-trigger modal: Bump Type, Object, and Change Type are now
   mandatory** (each defaults to a disabled blank "— Select —" option, no more
   silently defaulting to `major`), both client-side (`required`) and
   server-side (flash + reject). **Branch override removed entirely** — the
   branch built is always the Builder's own configured `default_branch`; the
   modal shows it read-only, and the route ignores any stray
   `branch_override_*` in the request. Change it via Edit Builder instead.
   **Bump Type display order changed** to patch, minor, major (was major,
   minor, patch) — `BUMP_TYPES` in `app/services/build/versioning.py`.

6. **Object field is now a searchable dropdown**, not a plain
   `<input list="datalist">` — still free text (typing a brand-new value
   always works), but focusing/typing shows a filtered list of previously
   used values. Caught and fixed a real bug while building this: passing the
   suggestions via a `data-suggestions="{{ ...|tojson }}"` HTML attribute
   breaks the moment any suggestion exists (`tojson`'s escaping targets
   `<script>` content, not quoted attributes, so raw `"` leaked through) —
   fixed by moving the JSON into a `<script type="application/json">` block.

7. **`/documentation` page gained a full multi-field filter**: Version,
   Version String (substring), Change Type, Object (substring, with
   history datalist), Built By, Status (Documented/Pending), and a date
   range — all combine with AND, preserved across pagination, with a
   "Reset" link. Query joins `VersionDocumentation` directly (safe — every
   successful `BuildBatch` always has exactly one doc row, created atomically
   with the status flip in `worker._update_batch_status`).

8. **Home dashboard (`/`) overhaul.** Beyond the original Active
   Users/Roles/Recent Activity: four new stat cards (Builders, Versions,
   Images Built, Documentation Pending), each gated behind the exact
   permission its own list page requires; a Build Engine busy/idle pill
   (`builder.view`); a "N errors in the last 7 days" pill linking to
   `/logs/errors` (`logs.view`); and a Recent Builds table (`image.view`).

9. **UI polish pass:**
   - Animated hamburger↔cross `#sidebar-toggle` icon (3 CSS-animated bars,
     synced via both the button's click and the drawer checkbox's own
     `change` event, since the checkbox can also close natively via the
     mobile overlay backdrop).
   - **Dark mode** — daisyUI already ships `light`+`dark` themes by default;
     wired up a navbar sun/moon toggle, `localStorage` persistence, and a
     synchronous pre-paint `<script>` in both `base.html` and the standalone
     `auth/login.html` to avoid a flash of the wrong theme.
   - **Sidebar icon dark-mode fix** — every actual pasted menu icon SVG in the
     dev DB either has no `fill` attribute (implicit black) or a hardcoded
     `stroke="#343C54"`; none used `currentColor` despite the `/menus` form's
     own hint. Fixed generically in `input.css`: force `fill`/`stroke` to
     `currentColor` on `.sidebar-icon` SVGs, preserving any deliberate
     `fill="none"`/`stroke="none"` so outline-style icons don't get an
     unwanted solid fill.
   - **Duplicate app title, now configurable.** New
     `SystemConfig.hide_navbar_title_when_sidebar_open` (bool, default
     `True`, checkbox on `/config`) — when enabled, `sidebar.js` hides the
     navbar's "MASIMPLE CICD" title whenever the sidebar is open (desktop or
     mobile), re-evaluated on resize too, so exactly one copy ever shows.
   - **Loading animations for running builds** — spinner + real progress bar
     on both live status widgets (`/builders`, `/images`), plus a small
     inline spinner next to every "running" status badge (images list,
     builders' Last Build column, dashboard's Recent Builds).

10. **Docs consolidation (this change).** `ai-plan.md`, `image-builder-prompt.md`,
    and `image-builder-progress.md` — plus this file's own old historical
    sections — merged into a single `AI_CONTEXT.md` at the repo root, in
    chronological Parts 1–4. This file (`SESSION_START.md`) stays in the root
    as the up-to-date, forward-looking handoff doc; `AI_CONTEXT.md` holds
    everything historical.

New migrations this session: `aa573230db0c` (`builder_roles` table),
`128297392cfc` (`hide_navbar_title_when_sidebar_open`, with a `server_default`
since the `system_configs` singleton row already existed).

Three small additions landed right at the end of that same session, after
item 10 above:

11. **Group field (Builder create/edit) got the same searchable-history
    dropdown treatment as Object** (item 6 above) — replaced its shared
    `<datalist>` with the same `setupSearchDropdown()` pattern, generalized
    out of the Object-specific code in `builders.js` so it's now one
    reusable function wired to both fields (Object: one instance; Group:
    one per create/edit form, sharing one suggestions payload).
12. **Version Type field (`/versions` create/edit) got the same treatment**
    — new `app/static/js/versions.js` (this page had no dedicated JS file
    before), same shared-JSON-script-tag + `setupSearchDropdown()` pattern.
    Still free text; an unseen value still creates a new `VersionType` row
    on save via `_get_or_create_version_type()`, unchanged.
13. **`APP_SUMMARY.md` added** at the repo root — a self-contained,
    non-historical application overview (tech stack, architecture, full
    data model, feature-by-page list, conventions, known gaps) written to
    be pasted into a *different* AI conversation with no repo access, e.g.
    for planning a new feature. Distinct from `AI_CONTEXT.md` (historical
    narrative) and `SESSION_START.md` (session handoff) — describes only
    current state, no change history.

---

# Part 6: The Deployment Module (2026-08-07 through 2026-08-08)

Built from scratch and then substantially extended across a first session
and a long follow-up session — full implementation detail for each piece
lives in `SESSION_START.md`'s "Current state" section (kept current, not
archived here); this is the narrative arc and the *why* behind the bigger
decisions.

**Initial build (2026-08-07).** The module described in
`docs/deployment-feature-plan.md`: `DeploymentServer` (register a
Kubernetes cluster via kubeconfig, or a custom agent via `{api_url,
token}`), `DeploymentManifest` (YAML with `{{SYS:VERSION[:key]}}`
placeholders resolved against a Builder's latest — or a pinned — successful
`ImageBuild`), `DeploymentRun`/`DeploymentExecution` (deploy history),
mirroring the Image Builder module's own shape throughout. Several open
design questions from the plan doc were resolved via direct questions to
the user before writing code, rather than guessed at:
- The deploy worker is an **independent** queue/thread from the build
  worker (its own partial-unique-index single-flight guarantee) — a build
  and a deploy can run concurrently.
- A group deploy **aborts remaining executions on first failure**
  (`"skipped"`) — deliberately different from `BuildBatch`'s
  continue-and-report-partial-failure.
- `DeploymentServer.allowed_roles` — holding `deployment.trigger` (as it
  was called then) wasn't sufficient on its own; the user's role also had
  to be in the target server's allow-list, mirroring `Builder.allowed_roles`.
- No dedicated rollback action — re-deploying with a version binding's
  `pinned_image_build_id` set *is* the rollback mechanism.
- No new pip dependencies: `KubernetesProvider` shells out to `kubectl`
  against a temp-file kubeconfig, same precedent as `DockerBuildEngine`/
  `KanikoBuildEngine` shelling to `docker buildx`/`kaniko-executor` rather
  than reimplementing their protocols.

**The long follow-up session (2026-08-08)** kept building on top of that
base, roughly in this order:

1. **Manifest ordering + Stop.** Groups gained a user-configurable,
   drag-and-drop deploy order (`DeploymentManifest.order`). A "Stop" action
   was added — tear down what's currently deployed — with the explicit
   requirement that a group *stop* walks the order **descending**, the
   reverse of how a group *deploy* goes up. This needed `DeploymentRun.action`
   ("deploy"/"stop") and `DeploymentExecution.source_execution_id` (a stop
   execution points at the deploy execution it's undoing, so it deletes
   exactly what was applied instead of re-resolving placeholders that may
   have since moved on).
2. **Live-status polling.** A second, independent background thread
   periodically re-checks (via `kubectl get -f -`) whether each deployed
   manifest's resources are still actually present, at an
   admin-configurable interval. Feeds the "currently deployed" signal the
   rest of the module leans on heavily.
3. **Pod management, then generalized into a full read-only K8s resource
   browser.** Started as just Pods (list/logs/describe) as its own pages;
   the user then explicitly asked for it to cover network (Services/
   Ingress), disk (PVs/PVCs), Namespaces, and Nodes too — and for it to
   stay strictly **read-only** (list + describe, no delete/edit) when
   asked directly. Logs/Describe were later converted from full page
   navigations into `<dialog>` modals fetched via JSON, auto-scrolling to
   the bottom on load (most-recent lines for logs, the Events section for
   describe). A real routing collision risk got caught and fixed here:
   pod routes had to move under an explicit `/pods/` path segment so a
   Kubernetes namespace literally named `resources` could never be
   ambiguously routed against the new `/resources/<kind>/...` browser
   endpoints (Werkzeug ranks a static path segment over a dynamic one).
4. **Deploy/Stop button visibility became conditional.** Originally both
   buttons always showed regardless of state. Changed so Deploy hides once
   a manifest is live on every target server and Stop shows once it's live
   on at least one — both can show at once mid-rollout — driven by the
   same `is_currently_deployed()` check the Stop route itself uses, so the
   buttons never promise something that wouldn't actually happen.
5. **A real bug, caught live in the dev DB**: a manifest the user had
   already stopped via "Stop Group" still refused to delete ("it has 5
   recorded deployment(s)"). Root cause: the delete guard blocked on *any*
   execution history ever, a leftover assumption from before Stop existed
   (when "has history" and "is still live" were the same question).
   Fixed to check `is_currently_deployed()` instead, and
   `DeploymentExecution.manifest_id` was made nullable so a deleted
   manifest's history survives (nulled out, not cascaded) — same pattern
   as the existing `ActivityLog.user_id` nulling on a hard user delete.
   Caught a second latent bug in the same area while there: deleting *any*
   manifest with version bindings had always 500'd, since nothing dropped
   `DeploymentManifestVersionBinding` rows first and that FK has no cascade.
6. **Deployment Runs auto-refresh** — a page-level, user-configurable
   full-page reload interval (Off/5s/10s/30s/60s), persisted in
   `localStorage`, independent of the existing 3-second "Deploy Status"
   widget poll.
7. **Update, Restart, granular permissions, per-manifest user access** —
   the biggest single addition. Summarized back to the user and confirmed
   before implementation, given how many real design decisions it forced:
   - **Update**: since Deploy hides once fully live, there was no way left
     to notice or push a newer Builder image to an already-deployed
     manifest. New `worker.get_available_update()` compares the
     currently-deployed resolved version against what the manifest would
     resolve to right now; internally an Update is just another
     `action="deploy"` enqueue under a different button/permission.
   - **Restart**: `kubectl rollout restart -f -`, kube-only. Required
     redefining `is_currently_deployed()` — it used to mean "latest
     successful action was a deploy", which would have made a successful
     restart look like an undeploy. Now it means "latest successful action
     wasn't a stop", and a restart's own execution carries `rendered_yaml`/
     `resolved_version_string` forward from its source so a *second*
     restart (or a stop) can still chain off it correctly.
   - **`deployment.trigger` retired**, split into `deployment.deploy`/
     `update`/`stop`/`restart` (one permission per action, explicitly
     requested "one by one"). Migrated live against the dev DB via a new
     idempotent step in `seeds/seed_admin.py`.
   - **`DeploymentManifest.allowed_users`** — per-manifest access,
     deliberately **per-user, not per-role** (unlike
     `DeploymentServer.allowed_roles`) and deliberately **open when empty**
     rather than locked-to-managers (the opposite of `DeploymentServer`'s
     empty-list default) — chosen specifically so shipping it didn't
     silently strip access from every manifest already in use. Checked
     alongside, not instead of, the existing per-server role gate.
8. **Two small, isolated fixes** after that: a standalone deploy run's
   title used to just say "Standalone deploy" everywhere with no
   indication of which manifest actually ran — now shows `"<name>
   (standalone)"`. And the homepage (`/`), which had zero mention of the
   Deployment module despite it now being roughly half the app, gained
   permission-gated stat cards (Servers/Manifests/Runs) and a deploy-engine
   busy/idle indicator, mirroring the existing Image Builder dashboard
   cards exactly.

New migrations across this arc: `dcedb8fa84b8` (initial module),
`e57baba6abe0` (manifest order / run action / execution live-status columns
/ config interval), `2864992eaa32` (`deployment_executions.manifest_id`
nullable), `869fda148361` (`deployment_manifest_users`). Test count grew
from the pre-Deployment-module baseline to 425 by the end of this arc, all
passing, all real Postgres (no sqlite/mocks) as everywhere else in this app.

**A later session (2026-08-10) extended `/deployment-pods` well past its
original read-only-browsing scope**, continuing directly on top of the
work above:

9. **Namespace, Secret, and ConfigMap management** — three new tabs, each
   full create/edit/delete, added one at a time as separate user requests
   rather than planned together up front. All reuse `kubectl apply`/`delete`
   under the hood (JSON-as-YAML piped to `kubectl apply -f -`, since JSON is
   valid YAML — avoided adding PyYAML as a dependency, same "don't
   reimplement the protocol" precedent as everything else in this module).
   Secrets got the most design care: values are write-only end to end
   (never decoded, re-displayed, or logged — edit pre-fills existing key
   *names* only, blank value = keep), support both generic `Opaque` and
   `kubernetes.io/dockerconfigjson` image-pull secrets, and gained a
   file-import/paste-as-text bulk data-entry mode (parses `.env`-style
   `KEY=VALUE` lines, or imports a whole file as one blob keyed by its
   filename) after an explicit "make the input UI friendlier" ask.
   ConfigMaps reused the identical UI pattern but simpler — since their data
   isn't sensitive, edit just pre-fills real current values directly, no
   blank-to-keep dance needed. Two real bugs surfaced and got fixed during
   this stretch: `row.keys` in Jinja silently resolved to a plain dict's
   built-in `.keys()` method instead of the data (renamed the field to
   `secret_keys`/`configmap_keys`), and the Namespaces/Secrets/ConfigMaps/
   Workloads pages initially forgot to pass `resource_kinds` into their
   templates, so the shared tab nav's Nodes/Services/Ingress/PVs/PVCs tabs
   silently rendered empty on those four pages until caught and fixed with
   a regression test guarding each page.
10. **A "Workloads" tab, and a deliberate split of what "Restart" means.**
    Asked to make `DeploymentManifest`'s existing Restart action use
    `delete()`+`apply()` instead of `kubectl rollout restart -f -` (fixing
    the long-standing gap where rollout restart only understood Deployment/
    DaemonSet/StatefulSet and errored per-resource on anything else in a
    manifest) — with the explicit tradeoff that this is now a real
    teardown-then-recreate, not a zero-downtime rolling recycle. To keep a
    *true* rolling restart available, a new read-only Workloads tab lists
    raw Kubernetes `Deployment` objects with their own Restart button that
    does call real `kubectl rollout restart deployment/<name>` — so the two
    "Restart" actions now live at different levels on purpose: manifest-wide
    hard recreate vs. single-Deployment zero-downtime rollout.
11. **`/deployment-pods` merged into `/deployment-servers`.** The former's
    own server-picker index page was recognized mid-session as pure
    duplication of the latter's own server list — retired (the URL now just
    redirects to `/deployment-servers`) in favor of a per-row link there.
    First attempt (a CSS dropdown menu) silently failed because the
    surrounding table's `overflow-x-auto` clipped the popup; fixed by
    reusing this app's existing `<dialog>` modal pattern instead — then
    simplified further, on request, to a single direct link straight into
    Pods (the tab bar from there already reaches everything else).
12. **Pod Logs/Describe modals gained live auto-refresh** — plain interval
    polling (3s logs / 5s describe), not real streaming, matching this
    app's only other precedent for "live" UI (the Deploy Status/Deployment
    Runs widgets are both interval polling too). Defaults on when a modal
    opens, has a Pause/Resume toggle, and always stops on close so nothing
    keeps polling once nobody's looking.

New migrations in this later session: **none** — every one of items 9–12
was built against the existing schema. Test count grew from 425 to 503.
See Part 7 for the same session's other major addition (direct Error Log
links), which is not part of the Deployment module itself and touches
several other blueprints too.

Everything through item 8 was left uncommitted for a while, per this
project's established pattern (the user batches and pushes on their own
terms) — items 9–12, together with Part 7, were committed and pushed in
the same batch that closed out this arc.

---

# Part 7: Direct, Shareable Links from Every Error Notification (2026-08-10)

Same session as Part 6's items 9–12, but a distinct, cross-cutting feature
rather than more Deployment-module scope — this one touches Registries,
Git Sources, Documentation, and Logs too, not just `deployment_pods`.

**The ask**: every place the app tells a user "...failed, see Error Logs
for details" should instead link straight to the specific `ErrorLog` row,
and that row should be independently shareable (e.g. paste a link in
Slack). Planned via `EnterPlanMode` before writing code, including one
explicit scoping question to the user: only the 3 places that already said
"See Error Logs for details" (small), or all ~24 `log_error()` call sites
app-wide regardless of what they currently show (much bigger)? The user
picked the broad sweep.

**Design, once scoped:**
- **No schema change.** `ErrorLog.id` (already a UUID PK) is the shareable
  identifier — no new "share token" column. New route:
  `GET /logs/errors/<uuid:error_id>`, rendering the *same*
  `logs/errors.html` template in a focused single-row mode (`logs=[entry]`,
  no pagination/filtering at all) rather than trying to land the linked row
  on "the right page" of the normal filtered list — simpler, and guarantees
  the row's detail `<dialog>` actually exists in the DOM for the app's
  existing `data-open-modal`/`modal-form.js` auto-open convention to find.
- **One reusable helper, not three**, because `log_error()` already
  returned the created row everywhere it's called (just previously
  discarded by every caller): `error_detail_link(message, entry)` in
  `app/utils/error_logger.py` returns a `markupsafe.Markup` string (message,
  HTML-escaped, plus a real `<a>`). Because it's `Markup`, both `flash(...)`
  and an inline-banner `error` template variable could adopt it with **zero
  template changes** — Jinja/MarkupSafe already skip their own escaping for
  anything that's already `Markup`, and both call sites were already just a
  bare `{{ message }}`/`{{ error }}`. Only JSON responses consumed by JS
  (rendered via `.textContent`, which can't hold a real anchor tag) needed
  different treatment: a separate `error_log_url` field plus a small shared
  `renderErrorWithLink()` JS helper that appends a real `<a target="_blank">`
  after the text.
- **Explicitly scoped out, and said so rather than silently skipping:**
  background worker `log_error()` calls (no request/flash context to target
  at all — those failures already surface through their own status UI, not
  a notification; wiring *that* to its ErrorLog row would need a new FK
  column and migration, a bigger separate follow-up if ever wanted), the
  global unhandled-exception handler (logs and hands off to Flask's generic
  500 page — no notification UI to attach a link to), and `builders`'
  repo-info failure path (its JS never displayed the error at all, on
  either the branch/Dockerfile-picker helper or the JSON route behind it —
  nothing existing to enhance, same reasoning as the background workers).
- **Share button** on the error-detail dialog reuses the existing
  copy-to-clipboard mechanism from the traceback-copy button (generalized
  its class from `.copy-traceback-btn` to `.copy-to-clipboard-btn`, no new
  JS) against a hidden span holding the absolute
  `url_for(..., _external=True)` link.

A caught-and-reverted mistake along the way, worth remembering: running
`seeds/seed_menu.py` (as part of Part 6's item 11 consolidation, to retire
a stale "Deployment Pods" menu row) created 11 duplicate sidebar rows,
twice, because that script matches existing rows by exact `label` +
`parent_id` and this dev DB's real menu labels have since been hand-renamed
away from what the script expects (its own "Builder" vs. the script's
"Image Builder", etc.) — a pre-existing drift between the script and this
one database, unrelated to the current task. Cleaned up by deleting the
exact duplicate rows by UUID (twice) rather than editing the script's
matching logic, which was explicitly left as a separate, un-fixed known
issue — **do not run `seeds/seed_menu.py` against this dev DB again**
without addressing that first (e.g. matching by `url` instead of `label`).

This feature's new/changed tests are folded into the same overall count as
Part 6's items 9–12 — 503 total by the end of this session, not tracked
separately. No new migration.
