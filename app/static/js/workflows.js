document.addEventListener("DOMContentLoaded", () => {
  const csrfToken = document.getElementById("workflow-view-page")?.dataset.csrf;

  // --- Step drag-and-drop reordering (workflows/view.html) — same Sortable
  // + POST-list-of-ids pattern as deployment_manifests.js's group reorder. ---
  const stepsBody = document.getElementById("workflow-steps-body");
  if (stepsBody && typeof Sortable !== "undefined") {
    const reorderUrl = stepsBody.dataset.reorderUrl;
    new Sortable(stepsBody, {
      handle: ".drag-handle",
      animation: 150,
      onEnd: async () => {
        const stepIds = Array.from(stepsBody.querySelectorAll(".workflow-step-row")).map((row) => row.dataset.stepId);
        try {
          await fetch(reorderUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
            body: JSON.stringify({ step_ids: stepIds }),
          });
        } catch {
          // A failed reorder leaves the DB order as it was — reloading shows
          // the real (unreordered) state rather than silently drifting.
          window.location.reload();
        }
      },
    });
  }

  // --- Add Build Step modal (workflows/view.html): auto-preview from git —
  // unlike the Image Builder trigger modal's explicit "Preview from Git"
  // button (builders.js), a step's target selection is made *inside* this
  // modal via checkboxes, so there's nothing to preview until at least one
  // is checked — fires automatically on every group/builder checkbox
  // change instead of behind a button. Pre-fills Bump Type/Object/Change
  // Type/Additional Description; every field stays editable, and this only
  // ever affects the step's authoring-time values, not how it resolves at
  // run time (see workflows.routes.build_step_preview's docstring). ---
  const buildStepModal = document.getElementById("add-build-step-modal");
  if (buildStepModal) {
    const previewUrl = buildStepModal.dataset.previewUrl;
    const previewStatus = document.getElementById("build-step-preview-status");
    let debounceTimer = null;

    function checkedValues(containerId) {
      return Array.from(
        document.querySelectorAll(`#${containerId} input[type="checkbox"]:checked`)
      ).map((input) => input.value);
    }

    function runPreview() {
      const groupNames = checkedValues("build-step-group-names");
      const builderIds = checkedValues("build-step-builder-ids");
      if (groupNames.length === 0 && builderIds.length === 0) {
        previewStatus.textContent = "";
        return;
      }

      const formData = new FormData();
      formData.append("csrf_token", csrfToken);
      groupNames.forEach((name) => formData.append("group_names", name));
      builderIds.forEach((id) => formData.append("builder_ids", id));
      const notesField = document.getElementById("build-step-additional-description");
      if (notesField && notesField.value.trim()) {
        formData.append("additional_description", notesField.value.trim());
      }

      previewStatus.textContent = "Reading commits since the last build...";

      fetch(previewUrl, { method: "POST", body: formData })
        .then((response) => response.json().then((data) => ({ ok: response.ok, data })))
        .then(({ ok, data }) => {
          if (!ok) {
            previewStatus.textContent = data.error || "Preview failed.";
            return;
          }

          const bumpSelect = document.getElementById("build-step-bump-type");
          if (bumpSelect && data.bump_type) bumpSelect.value = data.bump_type;

          const objectInput = document.getElementById("build-step-object");
          if (objectInput && data.object) objectInput.value = data.object;

          const changeTypeSelect = document.getElementById("build-step-change-type");
          if (changeTypeSelect && data.change_type_id) changeTypeSelect.value = data.change_type_id;

          const descriptionField = document.getElementById("build-step-additional-description");
          if (descriptionField && data.description) descriptionField.value = data.description;

          previewStatus.textContent =
            data.commit_count > 0
              ? `Pre-filled from ${data.commit_count} commit(s) since the last build — review before adding.`
              : "No new commits found since the last build — fields left as-is.";
        })
        .catch(() => {
          previewStatus.textContent = "Preview failed — check the server logs.";
        });
    }

    buildStepModal
      .querySelectorAll('input[type="checkbox"]:not(#build-step-auto-generate):not(#build-step-require-review)')
      .forEach((checkbox) => {
        checkbox.addEventListener("change", () => {
          clearTimeout(debounceTimer);
          debounceTimer = setTimeout(runPreview, 300);
        });
      });

    // Auto-generate toggle: hides the manual Bump Type/Change Type/Object/
    // Additional Description fields (they're left blank and generated at
    // run time instead — see WorkflowStep.auto_generate_build_metadata),
    // and the "require review" checkbox only makes sense once auto-generate
    // is on.
    const autoGenerateCheckbox = document.getElementById("build-step-auto-generate");
    const manualFields = document.getElementById("build-step-manual-fields");
    const requireReviewRow = document.getElementById("build-step-require-review-row");
    if (autoGenerateCheckbox && manualFields && requireReviewRow) {
      function applyAutoGenerateVisibility() {
        const auto = autoGenerateCheckbox.checked;
        manualFields.classList.toggle("hidden", auto);
        requireReviewRow.classList.toggle("hidden", !auto);
      }
      autoGenerateCheckbox.addEventListener("change", applyAutoGenerateVisibility);
      applyAutoGenerateVisibility();
    }
  }

  // --- Workflows index page (workflows/index.html) — polls statuses() and
  // reloads once a workflow's latest-run status actually changes, same
  // "only reload on a real state change" pattern as images-status.js
  // (rather than re-rendering the whole table client-side, which would
  // duplicate index.html's Jinja markup — badge classes, links, dashes —
  // in JS too). ---
  const workflowsIndexPage = document.getElementById("workflows-index-page");
  if (workflowsIndexPage) {
    const indexStatusUrl = workflowsIndexPage.dataset.statusUrl;
    let lastIndexSnapshot = null;

    function pollIndexStatuses() {
      fetch(indexStatusUrl)
        .then((response) => response.json())
        .then((data) => {
          const snapshot = JSON.stringify(data);
          if (lastIndexSnapshot !== null && snapshot !== lastIndexSnapshot) {
            window.location.reload();
            return;
          }
          lastIndexSnapshot = snapshot;
        })
        .catch(() => {});
    }
    pollIndexStatuses();
    setInterval(pollIndexStatuses, 5000);
  }

  // --- Run detail page (workflows/run.html) — polls run_status() and
  // re-renders the whole step table each tick, since steps' WorkflowStepRun
  // rows only start existing once the orchestrator actually reaches them
  // (see workflow.worker._start_step), not all up front. Stops once the
  // run reaches a terminal status. ---
  const runPage = document.getElementById("workflow-run-page");
  if (!runPage) return;

  const statusUrl = runPage.dataset.statusUrl;
  const statusBadge = document.getElementById("run-status-badge");
  const stepsTableBody = document.getElementById("workflow-run-steps-body");

  const TERMINAL_STATUSES = ["success", "failed", "completed_with_failures"];

  // The "Awaiting Review" panel (workflows/run.html) is server-rendered
  // once, outside #workflow-run-steps-body, so it never gets wiped by
  // poll()'s innerHTML replacement below — but that also means it can't
  // appear on its own once a step transitions into "awaiting_review" while
  // this page is already open. Reload once, the first time that happens,
  // rather than re-implementing the whole review form in JS.
  let hadAwaitingReview = runPage.dataset.hasAwaitingReview === "true";

  function badgeClass(status) {
    if (status === "success") return "badge-success";
    if (status === "failed" || status === "completed_with_failures") return "badge-error";
    if (status === "awaiting_review") return "badge-warning";
    return "badge-info";
  }

  function renderStepRun(stepRun) {
    const row = document.createElement("tr");
    row.className = "workflow-step-run-row";
    row.dataset.stepRunId = stepRun.id;

    const typeBadgeClass = stepRun.step_type === "build" ? "badge-primary" : "badge-info";
    const typeLabel = stepRun.step_type === "build" ? "Build" : "Deploy";

    let detailsHtml = "";
    if (stepRun.error) {
      detailsHtml += `<span class="text-error"></span>`;
    }
    if (stepRun.link) {
      detailsHtml += `<a class="link link-primary"></a>`;
    }

    row.innerHTML = `
      <td></td>
      <td><span class="badge ${typeBadgeClass} badge-sm">${typeLabel}</span></td>
      <td><span class="step-run-status badge badge-sm ${badgeClass(stepRun.status)}"></span></td>
      <td class="step-run-details text-xs">${detailsHtml}</td>
    `;

    row.children[0].textContent = stepRun.step_order + 1;
    row.querySelector(".step-run-status").textContent = stepRun.status;
    if (stepRun.error) {
      row.querySelector(".step-run-details .text-error").textContent = stepRun.error;
    }
    if (stepRun.link) {
      const link = row.querySelector(".step-run-details a");
      link.href = stepRun.link.url;
      link.textContent = stepRun.link.label;
    }

    return row;
  }

  function poll() {
    fetch(statusUrl)
      .then((response) => response.json())
      .then((data) => {
        const nowAwaitingReview = data.step_runs.some((stepRun) => stepRun.status === "awaiting_review");
        if (nowAwaitingReview && !hadAwaitingReview) {
          window.location.reload();
          return;
        }
        hadAwaitingReview = nowAwaitingReview;

        statusBadge.textContent = data.status;
        statusBadge.className = `badge ${badgeClass(data.status)}`;

        stepsTableBody.innerHTML = "";
        data.step_runs
          .sort((a, b) => a.step_order - b.step_order)
          .forEach((stepRun) => stepsTableBody.appendChild(renderStepRun(stepRun)));

        if (!TERMINAL_STATUSES.includes(data.status)) {
          setTimeout(poll, 2000);
        }
      })
      .catch(() => {
        setTimeout(poll, 2000);
      });
  }

  poll();
});
