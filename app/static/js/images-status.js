document.addEventListener("DOMContentLoaded", () => {
  const statusEl = document.getElementById("images-status-content");
  if (!statusEl) return;

  // Tracks whether the last poll was "busy", so the skeleton (including the
  // log <pre>) is only rebuilt on an idle<->busy transition, not on every
  // poll — rebuilding it every 3s would wipe both a manual resize (see
  // resize-y below) and the scroll position each time.
  let wasBusy = null;

  function buildBusySkeleton() {
    statusEl.innerHTML = `
      <div class="flex items-center gap-2 text-sm">
        <span class="loading loading-spinner loading-sm text-warning"></span>
        <span>
          Building <strong id="images-status-name"></strong> for batch <code id="images-status-batch"></code>
        </span>
      </div>
      <div class="mt-2 flex items-center gap-2">
        <progress id="images-status-progress-bar" class="progress progress-warning w-full"></progress>
        <span id="images-status-progress" class="whitespace-nowrap text-xs text-base-content/60"></span>
      </div>
      <pre id="images-status-log" class="mt-2 h-40 min-h-20 resize-y overflow-auto rounded bg-base-300 p-2 text-xs whitespace-pre-wrap"></pre>
      <div id="images-status-queue" class="mt-2 text-xs text-base-content/60"></div>
    `;
  }

  function poll() {
    fetch("/images/status")
      .then((r) => r.json())
      .then((data) => {
        if (!data.busy) {
          if (wasBusy === true) {
            // A build that was running when this page loaded (or during an
            // earlier poll) just finished — the batch/build rows below are
            // static server-rendered HTML from page load, so nothing else
            // on the page would reflect the new status without a reload.
            window.location.reload();
            return;
          }
          if (wasBusy !== false) {
            statusEl.innerHTML =
              '<span class="text-sm text-base-content/60">Idle — no build running.</span>';
          }
          wasBusy = false;
          return;
        }

        if (wasBusy !== true) buildBusySkeleton();
        wasBusy = true;

        const running = data.running;
        const progress = running.progress;
        document.getElementById("images-status-name").textContent = running.builder;
        document.getElementById("images-status-batch").textContent = running.full_version_string;
        document.getElementById("images-status-progress").textContent =
          `${progress.finished} of ${progress.total} built`;
        const progressBar = document.getElementById("images-status-progress-bar");
        progressBar.value = progress.finished;
        progressBar.max = progress.total;

        const logEl = document.getElementById("images-status-log");
        logEl.textContent = running.log_tail || "";
        // Always stick to the latest output on refresh — no manual
        // scrolling needed to see it.
        logEl.scrollTop = logEl.scrollHeight;

        const queueEl = document.getElementById("images-status-queue");
        queueEl.textContent = data.queue.length
          ? "Queue: " + data.queue.map((q) => `${q.full_version_string} (${q.builder})`).join(", ")
          : "";
      })
      .catch(() => {});
  }
  poll();
  setInterval(poll, 3000);
});
