document.addEventListener("DOMContentLoaded", () => {
  const page = document.getElementById("deployment-pods-list-page");
  if (!page) return;
  const serverId = page.dataset.serverId;

  function scrollToBottom(el) {
    el.scrollTop = el.scrollHeight;
  }

  function podUrl(namespace, podName, action) {
    return `/deployment-pods/${serverId}/pods/${encodeURIComponent(namespace)}/${encodeURIComponent(podName)}/${action}`;
  }

  // --- Logs modal ---
  const logsModal = document.getElementById("pod-logs-modal");
  const logsTitle = document.getElementById("pod-logs-title");
  const logsContent = document.getElementById("pod-logs-content");
  const logsContainerInput = document.getElementById("pod-logs-container-input");
  const logsContainerForm = document.getElementById("pod-logs-container-form");
  let currentLogsPod = null;

  function loadLogs() {
    if (!currentLogsPod) return;
    logsContent.textContent = "Loading...";

    const container = logsContainerInput.value.trim();
    const url = new URL(podUrl(currentLogsPod.namespace, currentLogsPod.podName, "logs"), window.location.origin);
    if (container) url.searchParams.set("container", container);

    fetch(url)
      .then((r) => r.json())
      .then((data) => {
        logsContent.textContent = data.error || data.logs || "(no log output)";
        // Logs read newest-at-the-bottom — land on the most recent lines by
        // default rather than making the user scroll down themselves.
        scrollToBottom(logsContent);
      })
      .catch(() => {
        logsContent.textContent = "Failed to fetch logs.";
      });
  }

  if (logsModal) {
    document.querySelectorAll(".pod-logs-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentLogsPod = { namespace: btn.dataset.namespace, podName: btn.dataset.podName };
        logsTitle.textContent = currentLogsPod.podName;
        logsContainerInput.value = "";
        logsModal.showModal();
        loadLogs();
      });
    });

    logsContainerForm.addEventListener("submit", (event) => {
      event.preventDefault();
      loadLogs();
    });
  }

  // --- Describe modal ---
  const describeModal = document.getElementById("pod-describe-modal");
  const describeTitle = document.getElementById("pod-describe-title");
  const describeContent = document.getElementById("pod-describe-content");

  if (describeModal) {
    document.querySelectorAll(".pod-describe-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const namespace = btn.dataset.namespace;
        const podName = btn.dataset.podName;
        describeTitle.textContent = podName;
        describeContent.textContent = "Loading...";
        describeModal.showModal();

        fetch(podUrl(namespace, podName, "describe"))
          .then((r) => r.json())
          .then((data) => {
            describeContent.textContent = data.error || data.description || "(no output)";
            scrollToBottom(describeContent);
          })
          .catch(() => {
            describeContent.textContent = "Failed to fetch describe output.";
          });
      });
    });
  }

  // --- Generic resource describe modal (namespaces/nodes/services/ingress/
  // PVs/PVCs on deployment_pods/resources.html) — same shape as the pod
  // describe modal above, just against /resources/<kind>/describe. ---
  const resourceDescribeModal = document.getElementById("resource-describe-modal");
  const resourceDescribeTitle = document.getElementById("resource-describe-title");
  const resourceDescribeContent = document.getElementById("resource-describe-content");

  if (resourceDescribeModal) {
    document.querySelectorAll(".resource-describe-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const kind = btn.dataset.kind;
        const name = btn.dataset.name;
        const namespace = btn.dataset.namespace || "";

        resourceDescribeTitle.textContent = name;
        resourceDescribeContent.textContent = "Loading...";
        resourceDescribeModal.showModal();

        const url = new URL(`/deployment-pods/${serverId}/resources/${kind}/describe`, window.location.origin);
        url.searchParams.set("name", name);
        if (namespace) url.searchParams.set("namespace", namespace);

        fetch(url)
          .then((r) => r.json())
          .then((data) => {
            resourceDescribeContent.textContent = data.error || data.description || "(no output)";
            scrollToBottom(resourceDescribeContent);
          })
          .catch(() => {
            resourceDescribeContent.textContent = "Failed to fetch describe output.";
          });
      });
    });
  }
});
