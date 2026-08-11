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

  function badgeClass(status) {
    if (status === "success") return "badge-success";
    if (status === "failed" || status === "completed_with_failures") return "badge-error";
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
