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

- **991 tests passing** (as of the last full run).
- **This session's work** (on top of everything below) — on the Deployment
  Manifests index page, each manifest group's card now starts **collapsed**
  instead of always showing its full manifest table. Requested directly, not
  via plan-mode. `app/templates/deployment_manifests/index.html` — the group
  name, manifest-count badge, and action buttons (Deploy/Update/Restart/Stop
  Group) stay outside the collapse and always visible/clickable, so acting on
  a whole group never requires expanding it first; only the drag-to-reorder
  hint text and the manifest table itself sit inside a checkbox-driven daisyUI
  `collapse collapse-arrow` (no `checked` attribute, so it starts closed) —
  the same no-JS pattern already used for Documentation's Filters section.
  The "Ungrouped" table (not a real group) was left as-is. No JS/backend/
  migration changes were needed; SortableJS's drag-reorder still initializes
  fine against the collapsed table since daisyUI hides collapse content via a
  zero-height grid row, not `display:none`. Existing
  `tests/test_deployment_manifests.py` (51 tests) pass unchanged, since this
  is a pure template/CSS change with no route or data behavior to cover.
- **A prior session's work** (on top of everything below) — pressing Build,
  Deploy, Update, Stop, or Restart (single or "Whole group") on the
  Builders / Deployment Manifests index pages no longer navigates away to
  the Images / Deployment Runs list on success — it stays on the same
  index page, with the success flash message itself now containing a
  "View in Images" / "View in Deployment Runs" link
  (`markupsafe.Markup`-wrapped, since Jinja autoescapes plain `flash()`
  strings by default) instead. Requested directly, not via plan-mode.
  `app/blueprints/builders/routes.py`'s `build()` now redirects to
  `builders.index` (was `images.list_images`); `app/blueprints/
  deployment_manifests/routes.py`'s shared `_trigger_deploy_action()`/
  `_trigger_teardown_style_action()` (covering `deploy()`/`update()`/
  `stop()`/`restart()` alike, single-manifest or group) now redirect to
  `deployment_manifests.index` (was `deployment_runs.index`). The
  Deployment Pods `restart_workload()` action already redirected back to
  its own originating page, so it needed no change. Error-path redirects
  (validation failures, "nothing to do") were already same-page and are
  unchanged. Six existing `tests/test_deployment_manifests.py` assertions
  on the old `/deployment-runs/` redirect path were updated to
  `/deployment-manifests/` accordingly; no new tests added since this is a
  redirect-target/flash-content change to already-covered routes, not new
  behavior. No migration needed.
