# Session Start Prompt

Paste the block below into a new chat to pick up this project with full context.

---

I'm continuing work on this Flask app (MASIMPLE CICD) at /home/daniirsyad/work/code/masimple-cicd.

First, read these files in full before doing anything else:

1. **CLAUDE.md** — project conventions (blueprint structure, RBAC/permission
   patterns, UUID PKs, `log_activity`, testing setup, "do not touch" list, etc.).
2. **APP_SUMMARY.md** — current-state application overview (tech stack,
   architecture, full data model, feature-by-page list, conventions, known
   gaps). No change history — just what the app *is*, right now. The fastest
   way to get oriented.
3. **AI_CONTEXT.md** — deep historical spec/progress log, chronological
   Parts 1–9, only if you need the *why* behind something (a past decision,
   a bug that was already found and fixed a certain way, a design tradeoff).
   Not required reading for routine work — `APP_SUMMARY.md` plus this file
   should already be enough context to start. **Note: not yet updated past
   Part 9** — everything in the "Current state" section below (both this
   session's and the prior session's work) postdates it and has no
   narrative write-up there yet.

Then ask me what to work on next rather than assuming.

## Current state

- **911 tests passing** (as of the last full run).
- **This session's work** (on top of everything below) — three features,
  built in sequence, each planned via plan-mode with the user before
  implementation. Not yet committed/pushed as of this note; see the git
  status check at the end of this session for whether that's since changed.
  1. **Workflow build steps can auto-generate their Version Bump/Change
     Type/Object/Message at run time instead of requiring them typed in at
     authoring time**, with an optional human-review gate. Reuses the
     existing heuristic (Conventional-Commits regex, `bump_heuristic.py`) +
     AI (`compute_build_prefill()`) engine that already powered the
     authoring-time "Preview from Git" modal — just invokes it from
     `app/services/workflow/worker.py::_start_step()` on every actual run
     instead of only at step-authoring time. Two new `WorkflowStep` columns:
     `auto_generate_build_metadata` (leaves the four fields blank/optional
     when set — `BuildStepForm`'s validators changed from `DataRequired()`
     to `Optional()`, with the route enforcing all-or-nothing) and
     `require_review_before_build` (default `True`, matching this app's
     existing "never apply raw AI output unseen" rule — see
     `build_prefill.py`'s own docstring). With review off, the generated
     values are applied immediately; with review on, `_start_step()` parks
     a `WorkflowStepRun` in a new `"awaiting_review"` status (no `batch_id`
     yet — the orchestrator's poll loop already no-ops on any non-`"running"`
     step_run, so this is safe with no other changes needed) with the
     suggestions stashed in four new nullable columns
     (`suggested_bump_type`/`suggested_change_type_id`/
     `suggested_object_names`/`suggested_description`). Two new routes,
     `workflows.approve_step_run`/`reject_step_run` (gated by the existing
     `workflow.run` permission), let a human edit-then-approve (enqueues the
     real `BuildBatch`) or reject (fails the step via a renamed, now-public
     `finish_step()`, previously private `_finish_step()`, in
     `workflow/worker.py`) from a new review panel on the Workflow Run
     detail page. That page's live-status-polling JS reloads once, the
     first time a step transitions into `awaiting_review` while already
     being watched, since the review panel itself is server-rendered
     outside the polled step table (so it doesn't get wiped by the poller's
     own `innerHTML` replacement) and can't otherwise appear without a
     manual refresh. Migration `12fa3cb1cfd5`.
  2. **Every "Delete" (or similar) button that can currently fail with a
     post-click flash error is now disabled up front, with a tooltip
     explaining why** — 16 such guards found and fixed across 10
     blueprints (Workflows ×2, Versions, Git Sources ×2, Deployment Servers
     ×2, Dockerfiles, Builders ×2, Permissions ×2, Deployment Manifests ×3,
     Registries; Roles' pre-existing `is_system` guard, which already hid
     the button entirely, was left as-is; Menus' guard just needed its
     already-computed-but-unused `has_children_map` wired into the
     template; Users' self-delete guard turned out to already be handled
     too). New shared macro `app/templates/partials/_macros.html`'s
     `disabled_attrs(reason)` renders `disabled title="..."` when given a
     reason, nothing otherwise — imported into every affected template.
     Each blueprint's index/detail route now computes a `{id: reason}` dict
     using the exact same query its own delete route already runs, so the
     tooltip text always matches what the flash message would have said.
     Also added: a "Run" button directly on the Workflows **index** page
     (not just the detail page), reusing the exact same form/disabled-state
     pattern as the existing one.
  3. **A "Disable" button now appears next to Delete, exactly when Delete
     is blocked, for the 8 "resource" blueprints** (Version, Git Sources,
     Deployment Server, Dockerfile, Builder, Deployment Manifest, Registry,
     Workflow) — archiving the item onto a new `/<blueprint>/archived` page
     (hidden from the main list) with a "Restore" button to bring it back.
     Confirmed with the user up front: Disable only shows when Delete is
     already blocked (not a general always-available archive action); scope
     is these 8 "resource" blueprints, not Roles/Permissions/Menus/Users;
     and disabling must be **functionally** enforced, not just cosmetic. New
     `is_active` column (migration `c5ebe879231b`) on all 7 of these models
     that didn't already have one — `Workflow` reused its pre-existing
     `is_active`. Every dropdown that offers one of these for a *new*
     selection elsewhere (e.g. Version/Dockerfile/RegistryTarget on the
     Builder create/edit form, DeploymentServer on the Manifest form,
     Builder/DeploymentManifest in Workflow step authoring) now filters to
     active-only, with a "keep the current value visible" fallback
     (mirroring the pre-existing `_branch_choices()` pattern in
     `builders/routes.py`) on an *existing* reference's own edit form, so
     disabling something already in use doesn't silently drop it or break
     re-saving an unrelated field. The two places that matter most for real
     enforcement (not just hiding a dropdown option):
     `app/services/workflow/resolver.py`'s
     `resolve_builders_from_selection()`/`resolve_step_manifests()` — the
     run-time group/individual-selection resolver a saved `WorkflowStep`
     re-runs on *every* run, not just once at authoring — now drop a
     disabled Builder/Manifest there too; and
     `deployment_manifests.routes.py`'s `deploy()`/`update()` (via
     `_trigger_deploy_action()`) now explicitly refuse a disabled manifest,
     while `stop()`/`restart()` (via `_trigger_teardown_style_action()`)
     deliberately do **not** — a manifest can be archived while still
     live (that's one of its own three delete-blocking reasons), and
     something archived while still deployed must stay stoppable; only
     *new* rollouts of it are blocked. `builders.build()`/`build_preview()`
     also explicitly reject a disabled Builder id even if POSTed directly,
     bypassing the (already-filtered) UI checkboxes.
  Two prior test-coverage gaps found and closed along the way, unrelated to
  the features themselves: the `menus` and `permissions` blueprints had **no
  test file at all** before this session (`tests/test_menus.py`,
  `tests/test_permissions.py` are new).
- **A prior session's work** (on top of everything below), already pushed to
  `origin/main`:
  1. **The container image never had `kubectl` installed at all** — every
     `DeploymentServer` action (test-connection, apply/delete, pods/
     secrets/configmaps management, rollout restarts — anything going
     through `KubernetesProvider`, which shells out to `kubectl` rather
     than using a Kubernetes REST client) failed with `RuntimeError: Could
     not reach cluster: [Errno 2] No such file or directory: 'kubectl'` the
     moment someone deployed this app's own image to a real environment,
     surfaced when the user deployed to their own Kubernetes cluster (one
     this session has no access to). `Dockerfile` already copied in a
     `docker` CLI and `kaniko-executor` as pinned static binaries but
     simply never did the equivalent for `kubectl`. Fixed by downloading
     the official release binary (`https://dl.k8s.io/release/${KUBECTL_
     VERSION}/bin/linux/$(dpkg --print-architecture)/kubectl`, arch-aware)
     into `/usr/local/bin/kubectl`, pinned via a new `KUBECTL_VERSION`
     build arg (default `v1.30.4`, overridable with `--build-arg
     KUBECTL_VERSION=vX.Y.Z` if a cluster needs a version closer to its own
     — kubectl supports ±1 minor version skew from the server). Verified
     by actually building the image locally (`docker build .`) and running
     `kubectl version --client` inside it — v1.30.4, works. `curl` was
     added to the apt-get line too (only needed transiently to fetch the
     binary).
  2. **Editing a Kubernetes Secret's value now auto-restarts the Deployments
     that consume it.** Root-caused a report of "updating a secret in
     `/deployment-pods` doesn't update the Kubernetes secret" — the Secret
     *object* was actually being patched correctly all along (`stringData`
     unconditionally overrides `data` for the same key at the API-server
     level, regardless of `kubectl apply` merge history); the real gap was
     that Kubernetes only injects a Secret's values into a container's env
     vars once, at container start, so an already-running Pod never picks
     up the new value on its own (confirmed by the reporter via `kubectl
     exec ... echo $VAR` still showing the old value). Fixed by adding
     `KubernetesProvider.find_deployments_using_secret()`
     (`app/services/deployment/kubernetes_provider.py`) — scans every
     Deployment in a namespace for a pod-template reference to a given
     Secret (`env[].valueFrom.secretKeyRef`, `envFrom[].secretRef`, or
     `volumes[].secret.secretName`, across containers and initContainers)
     — and wiring `edit_secret()`
     (`app/blueprints/deployment_pods/routes.py`) to roll-restart
     (`kubectl rollout restart deployment/<name>`) every match right after
     a successful opaque-secret update (only when a key was actually
     added/changed/removed, never on a no-op save). `_apply_and_respond()`
     gained a generic `on_success` hook for this. Each restart gets its own
     `RESTART_WORKLOAD` activity-log entry; a scan/restart failure is
     logged but never turns the secret update itself into a failure, since
     the Secret was already applied successfully by that point. The
     documented `kubectl apply` merge caveat for *removing* a key on a
     secret's very first edit (see `update_secret()`'s own docstring) is
     still unfixed — separate, narrower issue, not what was reported here.
  3. **A missing/malformed `CREDENTIAL_ENCRYPTION_KEY` no longer 500s.**
     Found via a real deployment attempt (`k8s/deployment.yaml`'s Secret
     still had its literal `CREDENTIAL_ENCRYPTION_KEY: "<GENERATE_A_FERNET_
     KEY>"` placeholder, never filled in) — saving System Config with a
     Telegram bot token crashed with a raw, unstyled "Internal Server
     Error" instead of anything actionable. `app/utils/crypto.py`'s
     `_get_cipher()` now raises a new `CredentialEncryptionError`
     (a `RuntimeError` subclass) with a clear, actionable message — both
     for the pre-existing "not set" case and a new "set but not a valid
     Fernet key" case (catches the `ValueError` `Fernet(...)` raises on a
     malformed/placeholder value) — and `decrypt()`'s existing "stored
     credential could not be decrypted" case now raises the same type. A
     new global `app.errorhandler(CredentialEncryptionError)` in
     `app/__init__.py` turns it into a normal flash + redirect back to
     `request.referrer` (still logs an `ErrorLog` entry itself, since
     catching it here means Flask's `got_request_exception` signal no
     longer fires for it) — covers every `encrypt()`/`decrypt()` call site
     app-wide (system config, AI settings, Git sources, registries,
     deployment servers), not just the Telegram token field that surfaced
     it. **Still true and unresolved**: `k8s/deployment.yaml`'s Secret
     placeholders (`CREDENTIAL_ENCRYPTION_KEY`, `SECRET_KEY`,
     `DATABASE_URL`, `ADMIN_PASSWORD`) are not filled in — the user is
     deploying to their own cluster this app has no access to, so this
     needs a real Fernet key (`python -c "from cryptography.fernet import
     Fernet; print(Fernet.generate_key().decode())"`) and the other real
     values set on their end, applied out-of-band (not committed into this
     file) before `/config/` (or anything else that encrypts a credential)
     will work there.
