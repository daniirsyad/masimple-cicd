# Performance plan: slow page-to-page navigation

Measured 2026-11-27 on the dev machine: dev database (small data set), Flask
test client, logged in as an admin, the same htmx-boosted GET as the browser
sends. These numbers are server time only and leave out network, proxy and
browser time, so production will be slower. Step 0 measures production.

## What was measured

| Page | Server time | SQL queries |
|---|---|---|
| `/github/` | **1031 ms** | 29 |
| `/images/` | 145 ms | **222** |
| `/menus/` | 98 ms | 96 |
| `/permissions/` | 97 ms | 114 |
| `/deployment-manifests/` | 79 ms | 88 |
| `/builders/` | 69 ms | 82 |
| `/documentation/` | 62 ms | 84 |
| an almost empty page (`/workflows/archived`) | 20 ms | **15** |

Findings, in order of how much they likely cost:

1. **Page scripts are re-checked over the network on every page change.**
   Static files are served with `Cache-Control: no-cache`, so each
   `<script src>` that htmx re-inserts with the new `<body>` costs a round trip
   to the server and gets a 304 back. Builders has 6 of these and Manifests
   has 7. Behind a reverse proxy with real latency this is likely the largest
   delay you can feel. Each check also takes one of gunicorn's 12 request
   threads.
2. **Every request parses all 39 migration files.** `is_setup_complete()` in
   [app/utils/setup_status.py](../app/utils/setup_status.py) rebuilds
   Alembic's script directory each call to find the head revision (~5 ms) and
   asks the DB for the current one. It runs twice per page: in the
   `before_request` hook and in the context processor. That's about 22 ms
   plus 4 queries on every page.
3. **Fixed work on every page: ~15 queries before the page's own work.**
   These come from loading the user, two separate menu queries (sidebar and
   navbar), `get_system_config()` twice, the setup checks, and the review
   badge. `awaiting_review_step_runs_for` loads `step_run.run` once per row,
   so it gets slower as reviews pile up.
4. **N+1 queries on list pages.** Images (222), Permissions (114), Menus (96),
   Manifests (88), Builders (82) and Documentation (84) run one query per row
   (relationships loaded lazily inside loops and templates). The count grows
   with the data, so production, which has more data than dev, is slower in
   proportion.
5. **Some pages call outside systems before they send any HTML.** `/github/`
   calls the GitHub API for every connection on every visit (1 s here).
   Pods and the cluster resource pages run `kubectl` inside the request,
   with timeouts of up to 30 s. While that runs, the page stays on the old
   screen with the loading bar.
6. **Server capacity is small and shared with polling.** gunicorn runs 3
   workers × 4 threads = 12 requests at once. The Builders, Images and
   Deployment Runs pages poll every 3 s and Workflows every 5 s, even when
   the tab is hidden. Each pod log stream (SSE) holds a thread for as long
   as it's open. A few open tabs can fill all 12, and then the next
   navigation waits in the queue. Also, each gunicorn worker starts its own
   copy of the background threads, so the live-status poller runs 3 times
   (3× the `kubectl`/`helm` calls). Only Telegram and Discord use an
   advisory lock to run once.

## Done (steps 0–6, 2026-11-27)

Same dev measurement after the change:

| Page | Before | After |
|---|---|---|
| almost empty page | 20 ms, 15 queries | 13 ms, 8 queries |
| `/images/` | 145 ms, 222 q | 99 ms, 129 q |
| `/deployment-manifests/` | 79 ms, 88 q | 59 ms, 76 q |
| `/builders/` | 69 ms, 82 q | 51 ms, 68 q |
| `/documentation/` | 62 ms, 84 q | 37 ms, 57 q |
| `/` | 50 ms, 41 q | 21 ms, 24 q |

Page scripts no longer go back to the server on page changes (step 1).
That saving shows only in the browser, so the table above leaves it out.
### Step 4 (2026-11-27)

| Page | Before step 0 | After step 3 | After step 4 |
|---|---|---|---|
| `/images/` | 145 ms, 222 q | 99 ms, 129 q | 18 ms, 13 q |
| `/permissions/` | 97 ms, 114 q | 77 ms, 107 q | 21 ms, 11 q |
| `/menus/` | 98 ms, 96 q | 72 ms, 89 q | 24 ms, 10 q |
| `/deployment-manifests/` | 79 ms, 88 q | 59 ms, 76 q | 30 ms, 28 q |
| `/builders/` | 69 ms, 82 q | 51 ms, 68 q | 27 ms, 22 q |
| `/documentation/` | 62 ms, 84 q | 37 ms, 57 q | 16 ms, 17 q |

These six pages' query counts no longer grow with the number of rows
(`test_list_page_queries_do_not_grow_with_rows`). One per-row cost is
left: Manifests' "update available" check still resolves each manifest
that is deployed somewhere.

### Step 5 (2026-11-27)

- `/github/` renders from the DB only (dev: 1031 ms -> 12 ms). Each
  connection's "Available to register" list loads after the page shows,
  from `git_sources.available_repos` (`hx-get`, `hx-trigger="load"`).
- The 8 cluster pages (pods, resources, namespaces, secrets, configmaps,
  ingresses, network policies, workloads) use `@loading_page_first`. On an
  htmx navigation it sends the layout with a "Reading the cluster..."
  spinner at once, which fetches the real page (`X-Render-Now`) and swaps
  it in. Chosen over splitting each page into fragments: those pages also
  read namespaces for their forms from kubectl, so a per-table split would
  have touched every one of them.
- Not done: caching the GitHub repo list. With the page no longer waiting
  on it, it wasn't needed.

### Step 6 (2026-11-27)