- **An earlier session's work** (on top of everything below) — the Telegram bot
  integration (previously Workflow-only: `/run`/`/status`/`/review`) can now
  also trigger manual, non-Workflow Builds and Deployment Manifest
  actions — requested directly, not via plan-mode. No migration needed.
  1. **`/build`** (`app/services/telegram/worker.py`) — a linked user
     holding `builder.build` picks an active, accessible Builder with a
     `default_branch` set; the bot then runs
     `services.build.prefill.compute_build_prefill()`, the exact same
     "Preview from Git" engine the web trigger modal's AJAX preview uses,
     and only offers a one-tap "✅ Confirm build" when Bump Type, at least
     one Object (matched or new), and Change Type all resolved confidently
     — otherwise it explains what's missing and points back to the web UI,
     the same escape hatch `/review` already uses for build-metadata
     approval (Telegram has no form to fill in a missing field). Confirming
     recomputes the prefill fresh rather than trusting the earlier
     preview's values (callback_data can't carry object names/description —
     Telegram's 64-byte limit), then calls the same
     `build/worker.enqueue_build_batch()` the web `builders.build()` route
     calls, resolving objects via `Object.resolve()` the same way.
  2. **`/deploy`, `/update`, `/stop`, `/restart`** — a linked user holding
     the matching `deployment.deploy`/`.update`/`.stop`/`.restart`
     permission picks a manifest to act on. Table-driven
     (`DEPLOY_ACTIONS`) single handler
     (`_handle_manifest_action_callback`) re-implements the same checks
     `deployment_manifests.routes._trigger_deploy_action`/
     `_trigger_teardown_style_action` make server-side: `deploy`/`update`
     refuse a disabled (`is_active=False`) manifest, `stop`/`restart`
     deliberately don't (an archived-but-still-live manifest must stay
     stoppable — same reasoning as the web routes); all four re-check
     manifest + every target server's accessibility at callback time, not
     just at list-build time (a stale button — access revoked, or the
     manifest disabled/deleted, in between — must still be caught here).
     `/stop`/`/restart` only list manifests currently deployed on at least
     one target server (`is_currently_deployed`), so a tap is never a
     guaranteed "nothing currently deployed" dead end. All four funnel into
     the same `deployment/worker.enqueue_deployment_run()` the web routes
     call.
  3. **A manifest belonging to a `group_name` also gets a "Whole group: X"
     button** alongside its individual one, requested directly as part of
     this session's scope (confirmed via AskUserQuestion up front, along
     with the AI-prefill-approve-as-is approach for Build above) — tapping
     it deploys/updates/stops/restarts every member manifest together, in
     the same ascending-for-deploy/descending-for-teardown `manifest.order`
     sequence the web UI's own group actions use. Telegram's `callback_data`
     is capped at 64 bytes and can't embed every member's UUID, so the
     button instead encodes a short SHA-1 hash of the group name
     (`_group_hash`); the callback resolves it back to the real manifest set
     via `_manifests_in_group`, scanning distinct group names for a hash
     match (collision risk negligible at this app's real scale — a handful
     of groups, not thousands). A single-member group doesn't get a
     redundant group button (`_groups_among` only returns groups with more
     than one eligible member).
  4. **`/build` got the same "Whole group: X" treatment as a same-session
     follow-up request** — a `Builder.group_name` group also gets a group
     button (`_single_version_groups_among`/`_builder_keyboard`), reusing
     `_group_hash`/the same `_groups_among` helper (works unchanged against
     Builder too, since it only reads `.group_name`). One extra constraint
     Build's group has that Deploy's doesn't: `builders.routes.build()`
     hard-rejects a build spanning more than one Version, so a group button
     is only offered when every eligible member already shares one
     (checked again at callback time via `_resolve_group_build_target`, in
     case membership drifted between the list being shown and the tap) —
     otherwise it would be a guaranteed-fail tap. The group flow mirrors the
     single-builder one exactly: `gbuild:<hash>` runs
     `compute_build_prefill()` across every member's commits and only shows
     `gbconfirm`/`gbcancel` when confident; `gbconfirm` recomputes fresh and
     calls `enqueue_build_batch()` once with all members'
     `(builder, branch)` pairs, producing one `BuildBatch` covering the
     whole group (same as the web UI's own "Build Group" button).
  5. **No new notification wiring was needed** — `notify_build_started/
     finished` and `notify_deploy_started/finished`
     (`app/services/telegram/helpers.py`) already fire on every manually
     (non-Workflow-)triggered build/deploy regardless of whether it was
     triggered via Telegram or the web, as long as `requested_by`/
     `triggered_by` is set; Telegram-triggered ones just needed to actually
     set that field, same as the web routes already did.
  New `BOT_COMMANDS`/`HELP_TEXT` entries for all five commands. 41 tests
  now in `tests/test_telegram_worker.py` (29 new): permission gating,
  active/accessible/has-target-servers listing filters, single- and
  whole-group action success + activity-log attribution, the
  disabled-manifest and "nothing currently deployed" edge cases for
  deploy/stop, the AI-prefill confident-vs-not-confident branches (plus
  Confirm/Cancel) for Build, and the mixed-Version group-build rejection.
- **A session before that's work** (on top of everything below) — two independent bug
  fixes in the Deployment/Workflow pipeline, found via direct user reports
  rather than the test suite. No migration needed for either. Two commits,
  `45464ac` and `95d3b93`.
  1. **A Deployment Manifest "Restart" now re-resolves the manifest fresh
     before tearing down and reapplying, instead of blindly replaying
     whatever YAML was last deployed** (`app/services/deployment/worker.py`
     `_run_deployment`) — reported as "restarting to refresh a Secret
     doesn't update it, only Stop+Deploy does." Root cause:
     `run_action == "restart"` reused `execution.source_execution.
     rendered_yaml` (the previous deploy's cached output) verbatim, on the
     theory that "a restart doesn't change what's running, so there's
     nothing to re-resolve" — true for the image/version, false for a
     Secret's own literal YAML content or any other manifest edit made
     since that last deploy. Restart now calls `resolve_manifest(manifest)`
     the same way `deploy` does, then delete+applies the fresh result — so
     it also picks up a newer image if a `{{SYS:VERSION}}` placeholder now
     resolves to "latest," a real behavior change from "recycle exactly
     what's running" to "Stop+Deploy via one click," called out in the
     Restart confirmation modal's copy
     (`app/templates/deployment_manifests/index.html`). New regression
     test `test_restart_reresolves_instead_of_replaying_stale_content`
     (`tests/test_deployment_worker.py`) edits a manifest's `yaml_content`
     between deploy and restart and asserts the new content reaches both
     the stored execution and what's actually applied.
  2. **Fixed the Workflow orchestrator starting the same step 2–3 times on
     one "Run" click** — reported with a screenshot showing three duplicate
     "running" Build rows all as step #1 for a single trigger. Root cause:
     `entrypoint.sh` runs gunicorn with `--workers 3`; each worker
     *process* starts its own `workflow-orchestrator` background thread
     (`app/services/workflow/worker.py` `start_worker`), and its
     `_worker_started` guard is a process-local global — it only stops a
     second thread in the *same* process, not the other two processes' own
     threads (identical root cause, different symptom, to the Telegram
     `getUpdates` 409 bug fixed in an earlier session — see below). Since
     `_tick()`'s `WorkflowRun.query.filter_by(status="queued")` had no row
     locking, all three processes' threads could see the same queued run
     within the same ~2-second poll window and each call `_start_step()`
     on it, each enqueueing its own `BuildBatch`. Fixed by adding
     `.with_for_update(skip_locked=True)` to both of `_tick()`'s queries
     (queued and running runs) — the exact `SELECT ... FOR UPDATE SKIP
     LOCKED` claiming pattern the build/deploy workers already use safely,
     just never applied here. New test
     `TestTickSkipsLockedRuns::test_skips_a_run_locked_by_another_
     connection` (`tests/test_workflow_worker.py`) holds the target row's
     lock open on a second, independent DB connection (a real thread race
     turned out to be too timing-dependent to assert on reliably — both
     racing threads' plain SELECTs can easily complete before either
     commits on a fast local test DB, whether or not the fix is present)
     and asserts a concurrent `_tick()` skips it while locked, then picks
     it up cleanly once released; run on a bounded background-thread join
     so a missing fix fails fast with a clear message instead of hanging
     the suite (confirmed: without the fix, the racing `_tick()`'s own
     `UPDATE` blocks on the held lock rather than skipping past it).
     **Not yet redeployed** — the reporting user's own instance (a
     different machine this session has no access to) still has the three
     duplicate rows from before the fix; they're inert leftovers (no
     corruption, just wasted redundant builds), and that one run will keep
     showing 3x "Build" until this fix is actually deployed there.