- **Everything before this session is committed and pushed to
  `origin/main`**, most recently as
  six commits from one session — all found and fixed by actually trying to
  *run* this app for the first time, via a new Podman-based trial deploy
  (`docker-compose.podman.yml`, see below) rather than only the usual
  bare-metal `.venv` dev flow. None of it was caught by the test suite alone:
  1. **Kubernetes manifests + the Podman trial-deploy compose file** —
     `k8s/deployment.yaml` (Namespace/Secret/Deployment/Service) +
     `k8s/network-policy.yaml`, a starting point for an actual cluster
     deploy; every per-environment value (registry/image, DB credentials,
     NetworkPolicy IP whitelist, the `hostPath` for `/app/data`) is an
     obvious `<PLACEHOLDER>` — none of it is real yet.
     `docker-compose.podman.yml` is the web-only (no `db` service) compose
     file actually used this session — points straight at an existing
     external Postgres server (`PODMAN_DATABASE_URL` in `.env`, separate
     from bare-metal dev's own `DATABASE_URL`) rather than a
     compose-managed one, and mounts Podman's rootless API socket
     (`systemctl --user enable --now podman.socket`) at the same
     `/var/run/docker.sock` path the image builder's `docker` CLI expects.
  2. **Clipboard copy buttons fixed on insecure (non-`localhost`) origins**
     — `navigator.clipboard` only exists in a secure context (HTTPS, or
     literally the hostname `localhost`); opening the app via any other
     hostname/IP over plain HTTP left every copy button throwing
     (`Cannot read properties of undefined (reading 'writeText')`). A
     `document.execCommand("copy")` fallback was tried first but confirmed
     in practice to sometimes report success on an insecure origin while
     silently never reaching the real OS clipboard (a Chromium quirk, not
     something page script can reliably detect). Both copy buttons
     (`copy-to-clipboard.js`, `yaml_generator.js`) now fall back to
     `window.prompt()` instead — real browser-native UI, not page-scripted,
     so a manual Ctrl+C/Cmd+C out of it always actually works.
  3. **Build/push pipeline hardened; Kaniko disabled app-wide** — several
     fixes discovered trying to actually trigger a build against the
     Podman trial deploy:
     - **Kaniko is no longer selectable** as a build engine — removed from
       `SystemConfig.build_engine`'s choices
       (`app/blueprints/system_config/forms.py`) and
       `app/services/build/factory.py`'s `_ENGINES` map.
       `KanikoBuildEngine` itself (`app/services/build/engine.py`) is left
       intact for later, just unreachable. **Why**: it runs
       `kaniko-executor` as a bare subprocess of this app, with no
       container/chroot of its own — confirmed in practice that building
       this repo's *own* multi-stage Dockerfile extracted
       `node:20-alpine`'s layers straight onto the **running app
       container's own filesystem** (overwrote `/etc/os-release` to
       report Alpine instead of Debian, dropped Alpine's `node`/`npm`
       binaries into `/usr/local/bin`). The real fix is running kaniko in
       its own throwaway container per build (e.g. `docker run` over the
       same socket `DockerBuildEngine` already uses) — this change just
       stops it from being triggerable until that's built. If this
       resurfaces: the live app container had to be recreated
       (`podman-compose down && up --build`) to clear the contamination:
       a plain restart wouldn't have, since it was in the container's own
       writable layer, not a volume.
     - **`DockerHubProvider.docker_config_auth_key`** (new property on
       `RegistryProvider`, defaults to `registry_host`) — Docker Hub is
       special-cased in the Docker/OCI credential convention: an
       unqualified reference (no host prefix, e.g. `myuser/myapp`)
       resolves to the default registry `index.docker.io`, and both
       `docker login` and go-containerregistry (Kaniko's credential-file
       lookup) key that default's auth entry under the legacy string
       `"https://index.docker.io/v1/"` — **not** `registry_host`
       (`registry-1.docker.io`, the real pull/push API host). This bit
       twice: Kaniko's generated `~/.docker/config.json` was keyed by
       `registry_host`, so it found no matching credentials and pushed
       anonymously (401); and `docker_client.login()` omitted `registry=`
       entirely, which a real `dockerd` defaults internally but Podman's
       Docker-API-compatible `/auth` endpoint does not, and 500'd trying
       to ping `https:///v2/` with no host at all. Both now use
       `docker_config_auth_key`.
     - **`DockerBuildEngine.CLIENT_TIMEOUT_SECONDS = 600`** — docker-py's
       default 60s is a **per-read** socket timeout, too tight pushing a
       real several-hundred-MB image through Podman's Docker-API socket
       (slower than a native `dockerd` push); raised a bare `ReadTimeout`
       mid-push with no retry.
     - **`BuildEngine.cleanup_local_image()`** — a Docker-engine build's
       locally-loaded image (buildx's `--load`) was never removed after a
       successful push, growing local daemon storage by a full image on
       *every single build, forever* — confirmed reaching 9.4GB / 105
       images (69% reclaimable) on the trial-deploy host from ordinary use
       plus this session's own repeated rebuilds. Default no-op on the
       base class for engines that push as part of `build_image()` itself
       (Kaniko), since those never load anything locally to begin with;
       `DockerBuildEngine` overrides it to actually remove the image.
       Called by the worker right after a successful push, best-effort —
       a cleanup failure is logged (`ErrorLog`, source
       `worker.cleanup_local_image`) but never flips the build to
       `failed`.
     - **`ImageBuild.error_log_id`** (new nullable FK, migration
       `7d79f7acc529`) — links a failed build to the `ErrorLog` row its
       own failure created (both `log_error()` call sites in
       `_run_build` now capture the returned entry), so `/images` shows a
       direct "View error" link per failed row instead of making someone
       go search Error Logs for the matching entry — gated behind
       `logs.view`, same permission the Error Logs page itself requires.
       Pre-existing failed builds (from before this migration) were
       backfilled by hand, matching each `ImageBuild.id` embedded
       verbatim in its `ErrorLog.message` text (`log_error`'s
       `description=f"Build {build.id} ..."`) — no script for this was
       kept, it was a one-off run directly against the DB.
  4. **Fixed background worker threads silently not starting, or dying,
     under gunicorn** — two separate bugs found chasing a build stuck
     permanently in `queued`:
     - All four background poll threads (build worker, deploy worker,
       deploy status poller, workflow orchestrator) shared this guard,
       meant to stop Flask's *own* dev-server reloader from
       double-starting them in its doomed parent watcher process:
       `if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true": return`.
       `app.debug` is just a config flag with no bearing on which WSGI
       server is actually running the app — gunicorn never sets
       `WERKZEUG_RUN_MAIN` either, so **any** deployment with
       `FLASK_ENV=development` (this repo's own `.env`, reused directly
       as `docker-compose`'s `env_file`) silently never started *any* of
       these threads at all under gunicorn: nothing was ever polling the
       build/deploy queues, triggered work just sat "queued" forever with
       no error anywhere. New `app/utils/runtime.py`'s
       `is_werkzeug_reloader_parent()` also checks `"gunicorn" in
       sys.modules` — only a real gunicorn worker process has that
       loaded — to tell it apart from Werkzeug's reloader parent, which
       does not.
     - Separately (and this is what actually caused the "stuck in
       queued" incident, once the guard bug above was already fixed):
       the build and deploy workers' `_poll_loop`s had **no
       per-iteration error handling at all**, unlike the heartbeat and
       status-poll loops, which already did. One uncaught exception (a
       schema briefly out of sync mid-deploy — the `error_log_id` column
       above didn't exist yet on this trial-deploy DB for a few minutes
       between deploying the code and running the migration against it)
       permanently killed the thread, with no way to recover short of
       restarting the whole process — the DB fix alone didn't help,
       since the thread was already dead. Both now wrap the claim/reap
       step in try/except: log-and-retry instead of dying.
  5. **First-time database setup wizard** — `entrypoint.sh` no longer runs
     `flask db upgrade` + the seed scripts unconditionally on every
     container start (still waits for DB connectivity first). A new
     `/setup` page (`app/blueprints/setup/`, `app/utils/setup_status.py`)
     now gates every other route — via a `before_request` hook in
     `app/__init__.py` — until the DB is confirmed at migration head
     *and* seeded (Alembic's current-vs-head revision, plus whether a
     `User` row exists). Shows DB connectivity and empty/non-empty status
     either way; a button always lets you run the same migrate+seed steps
     `entrypoint.sh` used to, regardless of which state it's in. Skipped
     entirely under `TestingConfig` (`app.testing`), since the test DB is
     built via `db.create_all()`, not Alembic, and would otherwise never
     look "set up" to this check. **Note**: this means a future deploy
     shipping new migrations will now show `/setup` again too, not just a
     brand-new install — routine upgrades are no longer silently
     auto-migrated on boot. Ask if you'd rather restore auto-migration for
     upgrades and keep `/setup` as a fresh-install-only fallback instead.
     - Follow-on fix, found the hard way (a bare 500 on `/setup` itself):
       hitting any route before/while the DB isn't fully set up could
       poison the shared SQLAlchemy session — a failed query leaves its
       transaction aborted until explicitly rolled back — which then made
       *every other* query in that same request fail too, including the
       error-logging path itself trying to look up `current_user` to
       attribute the error, turning what should've been a clean redirect
       into a raw, unstyled "Internal Server Error". `load_user()`
       (`app/__init__.py`) and `log_error()`
       (`app/utils/error_logger.py`) now catch and roll back instead of
       letting it propagate. `is_setup_complete()` also stopped caching
       "complete" forever in-process — it re-checks every time — since a
       permanent cache meant a DB that lost its tables *after* first
       being confirmed complete (e.g. someone manually dropped them) was
       never re-detected as needing setup again.
  6. **Default sidebar icons for menus that were missing one** — only
     Dockerfiles/YAML Generator/Workflows had an `icon` set; every other
     menu item (Home, Users, Roles, Deployment, System, ...) rendered
     with no icon at all, next to the ones that did. Added a matching
     Lucide icon (`seeds/seed_menu.py`) for each remaining item, plus a
     one-off backfill (`migrate_add_missing_icons`) so already-seeded
     databases pick them up too, not just fresh installs.
  Anything older is covered by `git log`/`AI_CONTEXT.md`, not repeated here.
- **Migration head is `c5ebe879231b`** (`7d79f7acc529` → `12fa3cb1cfd5`
  (this session's workflow-auto-generate columns) → `c5ebe879231b` (this
  session's `is_active` columns)) — applied to the bare-metal `.venv` dev DB
  (`masimple_cicd`) this session. **Not yet applied to the Podman
  trial-deploy DB** (an external Postgres server, dbname `postgres` — see
  `docker-compose.podman.yml`'s `PODMAN_DATABASE_URL` in `.env`) — that
  deploy hasn't been re-run since these two new migrations landed; it'll
  show `/setup` again (or need `flask db upgrade` run against it directly)
  next time it's touched.
- **This session's Podman trial deploy is worth repeating after any future
  change to the build/deploy pipeline** — every fix in commit 3/4 above was
  found only by actually running the app end-to-end this way; none of it
  was (or realistically could be, without a live daemon/socket) caught by
  the test suite. `podman-compose -f docker-compose.podman.yml up -d
  --build` from a shell with `podman.socket` enabled
  (`systemctl --user enable --now podman.socket`) is enough to stand it
  back up.
- **`seeds/seed_menu.py`'s label-drift risk is dormant, not fixed** — see
  `AI_CONTEXT.md` Part 7 and the `seed_menu_label_mismatch` memory (stale/
  historical) if this resurfaces.
- Run tests via:
  ```bash
  source .venv/bin/activate && set -a && source .env && set +a
  FLASK_ENV=testing python -m pytest tests/ -q
  ```
  (`.env`'s `DATABASE_URL`/`TEST_DATABASE_URL` already point at the WSL2
  gateway IP `172.29.16.1` directly, not the `db` Docker Compose hostname —
  no `sed` swap needed from inside the sandbox.)
- CSS changes need a rebuild to actually show up: `npm run build:css`.
- **gunicorn runs `--worker-class gthread --threads 4 --timeout 120`**
  (`entrypoint.sh`), not plain sync workers — changed to support the pod-logs
  SSE stream. Keep this in mind before adding any other long-lived-connection
  feature: worker/thread capacity is a real, finite budget (3 workers × 4
  threads), not "one request per worker, always fine." All four background
  poll threads (build/deploy/status/workflow) now correctly start in *every*
  one of these worker processes even with `FLASK_ENV=development` — see
  commit 4 above; this was silently broken before this session, with no
  error anywhere to indicate it.
- **TEBET-APP-3's client certificate expired 2026-08-09** — a real, live
  dev-DB `DeploymentServer` row. "Test Connection"/deploys/pod browsing
  against it will fail with "the server has asked for the client to provide
  credentials" (TLS handshake succeeds, cert is just past its
  `not_valid_after`) until someone re-issues a kubeconfig for it from the
  cluster side — not something fixable from this app.
- Enduring infra facts:
  - When manually curling a POST endpoint for verification (not via pytest,
    which disables CSRF under `TestingConfig`), remember that a form
    instantiated with `prefix=...` (which most create/edit forms in this
    app use — `CREATE_PREFIX`, `_edit_prefix(id)`, etc.) prefixes **every**
    field including the hidden `csrf_token` itself, e.g.
    `manifest-<uuid>-csrf_token`, not a bare `csrf_token` — grep the actual
    rendered `id="..."` first rather than assuming the field name. An
    AJAX-only endpoint not backed by a rendered form at all (e.g.
    `/yaml-generator/generate/<kind>`) instead reads the CSRF token from a
    `data-csrf` attribute on a hidden per-page `<span>` and sends it as the
    `X-CSRFToken` header — Flask-WTF's global `CSRFProtect` checks that
    header automatically, and the form itself is instantiated with
    `meta={"csrf": False}` so its own embedded `csrf_token` field
    (which the AJAX body never includes) doesn't also get checked and fail.
  - **Kaniko is disabled, not just "an alternative to docker"** — see
    commit 3 above. Only `"docker"` is a selectable `SystemConfig.
    build_engine` right now. `DockerBuildEngine` shells out to `docker
    buildx build`; against Podman's socket specifically (no native
    BuildKit support server-side), buildx's `docker-container` driver
    transparently spins up its own `moby/buildkit` container to do the
    real work instead — confirmed working end-to-end (build, run, correct
    output) — rather than needing the daemon itself to support BuildKit.
  - The "docker" build engine runs bare-metal on the host (shells out to the
    host's own `docker` CLI over the mounted socket, real `dockerd` or
    Podman's Docker-API-compatible socket alike); "kaniko" is currently
    disabled (see above) rather than "runs containerized/daemonless" as
    previously — its actual problem is the *opposite* of daemonless
    isolation: no isolation from *this app's own* container at all.
    `buildx` needed on the host (or in this app's image, which already has
    it) for local "docker"-engine builds regardless of the Dockerfile's own
    copy.
  - `REPO_CLONE_ROOT` (`<app>/data/repos`) has a persistent named Docker
    volume (`repo_clones`, `docker-compose.yml`) — registered repos' local
    clones survive container restarts. This is a Docker-*managed* named
    volume, **not** a bind mount to the project's own `./data/repos` folder
    on the host — editing/adding a file under the project checkout's
    `data/repos` does nothing; reach the real clone via
    `docker compose exec web ls /app/data/repos`. The new `k8s/deployment.yaml`
    takes a different approach for an actual cluster deploy: a `hostPath`
    volume mounted at `/app/data` (not a PVC) with a placeholder node path,
    per explicit request — ties the pod to whichever node has that
    directory unless a `nodeSelector`/`nodeName` is also added.
  - The app now runs **four** independent background poll threads (build
    worker, deploy worker, deploy live-status poller, workflow
    orchestrator), each with its own DB-queue; none execute each other's
    work. Both the build and deploy workers additionally run a
    **heartbeat** thread each: every claimed job's `heartbeat_at` is ticked
    every 15s while it runs, and a stale/missing heartbeat (>60s) on the
    single `status='running'` row is auto-reaped as a failure. All four
    (and both heartbeat threads) now reliably start under gunicorn — see
    commit 4 above.
  - **If `SystemConfig.telegram_notifications_enabled` is turned on**,
    whatever host runs this app needs outbound HTTPS access to
    `api.telegram.org` — the Bot API call is a plain `requests.post` with a
    10s timeout and no retry; a network-level block just makes every
    notification silently fail (logged to Error Logs, never raised into
    the login/reset flow calling it).
