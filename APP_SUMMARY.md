# MASIMPLE CICD — Application Summary

Written to hand to a separate AI conversation (that has no access to this
codebase) as context for planning a new feature. It's self-contained: tech
stack, architecture, full feature list, data model, and the conventions a
plan should respect. It does not cover implementation history — that lives
in this repo's own `AI_CONTEXT.md`/`SESSION_START.md`, not needed here.

## What it is

MASIMPLE CICD is an internal Flask web app with four halves:

1. A general-purpose **admin/RBAC foundation** — users, roles, granular
   permissions, a database-driven sidebar/navbar menu, activity logging, and
   error logging.
2. A **Docker Image Builder module** built on top of it — register Git repos
   and container registries, define reusable "Builder" configs, trigger
   versioned builds (single or batched), push images, and auto-generate
   changelog-style documentation for each successful build (with an AI
   assist).
3. A **Deployment module** — register Kubernetes (or custom-agent) target
   servers, define YAML manifests with placeholders that resolve against an
   Image Builder Builder's latest (or a pinned) successful build, then
   deploy/update/stop/restart them (individually or as an ordered group),
   view deploy history, and browse live cluster state (pods + logs/describe,
   namespaces, nodes, services, ingresses, PVs/PVCs) for any registered
   Kubernetes server. Pod logs stream live over Server-Sent Events
   (`kubectl logs -f` under the hood); everything else "live" in this app is
   still plain interval polling. A standalone **YAML Generator**
   (`/yaml-generator`) builds Deployment/Service/ConfigMap/Secret/Ingress
   YAML from form fields with a live preview, independent of any manifest —
   its "Save as Manifest" action hands the result into this module's own
   manifest-creation flow rather than persisting anything itself.
