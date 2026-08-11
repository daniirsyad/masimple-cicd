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
   should already be enough context to start.

Then ask me what to work on next rather than assuming.

## Current state

- **542 tests passing.** For the full story of how the Deployment module was
  built and extended (Part 6), the direct Error Log links feature (Part 7),
  real-time pod log streaming over SSE (Part 8), and the new Workflow
  feature (Part 9), see `AI_CONTEXT.md` — this section only tracks what's
  true *right now*, not how it got there.
- **⚠️ Uncommitted work in the working tree**: the entire Workflow feature
  (new `workflows` blueprint, 5 new models, `app/services/workflow/`
  resolver+orchestrator, new migration `1268e9419f60`, new templates/JS,
  referential-integrity guards added to `builders.routes.delete_builder()`
  and `deployment_manifests.routes.delete_manifest()`, plus edits to
  `seeds/seed_admin.py`/`seeds/seed_menu.py`) is implemented, migrated, and
  tested against the dev DB but **not yet committed** — this project's
  established pattern is the user batches and pushes on their own terms
  (see AI_CONTEXT.md Part 6's closing note), so don't commit/push it
  unprompted. Everything through the pod-logs-streaming work (Part 8) is
  already committed and pushed (`f4b02d9`); local `main` matches
  `origin/main` up to that point only.
- **Migration head is `1268e9419f60`** (add workflow models) — already
  applied to the dev DB via `flask db upgrade`, and the new
  `workflow.view`/`workflow.manage`/`workflow.run` permissions are already
  seeded (`seeds/seed_admin.py` was re-run — safe, matches by permission
  `code`). One new `Menu` row ("Workflows") was inserted **directly** into
  the dev DB (not via `seeds/seed_menu.py` — see the next bullet).
- **⚠️ Do not run `python seeds/seed_menu.py` against the dev DB** until its
  `get_or_create()` matching (currently exact `label` + `parent_id`) is
  fixed to tolerate renames, e.g. matching by `url` instead — this DB's
  real menu labels have drifted from what the script expects (real
  "Builder"/"Servers"/"Yaml/Manifests"/"Runs" vs. the script's "Image
  Builder"/"Deployment Servers"/"Deployment Manifests"/"Deployment Runs"),
  so re-running it silently creates duplicate sidebar rows. Caught and
  cleaned up twice already (see Part 7 in `AI_CONTEXT.md`) — do not run it
  again without fixing the matching logic first. `seeds/seed_admin.py`
  doesn't have this problem (it matches by permission `code`, which hasn't
  drifted) and is safe to re-run as usual. If a new feature needs exactly
  one new sidebar entry, insert that single `Menu` row directly (see how
  the Workflows entry was added) rather than running the whole script.
- Run tests via:
  ```bash
  source .venv/bin/activate && set -a && source .env && set +a
  FLASK_ENV=testing python -m pytest tests/ -q
  ```
  (`.env`'s `DATABASE_URL`/`TEST_DATABASE_URL` already point at the WSL2
  gateway IP `172.29.16.1` directly, not the `db` Docker Compose hostname —
  no `sed` swap needed from inside the sandbox.)
- CSS changes need a rebuild to actually show up: `npm run build:css`
  (already run as of the Workflow feature's new templates).
- **gunicorn now runs `--worker-class gthread --threads 4 --timeout 120`**
  (`entrypoint.sh`), not plain sync workers — changed to support the pod-logs
  SSE stream (Part 8), which needs a long-lived connection that a sync
  worker can't hold without blocking the whole app. Keep this in mind before
  adding any other long-lived-connection feature: worker/thread capacity is
  now a real, finite budget (3 workers × 4 threads), not "one request per
  worker, always fine."
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
  - The app now runs **four** independent background poll threads (build
    worker, deploy worker, deploy live-status poller, and the new workflow
    orchestrator), each with its own DB-queue and its own concurrency
    story — none of them execute each other's work; the workflow
    orchestrator only ever enqueues into the build/deploy workers' existing
    queues and watches for terminal status, see AI_CONTEXT.md Part 9.
