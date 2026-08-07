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

- **288 tests passing**, run via:
  ```bash
  source .venv/bin/activate && set -a && source .env && set +a
  export DATABASE_URL=$(echo $DATABASE_URL | sed 's/@db:/@172.29.16.1:/')
  export TEST_DATABASE_URL=$(echo $TEST_DATABASE_URL | sed 's/@db:/@172.29.16.1:/')
  FLASK_ENV=testing python -m pytest tests/ -q
  ```
  (the sandbox can't reach the `db` Docker Compose hostname directly — that
  `sed` swaps it for the WSL2 gateway IP)
- CSS changes need a rebuild to actually show up: `npm run build:css` (needs
  `npm install` first if `node_modules/` isn't present).
- Migration head: `128297392cfc` (`hide_navbar_title_when_sidebar_open`),
  applied to the dev DB.
- **Nothing in this repo is committed except one staged deletion** —
  `git log` still shows only the original "first commit." `ai-plan.md`'s
  removal (its content lives on in `AI_CONTEXT.md`) is `git rm`'d and
  staged; everything else is unstaged working-tree state, across every
  session to date. Don't run any destructive git command without checking
  first, and don't commit anything unless explicitly asked.
- Enduring infra facts:
  - The "docker" build engine runs bare-metal on the host (shells out to the
    host's own `docker` CLI over the mounted socket); "kaniko" runs
    containerized/daemonless. `buildx` needed on the host for local
    "docker"-engine builds regardless of the Dockerfile's own copy.
  - `REPO_CLONE_ROOT` (`<app>/data/repos`) has no persistent volume mounted —
    registered repos' local clones are lost on container restart. Re-sync and
    the build worker self-heal via `sync_repo(repo_name=...)`; the Builder
    create/edit branch/Dockerfile pickers and `/github`'s unregistered-repo
    listing do not.
