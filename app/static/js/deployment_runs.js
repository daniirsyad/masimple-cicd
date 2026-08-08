document.addEventListener("DOMContentLoaded", () => {
  const statusEl = document.getElementById("deployment-runs-status-content");
  if (!statusEl) return;

  // Same idle<->busy skeleton-rebuild-only-on-transition approach as
  // images-status.js / builders.js's status polling.
  let wasBusy = null;

  function buildBusySkeleton() {
    statusEl.innerHTML = `
      <div class="flex items-center gap-2 text-sm">
        <span class="loading loading-spinner loading-sm text-warning"></span>
        <span>
          Deploying <strong id="deployment-runs-status-manifest"></strong> to
          <strong id="deployment-runs-status-server"></strong>
        </span>
      </div>
      <div class="mt-2 flex items-center gap-2">
        <progress id="deployment-runs-status-progress-bar" class="progress progress-warning w-full"></progress>
        <span id="deployment-runs-status-progress" class="whitespace-nowrap text-xs text-base-content/60"></span>
      </div>
      <pre id="deployment-runs-status-log" class="mt-2 h-40 min-h-20 resize-y overflow-auto rounded bg-base-300 p-2 text-xs whitespace-pre-wrap"></pre>
      <div id="deployment-runs-status-queue" class="mt-2 text-xs text-base-content/60"></div>
    `;
  }

  function poll() {
    fetch("/deployment-runs/status")
      .then((r) => r.json())
      .then((data) => {
        if (!data.busy) {
          if (wasBusy === true) {
            // A deploy that was running just finished — the run/execution
            // rows below are static server-rendered HTML from page load.
            window.location.reload();
            return;
          }
          if (wasBusy !== false) {
            statusEl.innerHTML = '<span class="text-sm text-base-content/60">Idle — no deployment running.</span>';
          }
          wasBusy = false;
          return;
        }

        if (wasBusy !== true) buildBusySkeleton();
        wasBusy = true;

        const running = data.running;
        const progress = running.progress;
        document.getElementById("deployment-runs-status-manifest").textContent = running.manifest;
        document.getElementById("deployment-runs-status-server").textContent = running.server;
        document.getElementById("deployment-runs-status-progress").textContent =
          `${progress.finished} of ${progress.total} done`;
        const progressBar = document.getElementById("deployment-runs-status-progress-bar");
        progressBar.value = progress.finished;
        progressBar.max = progress.total;

        const logEl = document.getElementById("deployment-runs-status-log");
        logEl.textContent = running.log_tail || "";
        logEl.scrollTop = logEl.scrollHeight;

        const queueEl = document.getElementById("deployment-runs-status-queue");
        queueEl.textContent = data.queue.length
          ? "Queue: " + data.queue.map((q) => `${q.manifest} → ${q.server}`).join(", ")
          : "";
      })
      .catch(() => {});
  }
  poll();
  setInterval(poll, 3000);

  // --- Configurable page auto-refresh — a full reload at the interval the
  // user picks (independent of the 3s status-widget poll above), so new
  // runs triggered by someone else or elsewhere also show up without a
  // manual refresh. Persisted per-browser in localStorage so the choice
  // survives the very reload it triggers. ---
  const AUTO_REFRESH_STORAGE_KEY = "deploymentRunsAutoRefreshMs";
  const autoRefreshSelect = document.getElementById("auto-refresh-select");
  if (autoRefreshSelect) {
    const saved = localStorage.getItem(AUTO_REFRESH_STORAGE_KEY);
    if (saved) autoRefreshSelect.value = saved;

    let autoRefreshTimer = null;
    function applyAutoRefresh() {
      if (autoRefreshTimer) clearInterval(autoRefreshTimer);
      const ms = parseInt(autoRefreshSelect.value, 10);
      if (ms > 0) autoRefreshTimer = setInterval(() => window.location.reload(), ms);
    }

    autoRefreshSelect.addEventListener("change", () => {
      localStorage.setItem(AUTO_REFRESH_STORAGE_KEY, autoRefreshSelect.value);
      applyAutoRefresh();
    });

    applyAutoRefresh();
  }
});