- **Two sessions before that's work** (on top of everything below) — the "kaniko" build
  engine now actually works, for a self-hosted deploy onto a real
  Kubernetes + CRI-O cluster (TEBET-APP-3) with no Docker-compatible socket
  to mount at all. No migration needed. Two commits, `928fa53` and
  `ec85143`.
  1. **`KanikoBuildEngine` rewritten to launch kaniko-executor as its own
     Kubernetes Job, not a bare subprocess** (`app/services/build/engine.py`)
     — an earlier session had disabled "kaniko" as a selectable Build
     Engine after a subprocess-run kaniko build corrupted the live app
     container's own filesystem (no daemon/chroot of its own). Re-enabled
     in `factory.py`/`system_config/forms.py` now that it's isolated in its
     own pod instead. The Job shares the app's own hostPath-backed volume
     (new `KANIKO_WORKSPACE_HOST_PATH` env var, must match the app
     Deployment's own hostPath) so it sees the exact repo clone `context_dir`
     already points at, and is pinned to the app pod's current node (new
     `NODE_NAME` Downward-API env var) since hostPath is node-local.
     Registry creds go in via a per-build Secret mounted at kaniko's
     expected config path, deleted (with the Job) after each build.
     `k8s/deployment.yaml` gained a `ServiceAccount`/`Role`/`RoleBinding`
     (Jobs/Pods+logs/Secrets, in-namespace) and wires `serviceAccountName`/
     `NODE_NAME`/`KANIKO_WORKSPACE_HOST_PATH` into the Deployment.
     `Dockerfile` no longer embeds the `kaniko-executor` binary (dead
     weight now — kaniko runs as its own pod using the official image, not
     a copy baked into this app's image). `tests/test_build_engine.py`
     rewritten for the Job-based flow; `test_build_factory.py`/
     `test_system_config.py` updated for "kaniko" being a normal choice
     again.
  2. **Fixed two bugs found by actually running a build against the real
     cluster** — `kubectl logs -f job/<name>` doesn't wait for a
     slow-starting pod by default; it failed immediately with `"container
     ... is waiting to start: ContainerCreating"` instead of retrying,
     confirmed via the user's own terminal output. Now passes
     `--pod-running-timeout=300s` explicitly. Separately, the fallback
     `KANIKO_JOB_STATUS_TIMEOUT_SECONDS` (waited for the Job to report
     succeeded/failed once log streaming ends) was left at 60s — nowhere
     near enough for a real image build — raised to 3600s, matching
     `DockerBuildEngine`'s own lack of any build-duration timeout.
  3. **Two DeploymentManifest DB rows edited/created directly (not via
     git)**, since this app deploys itself and the changes above needed a
     live manifest to actually take effect: the existing `MASIMPLE-CICD`
     manifest (deploys to TEBET-APP-3) gained `serviceAccountName:
     masimple-cicd-builder`, the `NODE_NAME` env var, and
     `KANIKO_WORKSPACE_HOST_PATH` (set to `/home/hamilton/masimple_cicd/data`,
     matching its existing hostPath). A new `MASIMPLE-CICD-RBAC` manifest
     was created (also targeting TEBET-APP-3) holding the ServiceAccount/
     Role/RoleBinding from `k8s/deployment.yaml` — **created but not yet
     deployed** (TEBET-APP-3's client cert was already known-expired since
     2026-08-09, so deploying it was deliberately not attempted this
     session).
  - **Still open**: this app builds/deploys *itself*, so the fixes in (2)
    above only take effect once a new image containing them is actually
    built and deployed — but building that image through this same
    cluster's kaniko path would hit the very same (now-fixed-in-source,
    not-yet-fixed-in-the-running-pod) bug. A one-time bootstrap build/push
    from outside the current broken loop (e.g. `docker build`/`docker push`
    from a workstation with real Docker, or any other CI already
    available) is needed to break the cycle; after that, kaniko builds
    through this app — including future builds of itself — should work
    end-to-end. The RBAC manifest also still needs an actual Deploy once
    TEBET-APP-3's cert is sorted.
