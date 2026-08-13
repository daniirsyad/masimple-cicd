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
   fixes, the six new providers, the YAML editor fix, the YAML Generator
   page, and the "auto-fill build metadata from git commits" feature)
   postdates it and has no narrative write-up there yet.

Then ask me what to work on next rather than assuming.

## Current state

- **733 tests passing** (as of the last full run).
- **Everything is committed and pushed to `origin/main`**, most recently as
  three batches of work:
  1. **This same commit** — a large batch of previously-uncommitted work
     spanning several independent features:
     - **Dockerfile management** (`/dockerfiles`, `dockerfile.manage`): a
       new `Dockerfile` model (name + raw content) a Builder can build from
       instead of a path inside its own repo. `Builder.dockerfile_source`
       (`"repo"`/`"managed"`) + `managed_dockerfile_id` pick which; the
       worker writes the managed content out to a fixed filename inside the
       repo clone (`.masimple_cicd_managed.Dockerfile`, rewritten fresh
       before every build, never committed to git) right before handing the
       build context to the build engine. Can't delete a Dockerfile still
       referenced by any Builder.
     - **Version-to-Version linking** (`/versions`' new "Linked Versions"
       checkbox picker): a **one-directional** many-to-many self-link on
       `Version` (`version_version_links` table — distinct from the
       existing batch-to-batch `VersionLink` table) controlling which
       *other* Versions' batches a Version's own Documentation "Linked
       Batches" picker offers, beyond same-Version batches (always
       offered). Linking A → B doesn't grant the reverse; that only exists
       if B's own picker is separately edited to include A.
     - **Commit log limit** (`SystemConfig.commit_log_limit`, editable on
       `/config`, default 20): caps how many commits
       `GitProvider.get_commits()`/`get_commit_messages()` reads when
       there's no prior build to diff against (first build on a branch, or
       a force-push/rebase made the prior commit unreachable) — threaded
       through as a `limit` param on both `GitProvider` methods, read fresh
       on every call, no restart needed.
     - **Home page + sidebar redesign**: dashboard stat cards each get a
       small Feather-style icon; the sidebar gained a logo/header, a
       scrollable nav area, a left-border active-item highlight, and a
       reordered menu tree — Workflows moved up near the top as the
       cross-module orchestration layer; Image Builder's children reordered
       into a logical setup-to-usage flow (GitHub → Registries → Versions →
       Dockerfiles → Builders → Images → Documentation → AI Settings);
       Dockerfiles/YAML Generator/Workflows top-level entries gained icons.
     - **Bug fix**: the Workflow detail page's Run/Delete Workflow/Delete
       Step `<form>`s were missing a `csrf_token` hidden input entirely —
       never caught by the test suite since `TestingConfig` disables CSRF,
       would have 400'd in production where it's enabled. Fixed.
     - **AI-assist description simplification**: generating an AI draft
       (Documentation page) now writes straight into the real Description
       field instead of showing a separate "AI Draft" preview box with its
       own "Use this draft" button — still fully editable there before
       saving, just one fewer click/element (`app/static/js/ai-generate.js`).
     - **Filter-card UI consistency**: `/images`, `/logs`, `/logs/errors`,
       `/users`, `/versions`, `/deployment-runs` all restructured to the
       same card + collapsible-filters pattern the Documentation page
       established.
     - **Documentation page's Object filter — rebuilt from scratch three
       times over the course of this work**, worth knowing the discarded
       paths in case this resurfaces: a native `<select multiple size="4">`
       broke the filter bar's row alignment → a hand-rolled pills widget
       grew too wide with several selections picked → **Choices.js was
       tried and fully reverted** (uninstalled, vendor files deleted, CSS
       reverted) after explicit user feedback rejecting the
       third-party-library approach entirely → **Select2 was then tried**
       (vendored jQuery + Select2 locally, fully restyled with daisyUI
       tokens) and **also fully reverted** per a later explicit "back to
       previous (not using select2)" request. What's actually in place now
       is the hand-rolled search+pills widget, given its own full-width row
       inside a daisyUI `collapse` — the structural fix that actually
       solved the width problem, independent of widget technology. Two real
       interaction bugs surfaced and were fixed along the way, worth
       remembering if a similar widget is ever added elsewhere:
       - The dropdown was clipped by the `.collapse`'s own `overflow:
         hidden` (needed for its own open/close grid-animation) — fixed by
         portaling the dropdown to `document.body` with `position: fixed`,
         positioned from `getBoundingClientRect()`, since `overflow: hidden`
         clips *any* descendant regardless of its `position` — only moving
         the element out of that ancestor's DOM subtree actually escapes it.
       - Picking a second item required clicking out and back in first —
         caused by the outside-click listener reading `event.target`
         *after* the click handler had already replaced that node via
         `innerHTML` (so a later `.contains()` check against the
         now-detached old node always failed); fixed with
         `event.composedPath()`, which is captured at dispatch time before
         any DOM mutation.
       Also added Up/Down-arrow-to-highlight + Enter-to-select keyboard
       navigation, and applied both fixes (dropdown-clipping escape +
       composedPath-safe outside-click, plus the arrow-key nav) to the
       **Builders "Build Selected" modal's** own Object(s) picker
       (`setupMultiObjectPicker` in `builders.js`) for parity — there the
       clipping culprit is the modal's `.modal-box` (`overflow-y: auto`)
       plus the `<dialog>` itself (`overflow-y: hidden`), and the portal
       target is the `<dialog>` element itself rather than `document.body`
       (a `document.body` portal would render *behind* an open `<dialog>`,
       since a shown dialog is promoted to the browser's top layer).
     - **⚠️ None of the above has been verified in a live browser** — same
       sandbox constraint as everything else in this file (no working
       headless Chromium here), only exercised via the Flask test client
       and, for the JS-only interaction fixes, manual reasoning about event
       ordering/CSS containment. Worth an especially careful manual pass on
       `/documentation` and the Builders build-trigger modal specifically,
       given how many rounds of interaction bugs those went through.
     - New migrations (all already applied to the dev DB via
       `flask db upgrade`): `039bae7c2bbf` (Dockerfile model + Builder
       fields), `4bec0353b1f9` (Version-to-Version linking), `2a347cde1361`
       (commit log limit) — chain is now `8467dc0ac6b0` → `039bae7c2bbf` →
       `4bec0353b1f9` → `2a347cde1361` (head).
     - The `dockerfile.manage` permission and a "Dockerfiles" `Menu` row are
       already applied to the dev DB and confirmed clean (no duplicate
       labels) as of this update.
  2. **`6e8eede`** — a YAML-editor cursor-position bug fix (shared
     `app/static/js/yaml_editor.js`, replacing two independently duplicated
     CodeMirror-init blocks in `deployment_manifests.js`/
     `deployment_servers.js`) plus a new `/yaml-generator` page/blueprint
     (`app/blueprints/yaml_generator/`, `app/services/yaml_generator/`) that
     builds Deployment/Service/ConfigMap/Secret/Ingress YAML from form
     fields with a live preview, Copy/Download, and a "Save as Manifest"
     hand-off into the existing Deployment Manifest creation flow.
     `requirements.txt` gained `boto3` (ECR) and `PyYAML` (YAML Generator, a
     deliberate exception to this codebase's usual dict→`json.dumps()`
     anti-PyYAML convention — see the code comment in
     `app/services/yaml_generator/render.py` for why). The `yaml_generator.view`
     permission and a "YAML Generator" `Menu` row are already applied to the
     dev DB (permission via `seeds/seed_admin.py`, safe/idempotent; the menu
     row via a direct `Menu(...)` insert, **not** `seeds/seed_menu.py` — see
     the warning below).
     - **⚠️ The cursor-bug fix itself is still not confirmed working in a
       live browser.** It went through two prior live round-trips with the
       user before this final version was applied (see git history /
       `AI_CONTEXT.md` Part 9 if this resurfaces): attempt 1 (disable
       `lineWrapping` + deferred `cm.refresh()`) didn't fix it; attempt 2
       (a `ResizeObserver`-triggered refresh + scroll nudge + **resize
       nudge**) briefly broke the editor entirely via a feedback loop; the
       version now committed removed the resize nudge, keeping only
       `cm.refresh()` + a scroll-position nudge on the same
       `ResizeObserver` trigger. Confirm with the user before treating this
       as fixed.
  3. **`41f0777`** — build-trigger inputs (Bump Type, Object(s), Change
     Type, Additional Description) can now be auto-filled from git commit
     history instead of typed in from scratch every time:
     - `ImageBuild.commit_sha` + a new `ImageBuildCommit` table record, per
       build, the commit it ran against and every commit since that
       (builder, branch) pair's last successful build
       (`worker._record_commit_history` — best-effort, a git-log failure
       never fails the build itself).
     - `Object` is now a first-class extensible lookup table (like
       `ChangeType`/`VersionType`), many-to-many with both `BuildBatch` and
       `VersionDocumentation`, replacing the old single free-text `object`
       string column on both (migration `8467dc0ac6b0` backfilled the 6
       pre-existing free-text values into real rows). A batch/documentation
       entry can reference several Objects now — picked via a
       search-and-add-new multi-picker (pills) on both the Builder trigger
       modal and the Documentation page, backed by
       `Object.resolve(object_ids, new_names)` (get-or-create, shared by
       every call site — reuse this rather than writing another
       get-or-create for Object).
     - `app/services/build/prefill.py`'s `compute_build_prefill()` is the
       one shared service behind every surface below: syncs the relevant
       repo(s), reads commits since last build, guesses Bump Type via
       deterministic Conventional Commits parsing
       (`app/services/build/bump_heuristic.py`), and asks the configured AI
       provider for an Object/Change Type/Description draft
       (`app/services/ai/build_prefill.py` — its own small structured-JSON
       prompt, not the `PromptTemplate` singleton, which is scoped to the
       single free-text `ai_description` field). Reuse `compute_build_prefill()`
       for any future "auto-fill from git" surface rather than
       reimplementing this.
       - **Image Builder** (`/builders`): explicit "Preview from Git"
         button in the trigger modal (`POST /builders/build/preview`).
       - **Workflow** (`/workflows/<id>`): the Add Build Step modal's
         group/builder checkboxes auto-fire the same prefill instead
         (debounced, no button — `POST /workflows/<id>/steps/build/preview`),
         since the target selection happens inside that modal rather than
         beforehand. **Deliberate, not-yet-resolved scope decision**:
         `WorkflowStep.bump_type`/`object`/`change_type_id`/
         `additional_description` are still captured once at authoring
         time and replayed unchanged on every run — the same
         already-documented behavior as before this feature, not changed
         by it. They are **not** re-resolved against fresh commits each
         time the workflow actually runs, so a workflow that runs
         repeatedly keeps reusing whatever the prefill produced when the
         step was first added. Moving this to true run-time resolution
         (the orchestrator calling `compute_build_prefill()` itself right
         before enqueueing, instead of `WorkflowStep`'s frozen columns)
         would be a real behavior change to a previously-documented design
         decision — flagged for the user, not decided unilaterally. Revisit
         if this comes up again.
     - **Documentation page** (`/documentation/<batch_id>`): each image's
       row in "Images in this Batch" now shows a `from → to` short-SHA
       commit range (from = the previous successful build's commit for
       that builder/branch *as of when this build ran*, not "current
       latest") with a commit-count badge, opening a modal with the full
       per-commit SHA/author/message/date list.
     - Also fixes a bug where reopening the Image Builder trigger modal for
       a *different* builder/group kept showing stale Bump
       Type/Object/Change Type/Description/preview-status left over from
       whatever was last previewed — `openBuildModal()` now resets the form
       on every open.
     - **⚠️ None of this new UI has been verified in a live browser** — same
       sandbox constraint as the YAML editor fix above, only exercised via
       the Flask test client so far.
     - New migrations: `526c1cd02a77` (commit tracking), `8467dc0ac6b0`
       (Object lookup table + m2m) — both already applied to the dev DB.
- **Migration head is `2a347cde1361`** — already applied to the dev DB via
  `flask db upgrade` (confirmed via `flask db current`).
- **`seeds/seed_menu.py`'s label-drift risk is dormant right now, not
  fixed** — the dev DB's menu tree was confirmed clean (26 rows, zero
  duplicate labels) as of this update, so its current content matches the
  script's `get_or_create()` exact-`label`+`parent_id` matching. The
  underlying fragility is unchanged though: `get_or_create()` still matches
  by exact `label` (not e.g. `url`, which wouldn't drift on a rename), so if
  the dev DB's menu labels ever get hand-edited again (as they did
  before — see Part 7 in `AI_CONTEXT.md` and the `seed_menu_label_mismatch`
  memory, which is now stale and should be treated as historical, not
  current), re-running the full script would silently create duplicates
  again. `seeds/seed_admin.py` doesn't have this problem (it matches by
  permission `code`, which hasn't drifted) and is safe to re-run as usual.
  If a new feature needs exactly one new sidebar entry and there's any doubt
  about drift, insert that single `Menu` row directly (matching by its **parent's**
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
    older than `81c1895`, this volume won't exist yet.) **Important:** this
    is a Docker-*managed* named volume (`repo_clones:/app/data/repos` in
    `docker-compose.yml`), **not** a bind mount to the project's own
    `./data/repos` folder on the host — editing/adding a file under the
    project checkout's `data/repos` does nothing; the container's actual
    clone lives in Docker's own volume storage. Reach it via
    `docker compose exec web ls /app/data/repos`, not the host filesystem.
    Also: `sync_repo()` re-`fetch`/`checkout`/`pull`s from the git remote
    right before every build, so even a file dropped directly into the real
    clone (bypassing git) is just untracked cruft the next sync won't
    remove but the Dockerfile's build context was never meant to rely on —
    commit and push to the Builder's configured branch instead.
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