4. A **Workflow module** on top of both of the above — chain existing
   Builders and DeploymentManifests into an ordered, reusable sequence (e.g.
   build → deploy to staging → deploy to prod) without re-entering any
   config, run it with one click, and watch it advance step by step. Never
   duplicates the Image Builder/Deployment modules' own execution — a
   workflow step just enqueues into their existing queues and watches for
   completion.

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
| Background work | Python `threading`/`queue` — independent in-process worker threads (one for builds, one for deploys, one deploy live-status poller, one workflow orchestrator), no Celery/Redis |
| Deployment (of MASIMPLE CICD itself) | Docker (multi-stage: Node build for CSS, then Python/gunicorn `--worker-class gthread --threads 4`), Docker Compose (`web` + `db`) |
| Git integration | GitPython, provider-abstracted (`GitProvider` → `GitHubProvider`) |
| Registry integration | docker-py, provider-abstracted (`RegistryProvider` → `DockerHubProvider`/`GHCRProvider`/`HarborProvider`/`ECRProvider`, all implemented) |
| Image builds | shells out to `docker buildx build` or a `kaniko-executor` binary, provider-abstracted (`BuildEngine`) |
| Kubernetes integration | shells out to the `kubectl` CLI (no `kubernetes` client library), provider-abstracted (`DeploymentProvider` → `KubernetesProvider`/`CustomAPIProvider`) — `kubectl` must be installed wherever MASIMPLE CICD itself runs; pod logs stream via `kubectl logs -f` over Server-Sent Events |
| AI description generation | provider-abstracted (`AIProvider` → `QwenProvider`/`ClaudeProvider`/`GeminiProvider`/`CustomAPIProvider`, all implemented) |
| Credential encryption | `cryptography` Fernet, key from `SECRET_ENCRYPTION_KEY` env var (Image Builder) / `CREDENTIAL_ENCRYPTION_KEY` env var (Deployment module) |
| AWS SDK | `boto3` — ECR `RegistryProvider` only (SigV4-signed calls, doesn't fit the generic Docker Registry v2 bearer-token flow the other registry providers share) |
| YAML generation | `PyYAML` — YAML Generator page only; everywhere else in this app deliberately avoids it in favor of dict→`json.dumps()` (JSON is valid YAML) since that output only ever feeds `kubectl apply -f -`, never a human — see `app/services/yaml_generator/render.py` |
| Tests | pytest against a **real** Postgres test DB (not sqlite/mocked), 656 tests |

## Architecture conventions

- App factory in `app/__init__.py` (`create_app`); extensions (`db`,
  `migrate`, `login_manager`, `csrf`) instantiated in `app/extensions.py`.
- **One blueprint per feature**, package name `<name>_bp`, each with its own
  `routes.py` + `forms.py` (Flask-WTF): `auth`, `main`, `users`, `roles`,
  `permissions`, `menus`, `logs`, `ai_settings`, `git_sources`, `registries`,
  `versions`, `builders`, `images`, `documentation`, `system_config`,
  `deployment_servers`, `deployment_manifests`, `deployment_runs`,
  `deployment_pods` (this last one now has a `forms.py` too — Namespace/
  Secret/ConfigMap create-edit-delete forms, on top of its original
  read-only Pods/Nodes/Services/Ingress/PVs/PVCs/Workloads browsing),
  `workflows` (chains existing Builders/DeploymentManifests into an ordered,
  reusable sequence — see the Workflow module below), `yaml_generator`
  (builds Kubernetes resource YAML from form fields — see below).
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
  The Deployment module has its own **independent** single-flight worker
  (`app/services/deployment/worker.py`) with its own DB-level partial
  unique index — a build and a deploy can run concurrently, but only one
  deploy runs system-wide at a time, same as builds. A third, separate
  background thread (`start_status_poller`) periodically re-checks whether
  each deployed manifest is still actually live on its target server(s), at
  an admin-configurable interval (`SystemConfig.
  deployment_status_check_interval_seconds`). A **fourth** background thread
  (`app/services/workflow/worker.py`) orchestrates Workflow runs — it never
  executes a build or deploy itself, it only enqueues into the two queues
  above (via the exact same `enqueue_build_batch()`/`enqueue_deployment_run()`
  the manual trigger routes call) and watches for terminal status before
  advancing to the next step, so a workflow step is subject to the same
  single-flight locks as any manually triggered batch/run. The build and
  deploy workers each also run a **heartbeat** thread: the single
  `status='running'` row's `heartbeat_at` is ticked every 15s while a job
  runs, and a separate reap check (folded into each worker's existing 2s
  poll loop) fails out a stale/missing heartbeat (>60s) as a crashed job,
  freeing the single-flight slot automatically instead of leaving it wedged
  forever after an ungraceful process death mid-build/mid-deploy — a
  downstream `WorkflowRun` watching that batch/run resumes on its own once
  it reaches that terminal status, no workflow-side handling needed.
- Templates: Tailwind/daisyUI utility classes; a handful of pages have their
  own small vanilla-JS file (`app/static/js/<page>.js`) for
  cascading dropdowns, searchable-history comboboxes, live-status polling,
  or client-built markup. One deliberate exception to "one JS file per
  page": `app/static/js/yaml_editor.js` is a small shared module (exposed
  as `window.YamlEditor`, no bundler) for the CodeMirror YAML-editor init
  used by `deployment_manifests.js`, `deployment_servers.js`, and
  `yaml_generator.js` — extracted specifically to fix a cursor-position bug
  that existed in two independently duplicated copies, not a general
  code-sharing precedent for future pages.

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
  session timeout minutes, build engine choice (`docker`/`kaniko`), a UI
  toggle (`hide_navbar_title_when_sidebar_open`), and the Deployment
  module's live-status poll interval.

**Deployment module:**
- `DeploymentServer` — a registered target: `connection_type` (`kube` or
  `api`), encrypted credentials (a kubeconfig blob, or `{api_url, token}`
  JSON for a custom agent), health `status` from a manual "Test Connection"
  check, `allowed_roles` (many-to-many `Role` — empty means locked to
  `deployment_server.manage` users only).
- `DeploymentManifest` — a YAML template with `{{SYS:VERSION}}` /
  `{{SYS:VERSION:key}}` placeholders, one or more `target_servers`
  (many-to-many `DeploymentServer`), an optional `group_name` (manifests
  sharing one deploy/stop/restart together, ordered via drag-and-drop —
  `order` int, ascending for deploy/update, descending for stop/restart),
  and `allowed_users` (many-to-many `User` — **empty means unrestricted**,
  the opposite default from `DeploymentServer.allowed_roles`, chosen
  deliberately so shipping this didn't silently lock every
  already-existing manifest).