- **Three sessions before that's work** (on top of everything below) — commit messages
  now drive Bump Type/Object(s)/Change Type more directly, plus a way to
  actually try that out and understand it from `/ai-settings`. No
  migration needed for any of it.
  1. **Object(s)/Change Type are now read straight out of the commit
     messages first, only falling back to the AI's own guess when nothing
     matched directly** — Bump Type already worked this way (Conventional
     Commits regex, `bump_heuristic.suggest_bump_type`, never AI); this
     extends the same priority to the two AI-assisted fields. New
     `_match_existing_from_commits()`/`_name_appears_in_text()`
     (`app/services/ai/build_prefill.py`) do a whole-word, case-insensitive
     search for each *existing* Object/Change Type name literally spelled
     out anywhere in the commit messages — found matches are used as-is,
     no AI call needed for that field; `suggest_metadata()` still always
     calls the AI too (still the only way to get `description`, and the
     only way to discover a genuinely *new* Object name), but a direct
     match now wins over whatever the AI guessed for the same field.
     Bonus: since direct matches don't depend on the AI succeeding at all,
     they now survive even when no AI provider is configured or the call
     fails — previously a broken AI setup meant every field came back
     blank; now only the AI-only parts (`description`, brand-new Object
     suggestions) do.
  2. **`major:`/`minor:`/`patch:` are now recognized as an explicit Bump
     Type prefix, taking priority over everything else** — requested after
     confirming (by testing directly) that writing e.g. `Major: ...` was
     previously silently misclassified as `patch`, since
     `bump_heuristic.classify_commit()` only ever mapped `feat`/`feature`
     to minor and a `!`/`BREAKING CHANGE` marker to major; the word
     "major" itself wasn't recognized at all. `classify_commit()` now
     checks for a literal `major`/`minor`/`patch` type-word prefix first,
     before the `!`/`BREAKING CHANGE`/`feat` checks — so an explicit word
     wins even over a conflicting marker on the same line (e.g.
     `minor!: ...` resolves to `minor`, not `major`), on the reasoning
     that the developer explicitly stating their intent is the clearest
     possible signal.
  3. **A "How does the 'Preview from Git' autofill work?" note, with
     worked examples, on `/ai-settings`** — a plain-language explanation of
     the above (Bump Type is heuristic-only/never AI; Object(s)/Change
     Type check the commit message directly before ever asking the AI;
     Description is always AI-drafted) plus side-by-side commit-message
     examples showing a well-labeled message resolving three of four
     fields without any AI guessing versus a vague one leaving more for
     the AI to work out. Uses the same collapse-arrow (checkbox-driven, no
     JS) pattern as Documentation's Filters section.
  4. **A "Test a commit message" tool, also on `/ai-settings`, in its own
     collapsible** — type one or more commit messages (one per line) and
     see exactly what Bump Type/Object(s)/Change Type/Description would
     come out, using the *real* `suggest_bump_type`/`suggest_metadata`
     functions, not a simulation — without needing a real git commit or
     build trigger to find out. New `TestCommitMessageForm`
     (`app/blueprints/ai_settings/forms.py`) and `test_commit_message`
     route (same `aiprovider.manage` permission gate as the rest of the
     page); nothing is persisted, the page just re-renders directly with
     the result. Two bugs caught and fixed while building this, both
     before commit: an initial `test_form.is_submitted()` check for "was
     anything submitted" only looks at the HTTP method, not which form was
     actually posted, so it would have spuriously shown the tester's
     status message on an unrelated failed submission elsewhere on the
     same page (e.g. an invalid AI Provider create) — replaced with an
     explicit flag from the route. That flag then turned out to be
     unreachable anyway: WTForms' `DataRequired` already strips-and-rejects
     an all-whitespace textarea submission before the route's own code
     ever runs, so the dead branch was removed and the field's own
     standard validation-error display handles that case instead.
  Also considered and fully reverted before landing on the above (no trace
  left — never committed): a first attempt at "make the AI prompt
  configurable" via a new `BuildMetadataPromptConfig` singleton model +
  migration + `/ai-settings` form for free-text "extra guidance" per
  field. Abandoned once the user redirected priorities toward "read it
  from the commit message first" instead — the migration was downgraded
  and dropped from the dev DB, and every file touched for it was restored/
  removed, so there's nothing to be confused by if this comes up again.
  New/changed tests: `tests/test_build_prefill.py` (+5, direct-match
  priority and its AI-failure resilience), `tests/test_bump_heuristic.py`
  (+6, explicit word recognition and its priority over conflicting
  markers), `tests/test_ai_settings.py` (+6, the tester route including a
  regression test for the `is_submitted()` bug above).
