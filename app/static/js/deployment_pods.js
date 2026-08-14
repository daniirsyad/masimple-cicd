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

  // These JSON endpoints render into a <pre> via .textContent, which can't
  // hold a real <a> — so on failure they send a separate `error_log_url`
  // field instead of embedding a link in the message text (contrast with
  // error_detail_link() on the Python side, used for flash()/inline-banner
  // messages, which CAN embed a real link since those render via Jinja).
  // This rebuilds the same "message + link" shape as a couple of DOM nodes.
  function renderErrorWithLink(el, message, errorLogUrl) {
    el.textContent = message;
    if (!errorLogUrl) return;
    el.appendChild(document.createElement("br"));
    const link = document.createElement("a");
    link.href = errorLogUrl;
    link.target = "_blank";
    link.className = "link link-primary";
    link.textContent = "View error details";
    el.appendChild(link);
  }

  // --- Live-refresh helper, used by the Describe modal below. Plain
  // setInterval polling, not a real stream — `kubectl describe` has no
  // follow/watch mode to stream from, unlike pod logs (see the Logs modal
  // below, which now uses a real SSE stream instead of this helper).
  // Auto-starts when the modal opens (Describe is opened specifically to
  // watch something happening) and always stops on close, so nothing keeps
  // polling in the background once nobody's looking. ---
  function createLiveRefresher(intervalMs, loadFn, toggleButton, indicatorEl) {
    let timer = null;
    let live = false;

    function updateUi() {
      if (toggleButton) toggleButton.textContent = live ? "Pause" : "Resume Live";
      if (indicatorEl) {
        indicatorEl.textContent = live ? "Live" : "Paused";
        indicatorEl.classList.toggle("badge-success", live);
        indicatorEl.classList.toggle("badge-ghost", !live);
      }
    }

    function stop() {
      live = false;
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
      updateUi();
    }

    function start() {
      live = true;
      if (timer) clearInterval(timer);
      // Refreshes triggered by the timer are silent (no "Loading..." flash)
      // — only the initial open and manual actions show a loading state.
      timer = setInterval(() => loadFn(false), intervalMs);
      updateUi();
    }

    if (toggleButton) {
      toggleButton.addEventListener("click", () => (live ? stop() : start()));
    }

    return { start, stop };
  }

  // --- Logs modal — a real stream (`kubectl logs -f` over Server-Sent
  // Events, see deployment_pods.routes.pod_logs_stream), unlike the Describe
  // modal below which stays on setInterval polling (kubectl describe has no
  // follow/watch mode to stream from). One EventSource per "open", torn
  // down and replaced on close/Pause/container-filter-change/reopen rather
  // than reused, so there's never more than one live kubectl process behind
  // an open modal. ---
  const logsModal = document.getElementById("pod-logs-modal");
  const logsTitle = document.getElementById("pod-logs-title");
  const logsContent = document.getElementById("pod-logs-content");
  const logsContainerInput = document.getElementById("pod-logs-container-input");
  const logsContainerForm = document.getElementById("pod-logs-container-form");
  const logsLiveToggle = document.getElementById("pod-logs-live-toggle");
  const logsLiveIndicator = document.getElementById("pod-logs-live-indicator");
  let currentLogsPod = null;
  let logsEventSource = null;

  function setLogsLiveUi(live) {
    if (logsLiveToggle) logsLiveToggle.textContent = live ? "Pause" : "Resume Live";
    if (logsLiveIndicator) {
      logsLiveIndicator.textContent = live ? "Live" : "Paused";
      logsLiveIndicator.classList.toggle("badge-success", live);
      logsLiveIndicator.classList.toggle("badge-ghost", !live);
    }
  }

  function appendLogLine(line) {
    if (logsContent.textContent === "Waiting for log output...") logsContent.textContent = "";
    const atBottom = logsContent.scrollHeight - logsContent.scrollTop <= logsContent.clientHeight + 20;
    logsContent.textContent += (logsContent.textContent ? "\n" : "") + line;
    // Logs read newest-at-the-bottom — keep following the tail only if the
    // user hasn't scrolled up to read older lines themselves.
    if (atBottom) scrollToBottom(logsContent);
  }

  function stopLogsStream() {
    if (logsEventSource) {
      logsEventSource.close();
      logsEventSource = null;
    }
    setLogsLiveUi(false);
  }

  function startLogsStream() {
    if (!currentLogsPod) return;
    stopLogsStream();
    logsContent.textContent = "Waiting for log output...";

    const container = logsContainerInput.value.trim();
    const url = new URL(
      podUrl(currentLogsPod.namespace, currentLogsPod.podName, "logs/stream"),
      window.location.origin
    );
    if (container) url.searchParams.set("container", container);

    logsEventSource = new EventSource(url);
    logsEventSource.onmessage = (event) => {
      appendLogLine(JSON.parse(event.data).line);
    };
    // A named "log-error" event means the server hit a genuine kubectl
    // failure (bad pod, permission, etc.) — stop instead of letting
    // EventSource's default auto-reconnect hammer the same broken call
    // every few seconds. A plain connection drop (pod restart, network
    // blip) fires the unnamed native "error" event instead, which is left
    // alone so the browser's normal auto-reconnect just resumes the tail.
    logsEventSource.addEventListener("log-error", (event) => {
      const data = JSON.parse(event.data);
      renderErrorWithLink(logsContent, data.error, data.error_log_url);
      stopLogsStream();
    });
    setLogsLiveUi(true);
  }

  if (logsModal) {
    document.querySelectorAll(".pod-logs-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentLogsPod = { namespace: btn.dataset.namespace, podName: btn.dataset.podName };
        logsTitle.textContent = currentLogsPod.podName;
        logsContainerInput.value = "";
        logsModal.showModal();
        startLogsStream();
      });
    });

    logsContainerForm.addEventListener("submit", (event) => {
      event.preventDefault();
      startLogsStream();
    });

    if (logsLiveToggle) {
      logsLiveToggle.addEventListener("click", () => (logsEventSource ? stopLogsStream() : startLogsStream()));
    }

    logsModal.addEventListener("close", () => stopLogsStream());
  }

  // --- Describe modal ---
  const describeModal = document.getElementById("pod-describe-modal");
  const describeTitle = document.getElementById("pod-describe-title");
  const describeContent = document.getElementById("pod-describe-content");
  const describeLiveToggle = document.getElementById("pod-describe-live-toggle");
  const describeLiveIndicator = document.getElementById("pod-describe-live-indicator");
  let currentDescribePod = null;

  function loadDescribe(showLoading = true) {
    if (!currentDescribePod) return;
    if (showLoading) describeContent.textContent = "Loading...";

    fetch(podUrl(currentDescribePod.namespace, currentDescribePod.podName, "describe"))
      .then((r) => r.json())
      .then((data) => {
        if (data.error) {
          renderErrorWithLink(describeContent, data.error, data.error_log_url);
        } else {
          describeContent.textContent = data.description || "(no output)";
        }
        scrollToBottom(describeContent);
      })
      .catch(() => {
        describeContent.textContent = "Failed to fetch describe output.";
      });
  }

  if (describeModal) {
    // Describe changes less often than logs (status/events, not a
    // continuous stream) — a slightly longer interval is enough to feel
    // live without extra kubectl load.
    const describeLive = createLiveRefresher(5000, loadDescribe, describeLiveToggle, describeLiveIndicator);

    document.querySelectorAll(".pod-describe-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentDescribePod = { namespace: btn.dataset.namespace, podName: btn.dataset.podName };
        describeTitle.textContent = currentDescribePod.podName;
        describeModal.showModal();
        loadDescribe();
        describeLive.start();
      });
    });

    describeModal.addEventListener("close", () => describeLive.stop());
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
            if (data.error) {
              renderErrorWithLink(resourceDescribeContent, data.error, data.error_log_url);
            } else {
              resourceDescribeContent.textContent = data.description || "(no output)";
            }
            scrollToBottom(resourceDescribeContent);
          })
          .catch(() => {
            resourceDescribeContent.textContent = "Failed to fetch describe output.";
          });
      });
    });
  }

  // --- Secret create modal: toggle between the Opaque and Image Pull
  // Secret field groups (deployment_pods/secrets.html). Both forms' fields
  // are always present in the DOM (different prefixes, no name collisions)
  // — this only controls which group is visible; the server decides which
  // one to actually validate based on the submitted `secret_kind` value. ---
  document.querySelectorAll(".secret-kind-radio").forEach((radio) => {
    radio.addEventListener("change", () => {
      const selected = document.querySelector(".secret-kind-radio:checked")?.value;
      document.querySelectorAll(".secret-kind-fields").forEach((group) => {
        group.classList.toggle("hidden", group.dataset.secretKind !== selected);
      });
    });
  });

  // --- Ingress create/edit modal: toggle between Form and Raw YAML editing
  // modes (deployment_pods/ingresses.html) — same shape as the Secret
  // opaque/pull toggle above, just per-modal-scoped since (unlike Secrets,
  // which only has one create-modal-wide toggle) an Ingress edit modal
  // exists once per row, each with its own independent mode radios. ---
  document.querySelectorAll(".ingress-mode-radio").forEach((radio) => {
    radio.addEventListener("change", () => {
      const scope = radio.closest("form");
      if (!scope) return;
      const selected = scope.querySelector(".ingress-mode-radio:checked")?.value;
      scope.querySelectorAll(".ingress-mode-fields").forEach((section) => {
        section.classList.toggle("hidden", section.dataset.ingressMode !== selected);
      });
    });
  });

  // --- Network Policy create/edit modal: Form/Raw YAML toggle (same shape
  // as the Ingress toggle above, kept as its own block rather than merged
  // with it — small, per-page-scoped widget duplication, same convention
  // this app already uses for setupMultiObjectPicker()/setupSearchDropdown()
  // across pages) plus the Ingress/Egress direction checkboxes, each of
  // which shows/hides its own peers+ports block. ---
  document.querySelectorAll(".netpol-mode-radio").forEach((radio) => {
    radio.addEventListener("change", () => {
      const scope = radio.closest("form");
      if (!scope) return;
      const selected = scope.querySelector(".netpol-mode-radio:checked")?.value;
      scope.querySelectorAll(".netpol-mode-fields").forEach((section) => {
        section.classList.toggle("hidden", section.dataset.netpolMode !== selected);
      });
    });
  });

  document.querySelectorAll(".netpol-direction-toggle").forEach((checkbox) => {
    const target = document.getElementById(checkbox.dataset.target);
    if (!target) return;
    checkbox.addEventListener("change", () => target.classList.toggle("hidden", !checkbox.checked));
  });

  // --- Raw YAML editors (Ingress create/edit "YAML" mode textareas) — same
  // CodeMirror wiring as deployment_manifests.js/deployment_servers.js/
  // yaml_generator.js. A no-op if this page didn't include CodeMirror
  // (window.YamlEditor undefined) or has no such textarea. ---
  if (window.YamlEditor) {
    document.querySelectorAll(".yaml-content-input").forEach((textarea) => {
      window.YamlEditor.initYamlEditor(textarea);
    });
  }

  // --- Dynamic key/value rows for Secret/ConfigMap create/edit (`entries`/
  // `new_entries` FieldList rows on secrets.html and configmaps.html). Each
  // "add" trigger points at a <template> (its markup has literal
  // "__INDEX__" placeholders standing in for the WTForms FieldList index)
  // and the row container to append into; each container tracks the next
  // index to use in `data-next-index` so a failed validation re-render
  // (which may already have several bound rows) keeps numbering correctly
  // instead of colliding with existing ones.
  //
  // addEntryRow is shared by the manual "+ Add Another Key" button and the
  // file-import/paste-as-text features below — all three just add rows to
  // the same container the same way, only the source of the key/value
  // differs (blank, a parsed file, or parsed pasted text).
  function addEntryRow(container, template, presetKey, presetValue) {
    const index = parseInt(container.dataset.nextIndex || "0", 10);
    const html = template.innerHTML.replaceAll("__INDEX__", String(index));
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html.trim();
    const row = wrapper.firstElementChild;

    if (presetKey !== undefined) {
      const keyInput = row.querySelector('input[name$="-key"]');
      if (keyInput) keyInput.value = presetKey;
    }
    if (presetValue !== undefined) {
      const valueInput = row.querySelector('textarea[name$="-value"]');
      if (valueInput) valueInput.value = presetValue;
    }

    container.appendChild(row);
    container.dataset.nextIndex = String(index + 1);
    return row;
  }

  document.querySelectorAll(".add-entry-row-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const container = document.getElementById(button.dataset.container);
      const template = document.getElementById(button.dataset.template);
      if (!container || !template) return;
      addEntryRow(container, template);
    });
  });

  // Delegated (rows are added dynamically after page load) — removes
  // whichever row's Remove button was clicked, whether it was rendered by
  // the server or added by any of the "add row" mechanisms above.
  document.querySelectorAll(".secret-entries").forEach((container) => {
    container.addEventListener("click", (event) => {
      const removeButton = event.target.closest(".remove-entry-row-btn");
      if (!removeButton) return;
      removeButton.closest(".secret-entry-row")?.remove();
    });
  });

  // --- Import from file / paste-as-text -> convert into the same key/value
  // fields above. Both are pure client-side convenience: they only
  // pre-fill the normal form fields before submit, nothing is uploaded or
  // parsed server-side.
  //
  // parseKeyValueText mirrors a .env file: one `KEY=VALUE` per line, blank
  // lines and `#`-comments ignored, one optional layer of surrounding
  // quotes stripped from the value (a common .env convention).
  function parseKeyValueText(text) {
    const entries = [];
    text.split(/\r?\n/).forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) return;
      const eq = trimmed.indexOf("=");
      if (eq === -1) return;
      const key = trimmed.slice(0, eq).trim();
      let value = trimmed.slice(eq + 1).trim();
      if (
        (value.startsWith('"') && value.endsWith('"') && value.length >= 2) ||
        (value.startsWith("'") && value.endsWith("'") && value.length >= 2)
      ) {
        value = value.slice(1, -1);
      }
      if (key) entries.push({ key, value });
    });
    return entries;
  }

  document.querySelectorAll(".secret-import-file-input").forEach((input) => {
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const container = document.getElementById(input.dataset.container);
      const template = document.getElementById(input.dataset.template);
      if (!container || !template) return;

      const reader = new FileReader();
      reader.onload = () => {
        const text = String(reader.result || "");
        const parsed = parseKeyValueText(text);
        if (parsed.length > 0) {
          // Looks like KEY=VALUE lines (e.g. a .env file) — one field per line.
          parsed.forEach((entry) => addEntryRow(container, template, entry.key, entry.value));
        } else {
          // Not KEY=VALUE formatted (e.g. a single cert/config blob) — import
          // the whole file as one entry keyed by its filename, matching
          // `kubectl create secret/configmap --from-file=path` semantics.
          const key = file.name.replace(/[^-._a-zA-Z0-9]/g, "_");
          addEntryRow(container, template, key, text);
        }
      };
      reader.readAsText(file);
      input.value = ""; // allow re-selecting the same file again later
    });
  });

  document.querySelectorAll(".secret-paste-toggle-btn").forEach((button) => {
    button.addEventListener("click", () => {
      document.getElementById(button.dataset.target)?.classList.toggle("hidden");
    });
  });

  document.querySelectorAll(".secret-paste-cancel-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const box = document.getElementById(button.dataset.target);
      if (!box) return;
      box.classList.add("hidden");
      const textarea = box.querySelector(".secret-paste-textarea");
      if (textarea) textarea.value = "";
    });
  });

  document.querySelectorAll(".secret-paste-apply-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const container = document.getElementById(button.dataset.container);
      const template = document.getElementById(button.dataset.template);
      const box = document.getElementById(button.dataset.target);
      const textarea = box ? box.querySelector(".secret-paste-textarea") : null;
      if (!container || !template || !textarea) return;

      parseKeyValueText(textarea.value).forEach((entry) => addEntryRow(container, template, entry.key, entry.value));

      textarea.value = "";
      box.classList.add("hidden");
    });
  });
});
