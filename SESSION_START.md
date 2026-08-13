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
   Part 9** — everything in the "Current state" section below (the reliability
   fixes, the six new providers, the YAML editor fix, and the YAML Generator
   page) postdates it and has no narrative write-up there yet.

Then ask me what to work on next rather than assuming.

## Current state

- **656 tests passing** (as of the last full run — see the uncommitted-work
  note below for what that count includes).
- **Everything through "Implement Custom API AI provider" is committed and
  pushed to `origin/main`** (`ae4c6fb`). That range covers, each as its own
  commit: the Workflow module; heartbeat-based crash recovery for the
  build/deploy workers; a persistent Docker volume for cloned repos plus a
  Builder-edit-form fix; tests for the custom-agent (`api`-type)
  `DeploymentServer` HTTP contract; and six previously-stubbed providers now
  fully implemented (GHCR, Harbor, ECR registry providers; Claude, Gemini,
  Custom API AI providers).
- **⚠️ Uncommitted work in the working tree, and NOT YET VERIFIED as working
  — do not consider this done**: a YAML-editor cursor-position bug fix
  (shared `app/static/js/yaml_editor.js`, replacing two independently
  duplicated CodeMirror-init blocks in `deployment_manifests.js`/
  `deployment_servers.js`) plus a new `/yaml-generator` page/blueprint
  (`app/blueprints/yaml_generator/`, `app/services/yaml_generator/`) that
  builds Deployment/Service/ConfigMap/Secret/Ingress YAML from form fields
  with a live preview, Copy/Download, and a "Save as Manifest" hand-off into
  the existing Deployment Manifest creation flow. Automated tests
  (`tests/test_yaml_generator.py`, 20 tests) all pass and are part of the
  656 count above, but **the actual cursor-bug fix itself has been through
  two live-browser round-trips with the user and is still mid-verification**:
  1. First attempt (disable CodeMirror `lineWrapping`, defer `cm.refresh()`
     via a double `requestAnimationFrame` after a `<dialog>` opens) — user
     confirmed live the bug still reproduced, but found that scrolling the
     editor down then back up fixed it immediately every time.
  2. Second attempt automated that: switched the trigger to a
     `ResizeObserver` on the editor's own wrapper element (fires once the
     browser has actually committed a real layout size, not a guessed frame
     count), and on trigger did `cm.refresh()` + a scroll-position nudge +
     a **resize nudge** (`cm.setSize()`). This **broke the editor
     entirely — it stopped rendering** — because the resize nudge mutated
     the exact element the `ResizeObserver` was watching, creating a
     feedback loop (each nudge re-triggered the observer, which nudged
     again).
  3. Third attempt (current state, just applied, **not yet confirmed
     working**): removed the resize nudge, keeping only `cm.refresh()` +
     the scroll-position nudge, triggered via the same `ResizeObserver`.
     Should fix both the original bug and the regression from attempt 2,
     but has not been re-tested in a browser yet.
  - **The agent cannot verify any of this itself** — this sandbox has no
    working headless Chromium (Playwright's own Chromium build is missing
    `libasound2` system library with no sudo available to install it; the
    system's snap-packaged Chromium is blocked by the container's
    mount-namespace restrictions). All browser verification for this work
    has been, and must continue to be, done by the user directly, reporting
    back what they see (ideally with a screenshot).
  - `requirements.txt` gained `boto3` (ECR) and `PyYAML` (YAML Generator, a
    deliberate exception to this codebase's usual dict→`json.dumps()`
    anti-PyYAML convention — see the code comment in
    `app/services/yaml_generator/render.py` for why) — both already
    `pip install`ed into the local `.venv`, but re-run `pip install -r
    requirements.txt` in any other environment.
  - The `yaml_generator.view` permission and a "YAML Generator" `Menu` row
    (nested under the existing "Deployment" parent) have already been
    applied directly to the dev DB (permission via re-running
    `seeds/seed_admin.py`, which is safe/idempotent; the menu row via a
    direct `Menu(...)` insert, **not** `seeds/seed_menu.py` — see the
    warning below). `seeds/seed_menu.py` itself was also edited to add the
    equivalent `get_or_create(...)` call for future fresh installs, but
    that edit has not been (and must not be) run against this dev DB.