- `DeploymentManifestVersionBinding` — one row per placeholder key: which
  `Builder` it resolves against, optionally pinned to one specific past
  `ImageBuild` instead of "latest successful" (the rollback mechanism —
  there's no separate rollback action, just re-deploying with a pin set).
- `DeploymentRun` — one Deploy/Update/Stop/Restart click, `action`
  (`deploy`/`stop`/`restart` — Update reuses `"deploy"`, it's the same
  operation under a different permission/button), aggregate `status`.
- `DeploymentExecution` — one manifest × one server inside a
  `DeploymentRun`: resolved version string, rendered YAML (audit snapshot,
  never mutated after the fact), status, log, live-status poll result
  (`live_status`/`live_checked_at`), and `source_execution_id`
  (self-referential — a stop/restart execution points at the deploy
  execution it's acting on, so it acts on exactly what was applied rather
  than re-resolving). `manifest_id` is **nullable**: deleting a manifest
  nulls it out on historical executions rather than blocking the delete or
  cascading, so `/deployment-runs` history survives.

**Workflow module:**
- `Workflow` — name, description, `is_active`, `allowed_roles` (same
  access-gate shape as `Builder`/`DeploymentManifest`: empty means locked to
  `workflow.manage` users only).
- `WorkflowStep` — one ordered step (`order`, `step_type` `build`/`deploy`,
  `on_failure` `stop`/`continue`). A `build` step also carries
  `bump_type`/`change_type_id`/`object`/`additional_description`, captured
  once at authoring time since `/builders/build` requires them at every
  trigger. What a step actually targets is **resolved live at run time**,
  never stored as a frozen list:
  - `WorkflowStepGroup` — one row per selected `group_name`, resolved
    against whichever Builders/Manifests currently share that name (so
    adding a member to an already-referenced group later is picked up
    automatically next run).
  - `WorkflowStep.selected_builders`/`selected_manifests` — individually
    picked *ungrouped* Builders/Manifests only (an item belonging to a
    group is only reachable by selecting that group — same rule as the
    "Deploy Group vs. per-manifest Deploy button" split on
    `/deployment-manifests`).
  - A step can combine several selected groups **and** individual items at
    once.
- `WorkflowRun` — one "Run" click; `status`
  (`queued`/`running`/`success`/`failed`/`completed_with_failures`),
  `current_step_id`, `has_failed_step` (set on any failure regardless of
  that step's `on_failure`, distinguishing a clean success from a
  continued-through failure).
- `WorkflowStepRun` — one step's execution within one run;
  `batch_id`/`deployment_run_id` point at the real `BuildBatch`/
  `DeploymentRun` the orchestrator enqueued for it (exactly one is set), so
  the run page links straight into the existing build/deploy detail pages
  rather than duplicating any log UI. A step that couldn't even be started
  (nothing resolved, or a build step's builders don't share one Version/are
  missing a default branch) gets a `WorkflowStepRun` with just an `error`
  string and no batch/run at all.

## Feature list (by page)

- **`/` Dashboard** — active users/roles counts; permission-gated Image
  Builder stat cards (Builders/Versions/Images Built/Documentation Pending)
  and Deployment stat cards (Deployment Servers/Manifests/Runs), each only
  shown if the viewer has that resource's view permission; a build-engine
  and a deploy-engine busy/idle indicator; a recent-errors count; a
  recent-builds table; recent activity log.
- **`/users`, `/roles`, `/permissions`** — standard RBAC CRUD. The Roles
  page's permission picker groups the ~41 permissions under friendly
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
  timeout, build engine, duplicate-title toggle, deploy live-status poll
  interval.
- **`/deployment-servers`** — register/edit target servers (kubeconfig or
  custom-agent credentials, never re-shown after save), per-server "Test
  Connection", `allowed_roles` picker, per-row "Kubernetes" link into that
  server's Pods page (kube type only) — this is now the *only* entry point
  into `/deployment-pods`; there's no separate server-picker page there
  anymore (see below).
- **`/deployment-manifests`** — manifest CRUD grouped by `group_name`
  (drag-and-drop reorder within a group via SortableJS); per-manifest
  version-binding picker (scan YAML for placeholders, pick a Builder +
  optional pin per placeholder); `allowed_users` picker. Per-row/group
  action buttons are **conditional, not always all shown**: Deploy hides
  once live everywhere, Stop/Restart show once live anywhere, Update shows
  only when a newer resolvable version exists and it's currently deployed
  — computed server-side from live deploy-run history, not client state.
  Each action (Deploy/Update/Stop/Restart) has its own confirmation modal
  (fetched preview, no page navigation) and its own permission
  (`deployment.deploy`/`update`/`stop`/`restart`).
- **`/yaml-generator`** (`yaml_generator.view`, nested under the Deployment
  sidebar group) — a standalone Kubernetes YAML builder: pick a resource
  kind (Deployment/Service/ConfigMap/Secret/Ingress), fill in form fields
  (key/value rows for labels/env/data, port rows for Services), and get a
  live-updating YAML preview (debounced, re-generated server-side on every
  field change so the preview always matches what a save would actually
  produce) in the same CodeMirror editor `/deployment-manifests` uses.
  Copy-to-clipboard and Download-as-`.yaml` are pure client-side. "Save as
  Manifest" hands the generated YAML off into `/deployment-manifests`'
  own Create-Manifest flow via a one-shot session prefill (`name`/
  `yaml_content` only) rather than persisting anything itself — nothing in
  this blueprint ever touches `DeploymentManifest`/`db.session` directly,
  so the actual save still goes through that flow's own validation/
  permission checks (`deployment_manifest.manage`). Doesn't persist any
  config of its own — no new DB tables. A generated `image` field accepts
  a literal `{{SYS:VERSION}}`/`{{SYS:VERSION:key}}` token, since the
  resolver that reads those (`app/services/deployment/resolver.py`) is a
  plain regex over text, not YAML-structure-aware.
- **`/deployment-runs`** — paginated run history (filter by status/
  manifest/server/date range), live "Deploy Status" widget (polls every
  3s) plus a user-configurable full-page auto-refresh (Off/5s/10s/30s/60s,
  `localStorage`-persisted). A standalone run's title shows the manifest
  name (`"<name> (standalone)"`), not a generic label. Run detail page
  shows per-execution logs.
- **`/deployment-pods`** — a tabbed browser for one Kubernetes server's live
  cluster state, reached via a per-row link on `/deployment-servers` (its
  own former server-picker index page was retired as duplication — the URL
  itself now just redirects to `/deployment-servers`). Tabs:
  - **Pods** — list + Logs/Describe as modals, auto-scrolled to the bottom.
    Logs stream live over Server-Sent Events (`kubectl logs -f`, one
    subprocess per open modal); Describe still polls on an interval (no
    follow/watch mode to stream from).
  - **Namespaces** — full CRUD (create/edit labels/delete,
    `deployment_namespace.manage`); delete warns it cascades every
    resource inside.
  - **Secrets** — full CRUD (`deployment_secret.manage`) for both generic
    Opaque key/value secrets and `kubernetes.io/dockerconfigjson` image-pull
    secrets. Values are write-only (never decoded/re-shown/logged — edit
    pre-fills existing key *names* only, blank value = keep); data entry
    supports manual key/value rows, importing a file (`.env`-style parsed
    into rows, or the whole file as one blob keyed by filename), or pasting
    `KEY=VALUE` text to convert into rows.
  - **ConfigMaps** — full CRUD (`deployment_configmap.manage`); unlike
    Secrets, data isn't sensitive so edit pre-fills real current values
    directly (no blank-to-keep dance) and list rows show real key names.
    Same file-import/paste-as-text data entry as Secrets.
  - **Workloads** — read-only list of raw Kubernetes `Deployment` objects
    (ready/desired replica count, image) plus a per-row Restart button
    (`deployment_workload.restart`) that does a true `kubectl rollout
    restart deployment/<name>` — a zero-downtime rolling recycle, unlike
    `DeploymentManifest`'s own Restart action (see Known Gaps).
  - **Nodes, Services, Ingress, PersistentVolumes,
    PersistentVolumeClaims** — read-only, list + Describe modal only,
    deliberate scope choice (unlike the four sections above).

  `"api"`-type servers aren't supported anywhere in this whole section (no
  pod/namespace/etc. concept for a generic agent endpoint) and 404 if tried.

