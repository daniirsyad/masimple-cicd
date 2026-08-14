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
   Part 9** — everything in the "Current state" section below postdates it
   and has no narrative write-up there yet.

Then ask me what to work on next rather than assuming.

## Current state

- **829 tests passing** (as of the last full run).
- **Everything is committed and pushed to `origin/main`**, most recently as:
  1. **This same commit** — Kubernetes Ingress/NetworkPolicy management, login
     security (lockout), and a Telegram security-notification integration,
     all from one session's work:
     - **Ingress CRUD** (`/deployment-pods`'s new "Ingress" tab,
       `deployment_ingress.manage` permission): add/edit/delete, with two
       editing modes toggled per create/edit dialog — **Form** (host,
       optional ingress class, optional TLS secret name, one-or-more paths
       each with path/pathType/backend service+port — add/remove rows) and
       **raw YAML** (CodeMirror, same shared editor module as Deployment
       Manifests/YAML Generator; server-side confirms `kind: Ingress`
       before applying, so this box can't be used to slip in an arbitrary
       manifest under a narrower permission). An Ingress that already has
       more than one rule or TLS entry forces YAML-only editing — the Form
       only ever shows/edits the first rule, so this stops it from
       silently dropping the others on save. Describe (kubectl
       describe — events/load-balancer address) is kept via the existing
       generic `RESOURCE_KINDS`/`describe_resource()` mechanism rather than
       a new dedicated route. The dict-building logic
       (`app/services/yaml_generator/ingress.py`'s `build()`) is shared
       with `/yaml-generator`, extended to accept either a single
       path/backend (that page's existing shape, untouched) or a `paths`
       list (the CRUD feature's one-host/multiple-paths shape) — one
       source of truth for what an Ingress manifest dict looks like.
       - **An "Allowed Source IPs/CIDRs" field (nginx-ingress
         `whitelist-source-range` annotation) was added, then fully
         reverted** a turn later per explicit request — no trace of it
         remains in code or tests. Worth knowing in case this resurfaces;
         nginx-ingress has no equivalent block-list annotation, only
         allow-list, and a NetworkPolicy (see below) is a more portable
         alternative for IP-based restriction.
     - **NetworkPolicy CRUD** (`/deployment-pods`'s new "Network Policies"
       tab, `deployment_network_policy.manage` permission): same
       Form/raw-YAML dual-mode pattern as Ingress. Form mode: pod selector
       (which pods the policy applies to, blank = all), independent
       Ingress/Egress checkboxes each revealing their own peer list (Pod
       Selector labels / Namespace Selector labels / IP Block CIDR —
       add/remove rows) and port list (protocol+port). Same multi-rule
       safety guard as Ingress (more than one ingress or egress rule
       forces YAML-only). New shared builder
       `app/services/yaml_generator/network_policy.py`, also added to
       `/yaml-generator`'s resource-kind dropdown. `KubernetesProvider`
       gained `list_ingresses`/`delete_ingress`/`list_network_policies`/
       `delete_network_policy` (create/update reuse the existing
       `apply()` — no new provider methods needed there).
     - **Login lockout**: `SystemConfig.max_login_attempts` (new
       "Security" section on `/config`, default 5) + `User.
       failed_login_attempts`/`locked_at`. An account locks after that
       many *consecutive* wrong-password attempts — a locked account is
       rejected even if the next attempt's password is actually correct
       (checked before the password itself), and the login page shows the
       same generic "Invalid username or password" either way, so the
       attempt that crosses the threshold doesn't reveal anything extra to
       whoever's typing. Only clearable by a new `user.unlock` permission
       holder (Locked badge + Unlock button on `/users`) or the account
       owner completing a Telegram password reset (see below) — no
       auto-expiry.
     - **Telegram integration** (`app/services/telegram/` —
       `TelegramNotifier.send_message()` wraps the Bot API's
       `sendMessage`, `notify_user()`/`notify_security_contact()` are
       best-effort wrappers that never raise, only log-and-return-False on
       any failure). `SystemConfig.telegram_notifications_enabled` +
       `encrypted_telegram_bot_token` (Fernet, same convention as every
       other stored credential) live in a new "Telegram Integration"
       section on `/config`. **Deliberate design, reached after an explicit
       follow-up correction**: wrong-password/lockout/login security
       alerts do **not** go to the affected account's own Telegram chat —
       they all go to the single user configured as `SystemConfig.
       security_notification_user_id` (a dropdown of every user, also on
       `/config`), so one security contact watches every account rather
       than each user getting pinged about their own activity. The
       forgot-password flow is the one exception and is *not* routed
       through the security contact: a reset link can only be acted on by
       the account owner, so it always goes straight to that user's own
       `User.telegram_chat_id` (admin-set on `/users`, along with the
       security contact's own chat ID if they're the one configured).
       - **Forgot/reset password**: `/forgot-password` (username →
         Telegram link, only if that account has a chat ID configured) →
         `/reset-password/<token>`. New `PasswordResetToken` model — only
         a sha256 hash of the token is ever stored, 15-minute expiry,
         single-use. The flash message is identical whether or not the
         submitted username/Telegram setup actually exists, so this can't
         be used to enumerate valid usernames. A successful reset also
         clears any existing lockout (same trust level as a `user.unlock`
         holder resetting it by hand).
     - **Self-service `/account` page** (new `account` blueprint, no
       permission gate beyond being logged in; linked from the navbar's
       user dropdown as "My Account"): lets any user edit their own Full
       Name, Telegram Chat ID, and password (current password required to
       set a new one). Username/role/active status are deliberately absent
       from this form — those stay admin-only via `/users`.
     - New migrations (already applied to the dev DB via
       `flask db upgrade`): `5c9ba2cb302e` (login lockout columns,
       Telegram columns, `password_reset_tokens` table — the three
       `NOT NULL` columns got `server_default` added by hand after
       autogenerate, same as every prior batch that added a NOT NULL
       column to a table with existing rows), `168112da5b63`
       (`security_notification_user_id`). Chain is now `2a347cde1361` →
       `5c9ba2cb302e` → `168112da5b63` (head).
     - New permissions (seeded, applied to the dev DB, granted to Super
       Admin): `deployment_ingress.manage`, `deployment_network_policy.
       manage`, `user.unlock`.
     - **⚠️ None of this has been verified in a live browser** — same
       sandbox constraint as everything else in this file (no working
       headless Chromium here): the Ingress/NetworkPolicy Form↔YAML
       toggles and their several independent add/remove-row widgets, the
       new `/config` Security/Telegram sections, the actual lockout UX,
       Telegram message delivery/formatting (only exercised against a
       mocked `requests.post`, never a real bot), and the `/account` page.
     - **Explicitly not built** (told "not for now"): using this same
       Telegram integration (plus a future Discord one) to *trigger*
       Workflow runs, not just notify about login/security events. The
       bot-token/chat-ID plumbing here is meant to be reused for that
       later, but no command-listening/webhook surface exists yet.
  2. **`ec9d727`** — Dockerfile management (`/dockerfiles`, a `Builder` can
     build from a managed Dockerfile instead of a path in its own repo),
     one-directional Version-to-Version linking (widens a Version's
     "Linked Batches" picker on Documentation), a configurable commit log
     limit (`SystemConfig.commit_log_limit`), a home page/sidebar redesign,
     and a rebuilt Documentation-page Object filter/picker (several rounds
     of dropdown-clipping and re-render interaction-bug fixes along the
     way — see this commit or `AI_CONTEXT.md` Part 9 if a similar
     dropdown-in-a-`.collapse`-or-`<dialog>` widget is added elsewhere).
  3. **`6e8eede`** — a shared `app/static/js/yaml_editor.js` CodeMirror
     module (fixing a cursor-position bug that existed as two duplicated
     init blocks) plus the original `/yaml-generator` page (Deployment/
     Service/ConfigMap/Secret/Ingress — Ingress and the shared editor are
     both now extended further by this session's work above).
  Anything older is covered by `git log`/`AI_CONTEXT.md`, not repeated here.
- **Migration head is `168112da5b63`** — already applied to the dev DB via
  `flask db upgrade` (confirmed via `flask db current`).
- **`seeds/seed_menu.py`'s label-drift risk is dormant, not fixed** — see
  `AI_CONTEXT.md` Part 7 and the `seed_menu_label_mismatch` memory (stale/
  historical) if this resurfaces. None of this session's new permissions
  needed a new `Menu` row (Ingress/NetworkPolicy are tabs under the
  existing `/deployment-pods` pages; Security/Telegram are sections on the
  existing `/config` page; `/account` is reached from the navbar dropdown,
  not the sidebar menu tree) — this risk is unchanged from before.
- Run tests via:
  ```bash
  source .venv/bin/activate && set -a && source .env && set +a
  FLASK_ENV=testing python -m pytest tests/ -q
  ```
  (`.env`'s `DATABASE_URL`/`TEST_DATABASE_URL` already point at the WSL2
  gateway IP `172.29.16.1` directly, not the `db` Docker Compose hostname —
  no `sed` swap needed from inside the sandbox.)
- CSS changes need a rebuild to actually show up: `npm run build:css`
  (already run as of this session's work — new daisyUI classes pulled in
  along the way, e.g. `.divider`, weren't previously compiled).
- **gunicorn now runs `--worker-class gthread --threads 4 --timeout 120`**
  (`entrypoint.sh`), not plain sync workers — changed to support the pod-logs
  SSE stream. Keep this in mind before adding any other long-lived-connection
  feature: worker/thread capacity is a real, finite budget (3 workers × 4
  threads), not "one request per worker, always fine."
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
  - `REPO_CLONE_ROOT` (`<app>/data/repos`) has a persistent named Docker
    volume (`repo_clones`, `docker-compose.yml`) — registered repos' local
    clones survive container restarts. This is a Docker-*managed* named
    volume, **not** a bind mount to the project's own `./data/repos` folder
    on the host — editing/adding a file under the project checkout's
    `data/repos` does nothing; reach the real clone via
    `docker compose exec web ls /app/data/repos`.
  - The app now runs **four** independent background poll threads (build
    worker, deploy worker, deploy live-status poller, workflow
    orchestrator), each with its own DB-queue; none execute each other's
    work. Both the build and deploy workers additionally run a
    **heartbeat** thread each: every claimed job's `heartbeat_at` is ticked
    every 15s while it runs, and a stale/missing heartbeat (>60s) on the
    single `status='running'` row is auto-reaped as a failure.
  - **If `SystemConfig.telegram_notifications_enabled` is turned on**,
    whatever host runs this app needs outbound HTTPS access to
    `api.telegram.org` — the Bot API call is a plain `requests.post` with a
    10s timeout and no retry; a network-level block just makes every
    notification silently fail (logged to Error Logs, never raised into
    the login/reset flow calling it).
