# Feature Prompt: Deployment Module for Hamilton

## Context (paste Hamilton's app summary alongside this)

Hamilton already has a Docker Image Builder module: `Repository` + `Version`

- `RegistryTarget` combine into a `Builder`; triggering a `Builder` (or a
  group of them) creates a `BuildBatch`, which bumps the `Version` once and
  produces a shared version string (`{TYPE}.{MAJOR}.{MINOR}.{PATCH}.{DATETIME}`)
  shared by each `ImageBuild` inside it.

This feature adds **Deployment**: store target servers, store/manage
Kubernetes YAML manifests, resolve a `{{SYS:VERSION}}` placeholder in a
manifest to a real image tag pulled from an existing successful
`ImageBuild`, group manifests for one-click multi-deploy, and apply the
rendered YAML to selected servers.

Do not invent a second versioning system — a manifest's version placeholder
resolves against the **existing** `Builder` → `ImageBuild` chain (a specific
builder's latest successful build, or a specific past build if pinned).

## New Blueprints (following the one-blueprint-per-feature convention)

- `deployment_servers_bp` — CRUD for target servers (sibling to
  `registries_bp` in structure/complexity).
- `deployment_manifests_bp` — CRUD for YAML manifests + the deploy-trigger
  action (sibling to `builders_bp`).
- `deployment_runs_bp` — read-only run history (sibling to `images_bp`).

All new routes protected with `@permission_required("resource.action")`.
New permission codes to add to `seeds/seed_admin.py`'s `BASE_PERMISSIONS`:
`deployment_server.view`, `deployment_server.manage`,
`deployment_manifest.view`, `deployment_manifest.manage`,
`deployment_run.view`, `deployment.trigger`.
Add corresponding `Menu` rows via `seeds/seed_menu.py` — don't hardcode nav.

## New Provider Abstraction (matching `GitProvider`/`RegistryProvider`/`BuildEngine`)

`DeploymentProvider` (factory/strategy, one new class + one config row =
new implementation, no calling-code changes):

- `KubernetesProvider` — applies manifests via the Kubernetes API using a
  stored kubeconfig / service account token.
- `CustomAPIProvider` — sends the rendered manifest to a client-side agent
  endpoint (matches your "or API" flexibility requirement).

Both implement one interface, e.g. `test_connection()`, `apply(manifest_yaml) -> DeployResult`.

## Data Model (UUID PKs throughout, via `sqlalchemy.dialects.postgresql.UUID`)

- **`DeploymentServer`** — name, `connection_type` (`api`/`kube`),
  encrypted credentials (Fernet, same pattern as existing credential
  storage — kubeconfig blob or API URL+token), `status`
  (unverified/healthy/unreachable), last-checked timestamp.

- **`DeploymentManifest`** — name, `yaml_content`, `group_name` (nullable,
  **searchable-history combobox** — same established pattern as
  Builder's Group/Object fields), `status`. A manifest with no
  `group_name` is standalone; one with a `group_name` deploys together
  with others sharing that name.

- **`DeploymentManifestVersionBinding`** — one row per `{{SYS:VERSION}}`
  (or `{{SYS:VERSION:key}}` if you want multiple images per manifest) found
  in a manifest: `manifest_id`, `placeholder_key` (default `"default"`),
  `builder_id` (FK — which Builder's image this resolves against),
  `pinned_image_build_id` (nullable FK to `ImageBuild` — if null, resolve
  to that builder's latest successful `ImageBuild` at deploy time).

- **`DeploymentManifestServer`** — join table, `manifest_id` ↔
  `server_id`, selected at manifest setup time.

- **`DeploymentRun`** — one "Deploy" click. If triggered on a group, covers
  every manifest sharing that `group_name`; if triggered on a standalone
  manifest, covers just that one. Aggregate `status`
  (queued/running/success/partial_failure/failed), `triggered_by`,
  timestamps — same shape as `BuildBatch`.

- **`DeploymentExecution`** — one manifest × one server inside a
  `DeploymentRun`: resolved version string, rendered YAML (snapshot, for
  audit), status, logs, timestamps — same shape as `ImageBuild` inside
  `BuildBatch`.

## Execution Model

- Reuse the single-in-process-worker-thread pattern
  (`app/services/deployment/worker.py`, mirrors
  `app/services/build/worker.py`): a queue, one deploy job processed at a
  time system-wide, consistent with the existing "only one build runs at a
  time" rule. (Flag this as a discussion point if you want deploys to run
  concurrently with builds, or concurrently with each other — see Open
  Questions.)
- Placeholder resolution happens at execution time, not at save time, so
  "latest" always means latest-at-deploy.
- Log via `log_activity(...)` after manifest create/update/delete and after
  each deploy trigger.

## UI / Pages

- **`/deployment-servers`** — CRUD, connection test button, status badge
  with `loading loading-spinner loading-xs` while a run is executing
  (matching the app's existing "in progress" visual language).
- **`/deployment-manifests`** — CRUD, grouped by `group_name` (grouped
  manifests deploy together; ungrouped deploy individually — same UX as
  the Builders page). Setup form: YAML editor/textarea, version binding
  picker(s) per placeholder (optional — defaults to latest), Group field
  (searchable-history combobox), target server multi-select. The
  deploy-trigger modal should require explicit confirmation, no silent
  defaults, matching the Builder trigger modal's pattern (Bump
  Type/Object/Change Type there → here, maybe "confirm target servers +
  resolved versions" as a review step before applying).
- **`/deployment-runs`** — history grouped by `DeploymentRun`, filterable
  by status/manifest/server/date range, per-execution logs and resolved
  version, spinner on running items.

## Conventions to Follow

- New tables via `flask db migrate` (never hand-edit migration files).
- Jinja: no inline `{% if %}...{% endif %}` inside a tag's attribute list —
  use `{{ 'x' if cond else '' }}`.
- Any new SVG icons use `fill="currentColor"`/`stroke="currentColor"` for
  dark-mode support.
- Searchable-history combobox fields: plain `<input>` + same-page
  `<ul class="...-dropdown">`, suggestions passed via a
  `<script type="application/json">` block (not a `data-*` attribute — see
  the `tojson`-escaping gotcha already documented for this app), wired with
  a page-local `setupSearchDropdown()`.
- CSS changes require `npm run build:css`.

## Open Questions to Confirm Before/During Implementation

- Should a `DeploymentRun` (group deploy) run its manifests/servers
  sequentially or in parallel? Should the existing build worker queue and a
  new deploy worker queue share the "only one job system-wide" constraint,
  or run independently?
- On partial failure inside a group run, abort remaining manifests or
  continue and report partial_failure (mirrors `BuildBatch`'s
  `partial_failure` status)?
- Do manifests need an approval/staging gate before applying to
  production-tagged servers, or is any user with `deployment.trigger`
  sufficient (matching the existing Builder `allowed_roles` pattern —
  should `DeploymentServer` or `DeploymentManifest` get a similar
  role-restriction field)?
- Rollback: is re-deploying a manifest with a `pinned_image_build_id` set
  to a prior successful build sufficient, or do you need a dedicated
  "rollback" action that also tracks what it's rolling back from?
- Multiple images per manifest (multiple `{{SYS:VERSION:key}}`
  placeholders) — needed now, or is a single placeholder per manifest
  enough for v1?

## Known Gap Carried Over

Like `REPO_CLONE_ROOT`, there's no distributed/multi-instance deployment
story yet — the deployment worker, like the build worker, runs on a single
app instance. That's fine for now but worth noting if deploy volume grows.