- **Four sessions before that's work** (on top of everything below) — a Telegram bot
  integration, built in three parts in sequence (the first two planned via
  plan-mode with the user before implementation; the third — build/deploy
  notifications — was a small enough follow-up request to just implement
  directly). Migration `041a67231254`.
  1. **Workflows can now be run and checked from Telegram, not just
     notified about.** `app/services/telegram/worker.py` (new) is a fifth
     independent background poll thread — long-polls the Bot API's
     `getUpdates` (no public HTTPS/webhook needed, unlike a push-based
     alternative; works for the Podman trial deploy and bare-metal dev
     alike, since neither is reachable from the public internet) and
     registers a native "/" command menu inside the Telegram chat via
     `setMyCommands` (`/start`, `/run`, `/status`, `/review` — the last one
     is part 2 below). `/run` shows an inline-keyboard pick of active
     Workflows the sending chat's linked `User.telegram_chat_id` can see
     (`Workflow.is_accessible_to()`) and holds `workflow.run` for; tapping
     one calls the existing `enqueue_workflow_run()`, same as the web UI's
     Run button. `/status` lists that user's own last 5 runs; tapping one
     replies with its per-step status (same fields the existing
     `/workflows/runs/<id>/status` JSON route already exposes). New
     `SystemConfig.telegram_bot_commands_enabled` (a **separate** toggle
     from the pre-existing `telegram_notifications_enabled`, reusing the
     same bot token) gates all inbound command handling — a new checkbox on
     `/config`'s Telegram Integration section. `SystemConfig.
     telegram_last_update_id` persists the `getUpdates` offset across
     process restarts, so a redeploy doesn't replay (and re-run) old
     commands. `app/utils/logger.py`'s `log_activity()` gained optional
     `user`/`ip_address` kwargs, since this worker thread has an app
     context but no Flask *request* context to read
     `current_user`/`request.remote_addr` from (mirrors how
     `app/utils/error_logger.py`'s `log_error()` already guards its own
     request-only reads with `has_request_context()`) — every existing
     call site is unaffected since both default to the old behavior. Also
     new: a Workflow run now pushes a Telegram notification to whoever
     triggered it the moment it reaches a terminal status
     (`notify_run_finished()`, hooked into `workflow/worker.py`'s shared
     `finish_step()`), regardless of whether it was triggered via Telegram
     or the web.
  2. **A build step paused for human review (`awaiting_review`, from the
     prior session's auto-generate-at-run-time feature) can now be
     approved or rejected from Telegram too**, not just the web review
     panel — asked as a direct follow-up once part 1 above landed. The
     step's triggering user gets a push notification the moment it pauses
     (`notify_awaiting_review()`), showing the AI-suggested Bump
     Type/Change Type/Object(s)/Description with inline ✅ Approve/❌
     Reject buttons attached; any `workflow.run` holder can also pull the
     same thing up on demand via a new `/review` command — a **shared**
     review queue across every workflow they can see, not just their own
     triggered runs (matching the web panel's own
     `_awaiting_review_step_run_or_404` authorization rule, deliberately
     wider than `/status`'s "your own runs only" scope). Telegram approves
     only the suggestion **as-is** — there's no dropdown inside a Telegram
     chat to edit a missing value the way the web form's fields allow, so
     a step whose Bump Type or Change Type wasn't confidently suggested
     just points back to the web UI instead of guessing. The approve/
     reject logic itself was extracted out of `workflows/routes.py`'s
     `approve_step_run`/`reject_step_run` views and into two new shared
     functions, `approve_awaiting_step()`/`reject_awaiting_step()`
     (`app/services/workflow/worker.py`) — the web route and the Telegram
     callback now both call the same implementation instead of
     duplicating the "resolve builders, enqueue the real BuildBatch" logic
     a second time.
  3. **Manual (non-Workflow) builds and deploys now also push a Telegram
     notification on start and finish**, mirroring the Workflow-run
     notification from part 1 — requested as a direct follow-up once parts
     1–2 landed. Hooked into `app/services/build/worker.py`'s
     `_claim_next_job()`/`_update_batch_status()` and
     `app/services/deployment/worker.py`'s `_claim_next_job()`/
     `_update_run_status()` — the same places those modules already
     compute a batch/run's aggregate status — guarded so a multi-image
     batch or multi-execution run only notifies once each way (start: the
     first image/execution actually claimed; finish: the first status
     recomputation that lands on a terminal value, tracked by comparing
     against the status just before recomputing). Deliberately **skipped**
     when the `BuildBatch`/`DeploymentRun` was actually enqueued by a
     Workflow step (checked via `WorkflowStepRun.batch_id`/
     `.deployment_run_id` — see `_is_workflow_driven_batch`/
     `_is_workflow_driven_run` in `app/services/telegram/helpers.py`) —
     that already gets its own Workflow-level notification from part 1, so
     this avoids double-notifying on every workflow build/deploy step.
  4. **Fixed a `RuntimeError: Telegram API error (409): Conflict: terminated
     by other getUpdates request` crashing the bot poll thread in
     production**, found via a real traceback after deploying parts 1–3
     above. Root cause: gunicorn runs **3 worker processes**; the
     `_worker_started` guard in `app/services/telegram/worker.py` is a
     Python module-level global, so it only stops a *second thread in the
     same process* from starting, not the other 2 gunicorn processes each
     starting their own `telegram-bot` thread — all 3 ended up long-polling
     Telegram's `getUpdates` with the same bot token at once, which
     Telegram rejects outright (unlike the build/deploy/workflow workers,
     which are fine with several processes concurrently polling their own
     DB queue via `SELECT ... FOR UPDATE SKIP LOCKED` — `getUpdates` has no
     such queue, it's a single-consumer API). Fixed with a Postgres
     session-level advisory lock: new `_become_poll_leader()` blocks each
     process's `telegram-bot` thread on `pg_advisory_lock` before it starts
     polling, so only one gunicorn worker process is ever the active leader
     at a time; the lock is taken on a connection `.detach()`ed from the
     SQLAlchemy pool (so it isn't recycled or counted against `pool_size`)
     and held open for the process's life. If the leader process dies,
     Postgres releases the lock automatically and one of the other
     processes' blocked threads takes over — no manual failover needed.
     Verified against the real dev Postgres instance that a second acquirer
     genuinely blocks until the first releases. **Not covered by the
     automated test suite** (all 36 `test_telegram*.py` tests exercise
     `_tick()` directly and still pass unchanged) — this is inherently a
     multi-process/real-Postgres concern the existing white-box tests don't
     spin up; worth re-confirming after this next reaches a real
     multi-worker deploy (Podman trial or otherwise) with
     `telegram_bot_commands_enabled` on.
  New test files: `tests/test_telegram_worker.py` (bot command/callback
  dispatch, white-box on `_tick()`, same pattern as
  `tests/test_workflow_worker.py`), `tests/test_build_deploy_notifications.py`
  (start/finish hooks + the workflow-driven skip); plus additions to the
  existing `tests/test_telegram.py` (`notify_run_finished`,
  `notify_awaiting_review`).
- **Five sessions before that's work** (on top of everything below), already
  committed and pushed to `origin/main`:
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
- **Six sessions before that's work** (on top of everything below), already
  pushed to `origin/main`:
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
- **Migration head is still `041a67231254`** (unchanged this session — no
  new migration needed for the commit-message-priority/bump-heuristic/
  ai-settings-tester work below) (`7d79f7acc529` → `12fa3cb1cfd5` →
  `c5ebe879231b` (an earlier session's workflow-auto-generate and
  `is_active` columns) → `041a67231254` (a prior session's
  `telegram_bot_commands_enabled`/`telegram_last_update_id` columns on
  `SystemConfig`)) — applied to the bare-metal `.venv` dev DB
  (`masimple_cicd`). **Not yet applied to the Podman
  trial-deploy DB** (an external Postgres server, dbname `postgres` — see
  `docker-compose.podman.yml`'s `PODMAN_DATABASE_URL` in `.env`) — that
  deploy hasn't been re-run since `c5ebe879231b` landed, let alone
  `041a67231254`; it'll show `/setup` again (or need `flask db
  upgrade` run against it directly) next time it's touched.
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
  - **Both `"docker"` and `"kaniko"` are now selectable `SystemConfig.
    build_engine` choices** (see this session's work above for the kaniko
    rewrite). `DockerBuildEngine` shells out to `docker buildx build`;
    against Podman's socket specifically (no native BuildKit support
    server-side), buildx's `docker-container` driver transparently spins
    up its own `moby/buildkit` container to do the real work instead —
    confirmed working end-to-end (build, run, correct output) — rather
    than needing the daemon itself to support BuildKit. `KanikoBuildEngine`
    needs no Docker/CRI socket at all — it launches kaniko-executor as its
    own Kubernetes Job instead — but does need `KANIKO_WORKSPACE_HOST_PATH`
    set and its ServiceAccount/Role/RoleBinding applied (see
    `k8s/deployment.yaml`).
  - The "docker" build engine runs bare-metal on the host (shells out to the
    host's own `docker` CLI over the mounted socket, real `dockerd` or
    Podman's Docker-API-compatible socket alike); "kaniko" runs in its own
    pod, isolated from *this app's own* container entirely (the earlier
    subprocess-based version's actual problem was the opposite of
    daemonless isolation: no isolation from this app's own container at
    all — see this session's work above).
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
  - The app now runs **five** independent background poll threads (build
    worker, deploy worker, deploy live-status poller, workflow
    orchestrator, and this session's Telegram bot long-poller — thread
    name `telegram-bot`), each with its own DB-queue/external poll target;
    none execute each other's work. Both the build and deploy workers
    additionally run a **heartbeat** thread each: every claimed job's
    `heartbeat_at` is ticked every 15s while it runs, and a stale/missing
    heartbeat (>60s) on the single `status='running'` row is auto-reaped
    as a failure. The Telegram bot thread has no heartbeat of its own —
    Telegram's `getUpdates` itself already blocks server-side up to 25s
    per poll, so a hung/crashed thread just silently stops responding to
    commands rather than needing a stale-job reaper. All five poll threads
    (and both build/deploy heartbeat threads) reliably start under
    gunicorn, same `is_werkzeug_reloader_parent()` guard every one of
    them uses — see commit 4 in the six-commits section above for the
    original bug this guard fixed.
  - **If `SystemConfig.telegram_notifications_enabled` or
    `telegram_bot_commands_enabled` is turned on**, whatever host runs
    this app needs outbound HTTPS access to `api.telegram.org` — the Bot
    API calls are plain `requests` calls (10s timeout on `sendMessage`, up
    to 35s on the bot-commands thread's `getUpdates` long-poll) with no
    retry; a network-level block just makes every outbound notification
    silently fail (logged to Error Logs, never raised into
    the login/reset flow calling it).