- **`/workflows`** — list/create/edit Workflows (`workflow.view`/`manage`),
  `allowed_roles` picker. Detail page (`/workflows/<id>`) is the step
  builder: "+ Add Build Step"/"+ Add Deploy Step" open a modal combining a
  multi-select of existing `group_name`s with a multi-select of individual
  *ungrouped* Builders/Manifests (an item already in a group is never
  independently selectable — only reachable via its group), plus (build
  steps only) Bump Type/Change Type/Object/Additional Description and a
  per-step "if this step fails: stop the run / continue anyway" choice.
  Steps are drag-reordered (SortableJS, same pattern as manifest groups).
  Each step's card shows a **live** preview of what it currently resolves to
  (re-computed on every page load, not frozen at step-creation time). "Run"
  (`workflow.run`) queues a `WorkflowRun`; its detail page
  (`/workflows/runs/<id>`) polls run status and renders a step tracker that
  links each finished step straight into the real `/images` or
  `/deployment-runs/<id>` page for its actual build/deploy log — no
  duplicate log UI. Deleting a Workflow or an individual step is blocked
  once either has any run history, mirroring the delete-guard pattern
  elsewhere in the app (`builder.manage`'s "N recorded build(s)" block,
  etc.) rather than nulling out foreign keys.

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

