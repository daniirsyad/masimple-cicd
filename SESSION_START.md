# Session Start Prompt

Paste the block below into a new chat to pick up this project with full context.

---

I'm continuing work on this Flask app (Hamilton) at /home/daniirsyad/work/code/hamilton-app-deployment.

First, read these files in full before doing anything else:

1. **CLAUDE.md** — project conventions (blueprint structure, RBAC/permission
   patterns, UUID PKs, `log_activity`, testing setup, "do not touch" list, etc.).
2. **APP_SUMMARY.md** — current-state application overview (tech stack,
   architecture, full data model, feature-by-page list, conventions, known
   gaps). No change history — just what the app *is*, right now. The fastest
   way to get oriented.
3. **AI_CONTEXT.md** — deep historical spec/progress log, chronological
   Parts 1–5, only if you need the *why* behind something (a past decision,
   a bug that was already found and fixed a certain way, a design tradeoff).
   Not required reading for routine work — `APP_SUMMARY.md` plus this file
   should already be enough context to start.

Then ask me what to work on next rather than assuming.

## Current state

- **425 tests passing.** Latest session (2026-08-08) added, on top of the
  Deployment module below:
  - **Dashboard (`/`) now shows the Deployment module.** `app/blueprints/
    main/routes.py`'s `_module_stats()` gained three permission-gated cards
    (Deployment Servers/Manifests/Runs — same pattern as the existing
    Image Builder cards, each gated on that resource's own `.view`
    permission), plus a "Deploy engine" busy/idle indicator next to the
    existing "Build engine" one (`get_engine_status` from
    `app.services.deployment.worker`, aliased to avoid colliding with the
    build worker's same-named function). Previously the homepage had zero
    mention of the Deployment module at all.
  - **Deployment run title fix**: a standalone (non-group) run used to
    just say "Standalone deploy" everywhere (list page, detail page) with
    no indication of which manifest actually ran. Now shows
    `"<manifest name> (standalone)"` — `deployment_runs.routes.
    _run_display_name(run, executions)`, falling back to the old generic
    label only if a run somehow has zero executions.
  - **Update / Restart actions + granular permissions + per-manifest user
    access.** Big one, all in `app/blueprints/deployment_manifests/`,
    `app/services/deployment/worker.py`, `app/services/deployment/
    kubernetes_provider.py`/`custom_api_provider.py`/`base.py`:
    - `deployment.trigger` **retired**, split into `deployment.deploy`,
      `deployment.update`, `deployment.stop`, `deployment.restart` (one per
      button/route). Migrated live on the dev DB — any role holding
      `deployment.trigger` (only Super Admin did) got all four, then the
      old permission was deleted (`seeds/seed_admin.py`'s
      `migrate_deployment_trigger_permission`, safe to re-run).
    - **Update**: since Deploy hides once a manifest is fully live
      everywhere, there was no way to notice/push a newer Builder image to
      an already-deployed manifest. New `worker.get_available_update
      (manifest, server)` compares the currently-deployed
      `resolved_version_string` against what `resolve_manifest()` would
      produce right now; an "Update" button shows per-row/group when a
      newer version exists. Internally it's just another `action="deploy"`
      enqueue — same operation, different permission/button.
    - **Restart**: `kubectl rollout restart -f -` (new
      `DeploymentProvider.restart()`, kube-only —
      `CustomAPIProvider.restart` raises `NotImplementedError`, no agent
      contract for it). Reuses the stop/restart `source_execution_id`
      chaining machinery in `enqueue_deployment_run`. **Important semantic
      change**: `is_currently_deployed()` used to mean "latest successful
      action == deploy"; now it means "latest successful action != stop" —
      otherwise a successful restart would have wrongly read as
      "undeployed". A restart's own execution carries `rendered_yaml`/
      `resolved_version_string` forward from its source so a *second*
      restart (or a stop) can still chain off of it.
    - **Per-manifest user access**: new `DeploymentManifest.allowed_users`
      (many-to-many to `User`, migration `869fda148361`) — deliberately
      **users, not roles** (unlike `DeploymentServer.allowed_roles`), and
      deliberately **open-by-default when empty** (unlike
      `DeploymentServer`, which locks to managers when empty) — chosen so
      shipping this didn't silently strip access from every manifest
      already in use. `deployment_manifest.manage` always bypasses it, same
      as `DeploymentServer`. All four trigger routes now check both this
      *and* the existing per-server `allowed_roles` — both must pass.
    - Manifest table gained an "Access" column + an "Allowed Users"
      checkbox grid in the create/edit modal.
    - The stale "Manifests with any recorded deployment history can't be
      deleted" copy in the delete-confirm modal was fixed to match the
      already-corrected actual rule (still-deployed / in-progress only —
      see below).
  - **Deploy Runs auto-refresh**: a dropdown next to "Deployment Runs"
    (Off/5s/10s/30s/60s) that fully reloads the page on that cadence,
    independent of the existing 3s "Deploy Status" widget poll — catches
    runs triggered elsewhere. Persisted per-browser in `localStorage`
    (`deploymentRunsAutoRefreshMs`).
  - **Fixed: a fully-stopped manifest with history couldn't be deleted.**
    `delete_manifest` used to block on *any* `DeploymentExecution` history
    at all; now it blocks only if the manifest is still actually deployed
    somewhere (`is_currently_deployed`, per server) or has a queued/running
    execution in flight. Otherwise it deletes, first dropping
    `DeploymentManifestVersionBinding` rows and **nulling**
    `DeploymentExecution.manifest_id` (now nullable — migration
    `2864992eaa32`) on its history so `deployment_runs` audit history
    survives instead of failing on the FK or cascading. Also fixed a
    latent bug this exposed: deleting a manifest that had version bindings
    always 500'd before (nothing dropped them, and the FK has no cascade)
    — no prior test covered that path. Everywhere a template/route reads
    `execution.manifest.name` now falls back to "(deleted manifest)".
  - **Deploy/Stop button visibility**: on `/deployment-manifests`, the
    per-row and per-group Deploy/Stop buttons are now conditional, not
    always both shown — Deploy hides once a manifest is live on *every* one
    of its target servers; Stop shows as soon as it's live on *at least
    one* (both can show together mid-rollout). Driven server-side by the
    same `is_currently_deployed()` the Stop route itself uses
    (`_manifest_live_server_ids`/`_manifest_groups` in
    `deployment_manifests/routes.py`), so the buttons never promise
    something that wouldn't actually happen.
  - **Pod Logs/Describe are modals now, not separate pages** — clicking
    Logs/Describe on `/deployment-pods/<server_id>` opens a `<dialog>` and
    fetches via `fetch()`; the routes (`pod_logs`/`describe_pod`, now under
    `/pods/<namespace>/<pod_name>/...`) return JSON instead of rendering a
    page. Both modals auto-scroll their `<pre>` content to the bottom on
    load (logs: most recent lines; describe: the Events section, usually
    the most relevant part). `app/static/js/deployment_pods.js` is new.
  - **Deployment Pods expanded into generic K8s resource browsing**:
    `/deployment-pods/<server_id>/resources/<kind>` +
    `.../resources/<kind>/describe` (JSON), covering **namespaces, nodes,
    services, ingresses, PVs, PVCs** — read-only (list + describe), same
    `deployment_pod.view` permission, same kube-only/access-gated pattern
    as pods. `RESOURCE_KINDS` registry + per-kind summarizer functions live
    in `deployment_pods/routes.py`; `KubernetesProvider.list_resources`/
    `describe_resource` are the generic kubectl-backed methods (namespaced
    kinds default to `--all-namespaces` with no `?namespace=` filter,
    cluster-scoped kinds never pass `-n`/`-A`). A shared tab nav
    (`deployment_pods/_nav.html`) switches between Pods and each resource
    kind. **Deliberately read-only** — no delete/edit actions were added,
    by explicit choice this session (kept the same safety level as the
    existing Logs/Describe pod modals).
  - Note: pod logs/describe routes moved from
    `/<server_id>/<namespace>/<pod_name>/...` to
    `/<server_id>/pods/<namespace>/<pod_name>/...` — needed so a namespace
    literally named "resources" could never collide with the new
    `/<server_id>/resources/<kind>/...` routes (Werkzeug prefers a static
    path segment over a dynamic one, which would otherwise silently route
    to the wrong handler).
  - **Manifest ordering**: `DeploymentManifest.order` (Integer), drag-and-drop
    within a group via SortableJS (`app/static/js/deployment_manifests.js`,
    same pattern as `menu-reorder.js`), persisted through
    `POST /deployment-manifests/reorder` (`deployment_manifest.manage`). A
    group deploy walks manifests ascending by `.order`; a group **stop**
    walks them **descending** — reverse of how they went up.
  - **Stop / teardown**: `DeploymentRun.action` ("deploy"/"stop").
    `enqueue_deployment_run(..., action="stop")` only creates executions for
    (manifest, server) pairs that are actually currently deployed (see
    `worker.get_current_deployment`/`is_currently_deployed` — based on each
    pair's most recent *successful* execution, so a failed stop doesn't
    wrongly flip "currently deployed" to false). Each stop execution has
    `source_execution_id` pointing at the deploy execution being undone, and
    the worker calls `provider.delete(source.rendered_yaml)` — the exact
    YAML that was applied, never freshly re-resolved. New routes
    `POST /deployment-manifests/stop` +
    `POST /deployment-manifests/api/stop-preview` (`deployment.trigger`),
    "Stop"/"Stop Group" buttons + a confirmation modal mirroring the
    existing deploy-trigger modal.
  - **Live-status poller**: `DeploymentExecution.live_status`/
    `live_checked_at`, refreshed by an independent background thread
    (`worker._status_poll_loop`, started via `start_status_poller` in
    `app/__init__.py`) at an admin-configurable interval
    (`SystemConfig.deployment_status_check_interval_seconds`, editable at
    `/config`, floor of 5s). Uses `KubernetesProvider.get_live_status`
    (`kubectl get -f -`); `CustomAPIProvider.get_live_status` raises
    `NotImplementedError` (no agent contract for this) and the poller just
    leaves those pairs' status as unknown rather than guessing.
  - **`DeploymentProvider.delete()`** added to the base interface —
    `KubernetesProvider` via `kubectl delete -f - --ignore-not-found=true`,
    `CustomAPIProvider` via HTTP DELETE (symmetric assumption to `apply()`'s
    POST, same "adjust if a real agent's contract differs" caveat).
  - **New Deployment Pods section** (`app/blueprints/deployment_pods/`,
    `/deployment-pods`, new `deployment_pod.view` permission): list pods
    (`kubectl get pods -o json`), view logs (`kubectl logs`, last 500
    lines), and describe (`kubectl describe pod`) on any Kubernetes-type
    server the user has access to (reuses `DeploymentServer.is_accessible_to`
    — same trust boundary as triggering a deploy). Kubernetes-only by
    design (namespaces/pods don't map to the generic "api" provider type);
    an "api"-type server 404s. New `list_pods`/`pod_logs`/`describe_pod`
    methods live only on `KubernetesProvider`, not the abstract base.
  - New migration `e57baba6abe0` (order/action/live-status/interval
    columns), applied to the dev DB. Note: `flask db upgrade` against the
    *test* DB doesn't work from a blank slate (pre-existing, unrelated
    migration-ordering issue) — irrelevant in practice since
    `tests/conftest.py` provisions the test schema via `db.create_all()`
    directly from the models, not Alembic.
  - Everything above is **not yet committed** — same batching pattern as
    before.
- Run tests via:
  ```bash
  source .venv/bin/activate && set -a && source .env && set +a
  export DATABASE_URL=$(echo $DATABASE_URL | sed 's/@db:/@172.29.16.1:/')
  export TEST_DATABASE_URL=$(echo $TEST_DATABASE_URL | sed 's/@db:/@172.29.16.1:/')
  FLASK_ENV=testing python -m pytest tests/ -q
  ```
  (the sandbox can't reach the `db` Docker Compose hostname directly — that
  `sed` swaps it for the WSL2 gateway IP; `.env`'s `DATABASE_URL`/
  `TEST_DATABASE_URL` already point at `172.29.16.1` directly as of this
  session, so the `sed` swap may be a no-op — check `.env` first)
- CSS changes need a rebuild to actually show up: `npm run build:css` (needs
  `npm install` first if `node_modules/` isn't present).
- Migration head: `dcedb8fa84b8` (`add_deployment_module_models`, on top of
  `128297392cfc`), applied to the dev DB.
- **New this session: the Deployment module** (see
  `docs/deployment-feature-plan.md` for the original spec). Register target
  servers (`/deployment-servers`, Kubernetes via `kubectl apply` shelled out
  against a stored kubeconfig, or a custom agent API), manage YAML manifests
  with `{{SYS:VERSION}}`/`{{SYS:VERSION:key}}` placeholders
  (`/deployment-manifests`) that resolve against an existing `Builder`'s
  latest (or pinned) successful `ImageBuild`, and view deploy history
  (`/deployment-runs`). Mirrors the Image Builder module's shape:
  `DeploymentManifest`≈`Builder`, `DeploymentRun`≈`BuildBatch`,
  `DeploymentExecution`≈`ImageBuild`. Runs on its own independent
  worker/queue (`app/services/deployment/worker.py`) — a deploy and a build
  can run concurrently, but only one deploy runs at a time system-wide.
  Group deploys **abort remaining executions on first failure** (marked
  `skipped`), unlike `BuildBatch`'s "continue and report partial_failure."
  `DeploymentServer` has its own `allowed_roles` gate (mirrors
  `Builder.allowed_roles`) — `deployment.trigger` alone isn't sufficient to
  deploy to a server outside a user's role/`deployment_server.manage`. No
  new pip dependencies were added — `KubernetesProvider` shells out to the
  `kubectl` CLI (like `DockerBuildEngine`/`KanikoBuildEngine` do for
  builds) rather than using the `kubernetes` client library, and the
  `{{SYS:VERSION}}` placeholder is resolved via regex substitution over the
  manifest text, not real YAML parsing.
- **Follow-up polish this session, on top of the Deployment module above:**
  - **YAML editor**: both the manifest `yaml_content` field
    (`/deployment-manifests`) and the `DeploymentServer` `kubeconfig` field
    (`/deployment-servers`) now use CodeMirror 5 (vendored at
    `app/static/vendor/codemirror/`, same pattern as SortableJS — minified
    with `terser`, no bundler; `codemirror` pinned to `^5.x` in
    `package.json`'s devDependencies, **not** the default `codemirror`
    resolves-to-v6-ESM-only). It's a progressive enhancement over the same
    `<textarea>` the server reads (`cm.save()` synced on form submit) — see
    `app/static/js/deployment_manifests.js` / `deployment_servers.js`.
    Tab is rebound to insert spaces (`indentWithTabs: false` +
    `insertSoftTab`) — CodeMirror's default Tab-key behavior inserts a
    literal tab character, which is invalid YAML indentation and caused a
    real "found character that cannot start any token" error on a live
    kubeconfig during this session (fixed both in code and in that stored
    row). New `{% block extra_head %}` hook added to `base.html` for
    per-page `<link>` tags (CodeMirror's CSS).
  - **DeploymentServer.test_connection's flash message is now short** — the
    full exception/output still goes to `log_error` (Error Logs page) via
    `ErrorLog.traceback`; the flash just says "...failed. See Error Logs
    for details." instead of embedding the raw (sometimes very long)
    exception text.
  - **`DeploymentManifest.server_ids` is now required** (`DataRequired()`
    on the form field, not just optional) — saving a manifest with zero
    target servers used to succeed silently and only fail later at deploy
    time; now it's rejected immediately with a clear inline error.
  - **Real data note**: the actual `DeploymentServer` row "TEBET-APP-1" in
    the dev DB had a corrupted kubeconfig (literal tabs from the pre-fix
    editor) and a couple of real config issues, fixed directly against that
    row during this session: dropped a conflicting `insecure-skip-tls-verify:
    true` (kept `certificate-authority-data` instead, per the user's
    choice), and corrected `server:` from `10.5.0.3` to `192.168.18.152` to
    match the API server cert's actual SANs. Connection test now gets all
    the way to a network-level timeout — confirmed via a raw TCP probe that
    **this sandbox has no route to that private LAN IP at all**, so "Test
    Connection" can only be meaningfully verified from wherever Hamilton is
    actually deployed, not from this sandbox.
- **Not yet committed.** This session's Deployment module + follow-up work
  (6 new models, 1 migration, `app/services/deployment/*`, 3 new blueprints
  + templates + JS, vendored CodeMirror, 6 test files, seed updates) is
  sitting uncommitted on top of `4a8a663` — as always, don't commit/push it
  without being asked first. Everything prior through `4a8a663` ("add image
  builder feature", on top of `1e33c97` "first commit") is still pushed to
  `main` on `origin` (`git@github.com:daniirsyad/deployment_app.git`).
  Local `main` has no configured upstream tracking branch (`git branch -vv`
  won't show `[origin/main]`) even though it *is* pushed and matches — set
  one (`git branch -u origin/main`) if that ever gets in the way, or just
  keep using explicit `git push origin main`.
- Enduring infra facts:
  - When manually curling a POST endpoint for verification (not via pytest,
    which disables CSRF under `TestingConfig`), remember that a form
    instantiated with `prefix=...` (which most create/edit forms in this
    app use — `CREATE_PREFIX`, `_edit_prefix(id)`, etc.) prefixes **every**
    field including the hidden `csrf_token` itself, e.g.
    `manifest-<uuid>-csrf_token`, not a bare `csrf_token` — grep the actual
    rendered `id="..."` first rather than assuming the field name.
  - The "docker" build engine runs bare-metal on the host (shells out to the
    host's own `docker` CLI over the mounted socket); "kaniko" runs
    containerized/daemonless. `buildx` needed on the host for local
    "docker"-engine builds regardless of the Dockerfile's own copy.
  - `REPO_CLONE_ROOT` (`<app>/data/repos`) has no persistent volume mounted —
    registered repos' local clones are lost on container restart. Re-sync and
    the build worker self-heal via `sync_repo(repo_name=...)`; the Builder
    create/edit branch/Dockerfile pickers and `/github`'s unregistered-repo
    listing do not.