- `pageInterval` (page.js) skips its tick while the tab is hidden and runs
  one at once when it's shown again. That covers the Builders, Images,
  Deployment Runs and Workflows polls.
- The live-status poller waits for an advisory lock (`become_leader` in
  `app/utils/leader.py`, now shared with the Telegram and Discord workers,
  which had their own copies), so it runs in one gunicorn process, not 3.
  The build, deploy and workflow workers were left as they are: they
  already claim jobs with `FOR UPDATE SKIP LOCKED`, which is safe across
  processes.
- gunicorn `--threads 8` (was 4): 24 requests at once instead of 12. DB
  pool per process raised to 10 + 10 overflow to match.

All steps are done.

## Production check (2026-11-27)

Measured on production after steps 0–6 were deployed: curl for server
time, and headless Chromium clicking the real sidebar for browser time.
Server time was fine everywhere except Deployment Runs. The real cost was
in the browser:

| Click to | Server | Until painted, before | After the fix |
|---|---|---|---|
| Deployment Manifests | 230 ms | **5.9 s** (5.3 s of main thread blocked) | 0.7 s |
| YAML Generator (after Manifests) | 10 ms | 480 ms | 230 ms |
| Deployment Runs | 1000 ms | 1.1 s | not measured yet (needs deploy) |

- **Manifests built 52 CodeMirror editors on load**, one per edit/view
  modal (26,000 DOM nodes). Editors are now built when their modal first
  opens (`YamlEditor.lazyYamlEditor`). Checked against production with
  the new JS swapped in: editor appears with the manifest's YAML, no JS
  errors.
- **Every sidebar click got a 308 first**: menu URLs have no trailing slash
  and blueprint index routes do (about 70 ms). `strict_slashes = False`
  now serves both.
- **Deployment Runs** loaded each run's executions twice with their logs
  and rendered YAML, plus every manifest's YAML for the filter dropdown.
  It now loads them once, without those columns. Dev data is too small to
  show the gain, so measure it on production after deploy.

## Plan

Steps are ordered by value for the effort. Each step can ship on its own and
keeps today's behavior.

### Step 0: measure production first (small)

- Add a `Server-Timing` response header (total ms and SQL query count) from
  an `after_request` hook. Browser DevTools then shows server time vs.
  network time for each navigation, with no extra tools.
- Open DevTools > Network on production and click from one page to another.
  Count the script 304s and note the HTML document's time. This confirms
  which of 1–6 actually dominate there before the bigger steps.
- On the production DB, run `EXPLAIN ANALYZE` on the polled queries
  (`status = 'queued'`, `awaiting_review`, live-check candidates) to look for
  missing indexes.

### Step 1: cache static files (small, likely the biggest felt win)

- Set `SEND_FILE_MAX_AGE_DEFAULT` to one year and add a version to every
  static URL (a `url_for` override in `create_app()` that appends
  `?v=<file mtime>`), so a deploy still delivers new files at once.
- After this, page changes stop re-checking scripts at all.
- Check that the reverse proxy gzips HTML/CSS/JS. `output.css` is 133 KB
  uncompressed.

### Step 2: stop parsing migrations on every request (small)

- Compute the head revision once per process. It can only change when new
  code is deployed, which restarts the process.
- Call `is_setup_complete()` once per request (store it on `g`) and reuse it
  in the context processor.
- Keep the current-revision and user check live, so the "tables were
  dropped, go back to /setup" safety in its docstring still holds.
- Saves about 20 ms and 3–4 queries on every page.

### Step 3: trim the fixed per-page queries (small)

- Build the sidebar and navbar from one `Menu` query.
- Cache `get_system_config()` on `g` for the request.
- Change `has_permission` to check a set of codes built once per request.
- Change `awaiting_review_step_runs_for` to eager-load `run` (and what
  `is_accessible_to` reads) instead of loading it per row.
- Goal: an empty page at 5–6 queries instead of 15.

### Step 4: fix N+1 queries on the list pages (medium)

- Add `selectinload`/`joinedload` for the relationships each template walks.
  Start with Images, then Permissions, Menus, Manifests, Builders and
  Documentation.
- Precompute per-row values that run a query (e.g. `builders_using()` in
  loops, `delete_impacts`) with one grouped query.
- Add a test per page that seeds N rows and asserts the query count doesn't
  grow with N, so it stays fixed.

### Step 5: don't wait on GitHub or kubectl before showing the page (medium)

- `/github/`: render the page from the DB right away and load each
  connection's "available repos" list with `hx-get` after the page shows.
  Optionally cache that list for a minute or two per connection.
- Pods and resource pages: same pattern. Show the page shell and server
  name at once, then load the `kubectl` table into it with a spinner
  (`showLoading`). A slow cluster then delays only that table, not the
  whole navigation.

### Step 6: capacity and background work (medium)

- Pause the page polls (`pageInterval` in builders, images-status,
  deployment_runs, workflows) while `document.hidden`, and poll right away
  when the tab becomes visible again. One change in `page.js` covers all of
  them.
- Run the live-status poller (and check the build, deploy and workflow
  workers) in one process only, using the same Postgres advisory-lock
  pattern as `telegram/worker.py`. That cuts the background `kubectl`/`helm`
  load to a third.
- Raise gunicorn `--threads` (e.g. 8) so SSE log streams and polls can't
  fill all request slots.

## Not planned (and why)

- **Redis or a cache server:** nothing measured needs it. Request-scoped
  caching (`g`) and lazy-loading the slow fragments cover it.
- **Rewriting the frontend or dropping htmx:** the swap itself is fast. The
  cost is in the requests around it (steps 1 and 5).
- **Pre-rendered or cached HTML pages:** pages are per-user (permissions) and
  live (statuses), and they're cheap once steps 3–4 are done.