- A Workflow's deploy step still resolves "latest successful build for the
  bound Builder" — same as a manual deploy — not "the exact build this
  run's own preceding build step just produced." Fine when a workflow is
  the only thing building against a given Builder; could resolve to the
  wrong image if a manual build or another workflow finishes against the
  same Builder in between the two steps. Would need a new resolver mode
  scoped to a specific `BuildBatch`, not just latest.
- No Workflow step *editing* — only add/delete. Changing a step means
  deleting it and adding a new one (blocked entirely if the step has any
  run history — see `WorkflowStepRun`).
- The Workflow feature's UI (drag-and-drop step reorder, the run page's live
  JS polling) has been verified via Flask's test client (every route and
  template renders correctly, including populated/linked/errored run
  states) but not yet in an actual browser.
- **The YAML-editor cursor-position fix (see `app/static/js/yaml_editor.js`)
  is unverified as of this writing** — see `SESSION_START.md`'s "Current
  state" section for the exact live-debugging history (two prior attempts,
  one of which briefly broke the editor entirely via a `ResizeObserver`
  feedback loop). Confirm with the user before treating this as fixed.
- Everything runs through a single in-process worker thread per queue (one
  each for builds and deploys, plus the workflow orchestrator, which only
  ever enqueues into those same two queues) — there is no distributed/
  multi-instance deployment story; a feature that needs horizontal scaling
  would need to introduce a real broker first.
- `kubectl` must be installed on whatever host/container MASIMPLE CICD itself
  runs on (not the end user's machine) — deploys/updates/stops/restarts/pod
  browsing all shell out to it. No `kubernetes` client library is used.
- The `"api"` `DeploymentServer` connection type (a custom agent instead of
  raw Kubernetes) has an **assumed** HTTP contract (GET to check
  reachability, POST/DELETE the raw YAML to apply/tear down, a body on the
  DELETE) — `tests/test_custom_api_provider.py` now pins down that the code
  actually behaves exactly as documented (mocked HTTP calls, no real
  agent), but the contract itself has still never been matched against a
  real agent implementation. It also doesn't support restart, live-status
  polling, or any of the `/deployment-pods` browsing (all raise
  `NotImplementedError` / 404, by design, not by omission).
- `DeploymentManifest`'s Restart action is `delete()` + `apply()` of the same
  rendered YAML, not `kubectl rollout restart` — so there's a real gap with
  nothing running between the two steps, not a zero-downtime rolling
  recycle. Chosen over rollout restart specifically because rollout restart
  only understands Deployment/DaemonSet/StatefulSet and left every other
  resource kind in a manifest erroring per-resource; delete+apply work
  uniformly across whatever the manifest declares. For a true
  zero-downtime rolling restart of a single named Kubernetes `Deployment`,
  use the Restart button on `/deployment-pods/<server_id>/workloads`
  instead (`kubectl rollout restart deployment/<name>`) — a separate,
  narrower action that only exists for that one resource kind.