- **Migration head is `01350ebce389`** (add `heartbeat_at` to
  `image_builds`/`deployment_executions`, on top of `1268e9419f60`'s
  workflow models) — already applied to the dev DB via `flask db upgrade`.
- **⚠️ Do not run `python seeds/seed_menu.py` against the dev DB** until its
  `get_or_create()` matching (currently exact `label` + `parent_id`) is
  fixed to tolerate renames, e.g. matching by `url` instead — this DB's
  real menu labels have drifted from what the script expects (real
  "Servers"/"Yaml/Manifests"/"Runs" vs. the script's "Deployment
  Servers"/"Deployment Manifests"/"Deployment Runs"), so re-running it
  silently creates duplicate sidebar rows. Caught and cleaned up twice
  already (see Part 7 in `AI_CONTEXT.md`) — do not run it again without
  fixing the matching logic first. `seeds/seed_admin.py` doesn't have this
  problem (it matches by permission `code`, which hasn't drifted) and is
  safe to re-run as usual. If a new feature needs exactly one new sidebar
  entry, insert that single `Menu` row directly (matching by its **parent's**
  label, which hasn't drifted, the way the YAML Generator row above was
  added) rather than running the whole script.
- Run tests via:
  ```bash
  source .venv/bin/activate && set -a && source .env && set +a
  FLASK_ENV=testing python -m pytest tests/ -q
  ```
  (`.env`'s `DATABASE_URL`/`TEST_DATABASE_URL` already point at the WSL2
  gateway IP `172.29.16.1` directly, not the `db` Docker Compose hostname —
  no `sed` swap needed from inside the sandbox.)
- CSS changes need a rebuild to actually show up: `npm run build:css`
  (already run as of the YAML Generator page's new templates — new
  daisyUI `-xs` size-variant classes weren't previously compiled).
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
    rendered `id="..."` first rather than assuming the field name. An
    AJAX-only endpoint not backed by a rendered form at all (e.g.
    `/yaml-generator/generate/<kind>`) instead reads the CSRF token from a
    `data-csrf` attribute on a hidden per-page `<span>` and sends it as the
    `X-CSRFToken` header — Flask-WTF's global `CSRFProtect` checks that
    header automatically, and the form itself is instantiated with
    `meta={"csrf": False}` so its own embedded `csrf_token` field
    (which the AJAX body never includes) doesn't also get checked and fail.
  - The "docker" build engine runs bare-metal on the host (shells out to the
    host's own `docker` CLI over the mounted socket); "kaniko" runs
    containerized/daemonless. `buildx` needed on the host for local
    "docker"-engine builds regardless of the Dockerfile's own copy.
  - `REPO_CLONE_ROOT` (`<app>/data/repos`) **now has a persistent named
    Docker volume** (`repo_clones`, `docker-compose.yml`) — registered
    repos' local clones survive container restarts as of the "Persist repo
    clones..." commit. (Previously did not; if working from a checkout
    older than `81c1895`, this volume won't exist yet.)
  - The app now runs **four** independent background poll threads (build
    worker, deploy worker, deploy live-status poller, and the workflow
    orchestrator), each with its own DB-queue and its own concurrency
    story — none of them execute each other's work; the workflow
    orchestrator only ever enqueues into the build/deploy workers' existing
    queues and watches for terminal status, see AI_CONTEXT.md Part 9. Both
    the build and deploy workers additionally now run a **heartbeat**
    thread each (see `cac7252`): every claimed job's `heartbeat_at` is
    ticked every 15s while it runs, and a stale/missing heartbeat (>60s) on
    the single `status='running'` row is auto-reaped as a failure — this
    closes what used to be a real gap where a process crash mid-build/
    mid-deploy would leave that row wedged forever, silently blocking the
    entire pipeline until someone fixed it by hand.
