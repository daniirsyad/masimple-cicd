document.addEventListener("DOMContentLoaded", () => {
  const csrfToken = document.getElementById("deployment-manifests-page")?.dataset.csrf;

  function parseJsonData(elementId) {
    const dataEl = document.getElementById(elementId);
    if (!dataEl) return null;
    try {
      return JSON.parse(dataEl.textContent);
    } catch (err) {
      return null;
    }
  }

  const builderSuccessfulBuilds = parseJsonData("builder-successful-builds-data") || {};

  // --- YAML editor (CodeMirror) for the manifest yaml_content field —
  // progressive enhancement over the plain <textarea> the server actually
  // reads on submit (WTForms/Flask never see CodeMirror itself, only the
  // textarea it wraps). Falls back to the plain textarea untouched if the
  // vendored script failed to load. Shared init lives in yaml_editor.js
  // (used here, by deployment_servers.js, and by yaml_generator.js). ---
  const yamlEditors = {}; // textarea id -> CodeMirror instance
  document.querySelectorAll(".yaml-content-input").forEach((textarea) => {
    const cm = window.YamlEditor.initYamlEditor(textarea);
    if (cm) yamlEditors[textarea.id] = cm;
  });

  // --- Searchable dropdown for the Group field — same pattern as builders.js. ---
  function setupSearchDropdown(input, dropdown, suggestions) {
    if (!input || !dropdown) return;

    function renderOptions() {
      const query = input.value.trim().toLowerCase();
      const matches = query ? suggestions.filter((value) => value.toLowerCase().includes(query)) : suggestions;

      dropdown.innerHTML = "";
      if (matches.length === 0) {
        dropdown.classList.add("hidden");
        return;
      }

      matches.forEach((value) => {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.textContent = value;
        a.addEventListener("mousedown", (event) => {
          event.preventDefault();
          input.value = value;
          dropdown.classList.add("hidden");
        });
        li.appendChild(a);
        dropdown.appendChild(li);
      });
      dropdown.classList.remove("hidden");
    }

    input.addEventListener("focus", renderOptions);
    input.addEventListener("input", renderOptions);
    input.addEventListener("blur", () => dropdown.classList.add("hidden"));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") dropdown.classList.add("hidden");
    });
  }

  const groupNameSuggestions = parseJsonData("group-name-suggestions-data") || [];
  document.querySelectorAll(".group-name-input").forEach((input) => {
    setupSearchDropdown(input, input.parentElement.querySelector(".group-name-dropdown"), groupNameSuggestions);
  });

  // --- Version binding rows: one per {{SYS:VERSION[:key]}} placeholder found
  // in the manifest's YAML, each picking a Builder (+ optional pinned build)
  // to resolve against. Rows are cloned from #binding-row-template (whose
  // Builder <option>s are the same for every row/form) and kept in sync with
  // whatever placeholders are actually in the YAML via the "Scan" button —
  // see app/services/deployment/resolver.py for the server-side counterpart
  // of the {{SYS:VERSION[:key]}} pattern this mirrors. ---
  function populatePinnedOptions(pinnedSelect, builderId, selectedId) {
    pinnedSelect.innerHTML = "";
    const defaultOption = document.createElement("option");
    defaultOption.value = "";
    defaultOption.textContent = "Latest successful build";
    pinnedSelect.appendChild(defaultOption);

    (builderSuccessfulBuilds[builderId] || []).forEach((build) => {
      const option = document.createElement("option");
      option.value = build.id;
      option.textContent = `${build.tag} (${build.created_at})`;
      if (build.id === selectedId) option.selected = true;
      pinnedSelect.appendChild(option);
    });
  }

  function createBindingRow(key) {
    const template = document.getElementById("binding-row-template");
    const row = template.content.firstElementChild.cloneNode(true);
    row.dataset.key = key;
    row.querySelector(".binding-key-input").value = key;
    row.querySelector(".binding-key-label").textContent = key === "default" ? "{{SYS:VERSION}}" : `{{SYS:VERSION:${key}}}`;

    const builderSelect = row.querySelector(".binding-builder-select");
    const pinnedSelect = row.querySelector(".binding-pinned-select");
    builderSelect.addEventListener("change", () => populatePinnedOptions(pinnedSelect, builderSelect.value, ""));

    return row;
  }

  function syncBindingRows(container, keys, seedByKey) {
    const existingRows = {};
    container.querySelectorAll(".binding-row").forEach((row) => {
      existingRows[row.dataset.key] = row;
    });

    Object.keys(existingRows).forEach((key) => {
      if (!keys.includes(key)) existingRows[key].remove();
    });

    keys.forEach((key) => {
      if (existingRows[key]) return;
      const row = createBindingRow(key);
      container.appendChild(row);

      const seed = (seedByKey || {})[key];
      if (seed) {
        const builderSelect = row.querySelector(".binding-builder-select");
        const pinnedSelect = row.querySelector(".binding-pinned-select");
        builderSelect.value = seed.builder_id;
        populatePinnedOptions(pinnedSelect, seed.builder_id, seed.pinned_image_build_id);
      }
    });
  }

  // Seed each form's bindings from its manifest's existing bindings on load
  // (edit forms only — the create form starts empty until a scan).
  document.querySelectorAll(".bindings-rows").forEach((container) => {
    const dataId = container.dataset.existingBindingsId;
    if (!dataId) return;
    const existing = parseJsonData(dataId) || [];
    const seedByKey = {};
    const keys = [];
    existing.forEach((binding) => {
      keys.push(binding.key);
      seedByKey[binding.key] = binding;
    });
    syncBindingRows(container, keys, seedByKey);
  });

  document.querySelectorAll(".scan-placeholders-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const form = btn.closest("form");
      const container = form.querySelector(".bindings-rows");
      if (!container) return;

      // The CodeMirror-editor's live content, not the underlying textarea's
      // .value — that only reflects the editor once .save() runs (on
      // submit), so reading it here would scan stale/pre-edit YAML.
      const editor = yamlEditors[btn.dataset.yamlInputId];
      const yamlContent = editor ? editor.getValue() : document.getElementById(btn.dataset.yamlInputId)?.value || "";

      fetch("/deployment-manifests/api/placeholder-keys", {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken, "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ yaml_content: yamlContent }),
      })
        .then((r) => r.json())
        .then((data) => syncBindingRows(container, data.keys || []))
        .catch(() => {});
    });
  });

  // --- Deploy/Update trigger: resolve-and-confirm, showing resolved
  // versions before actually applying. Update reuses the exact same
  // preview endpoint/shape as Deploy — it's the same operation, just
  // surfaced under a different button/permission. ---
  function setupPreviewTriggerModal({ modalId, contentId, submitId, idsContainerId }) {
    const modal = document.getElementById(modalId);
    if (!modal) return null;

    function open(manifestIds) {
      const idsContainer = document.getElementById(idsContainerId);
      idsContainer.innerHTML = "";
      manifestIds.forEach((id) => {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "manifest_ids";
        input.value = id;
        idsContainer.appendChild(input);
      });

      const contentEl = document.getElementById(contentId);
      const submitBtn = document.getElementById(submitId);
      contentEl.textContent = "Resolving versions...";
      submitBtn.disabled = true;

      modal.showModal();

      const body = new URLSearchParams();
      manifestIds.forEach((id) => body.append("manifest_ids", id));

      fetch("/deployment-manifests/api/preview", {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken, "Content-Type": "application/x-www-form-urlencoded" },
        body,
      })
        .then((r) => r.json())
        .then((data) => {
          const previews = data.previews || [];
          contentEl.innerHTML = "";
          let hasError = previews.length === 0;

          previews.forEach((entry) => {
            const block = document.createElement("div");
            block.className = "rounded border border-base-300 p-2";

            const title = document.createElement("div");
            title.className = "font-semibold";
            title.textContent = entry.name;
            block.appendChild(title);

            if (entry.error) {
              hasError = true;
              const errEl = document.createElement("div");
              errEl.className = "text-error text-xs";
              errEl.textContent = entry.error;
              block.appendChild(errEl);
            } else {
              const versionsEl = document.createElement("div");
              versionsEl.className = "text-xs";
              Object.entries(entry.resolved_versions).forEach(([key, tag]) => {
                const line = document.createElement("div");
                const code = document.createElement("code");
                code.textContent = tag;
                line.append(`${key} → `, code);
                versionsEl.appendChild(line);
              });
              block.appendChild(versionsEl);

              const serversEl = document.createElement("div");
              serversEl.className = "text-xs text-base-content/60";
              serversEl.textContent = entry.servers.length
                ? `Servers: ${entry.servers.join(", ")}`
                : "No target servers configured";
              block.appendChild(serversEl);
              if (!entry.servers.length) hasError = true;
            }

            contentEl.appendChild(block);
          });

          submitBtn.disabled = hasError;
        })
        .catch(() => {
          contentEl.textContent = "Failed to resolve versions.";
        });
    }

    return open;
  }

  // --- Stop/Restart trigger: reports which (manifest, server) pairs are
  // actually currently live rather than resolved versions, and disables
  // submit entirely if nothing would happen. Restart reuses the exact same
  // stop-preview endpoint/shape — "currently deployed" is the same
  // precondition for either action. ---
  function setupTeardownTriggerModal({ modalId, contentId, submitId, idsContainerId, previewUrl, nothingText, willText }) {
    const modal = document.getElementById(modalId);
    if (!modal) return null;

    function open(manifestIds) {
      const idsContainer = document.getElementById(idsContainerId);
      idsContainer.innerHTML = "";
      manifestIds.forEach((id) => {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "manifest_ids";
        input.value = id;
        idsContainer.appendChild(input);
      });

      const contentEl = document.getElementById(contentId);
      const submitBtn = document.getElementById(submitId);
      contentEl.textContent = "Checking what's currently deployed...";
      submitBtn.disabled = true;

      modal.showModal();

      const body = new URLSearchParams();
      manifestIds.forEach((id) => body.append("manifest_ids", id));

      fetch(previewUrl, {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken, "Content-Type": "application/x-www-form-urlencoded" },
        body,
      })
        .then((r) => r.json())
        .then((data) => {
          const previews = data.previews || [];
          contentEl.innerHTML = "";
          let hasAnyLive = false;

          previews.forEach((entry) => {
            const block = document.createElement("div");
            block.className = "rounded border border-base-300 p-2";

            const title = document.createElement("div");
            title.className = "font-semibold";
            title.textContent = entry.name;
            block.appendChild(title);

            const serversEl = document.createElement("div");
            serversEl.className = "text-xs text-base-content/60";
            if (entry.servers.length) {
              hasAnyLive = true;
              serversEl.textContent = `${willText}: ${entry.servers.join(", ")}`;
            } else {
              serversEl.textContent = nothingText;
            }
            block.appendChild(serversEl);

            contentEl.appendChild(block);
          });

          if (!previews.length) contentEl.textContent = "Nothing currently deployed.";
          submitBtn.disabled = !hasAnyLive;
        })
        .catch(() => {
          contentEl.textContent = "Failed to check current deployment status.";
        });
    }

    return open;
  }

  function wireOneAndGroupButtons(oneSelector, groupSelector, openModal) {
    if (!openModal) return;
    document.querySelectorAll(oneSelector).forEach((btn) => {
      btn.addEventListener("click", () => openModal([btn.dataset.manifestId]));
    });
    document.querySelectorAll(groupSelector).forEach((btn) => {
      btn.addEventListener("click", () => {
        const group = btn.closest(".manifest-group");
        const ids = Array.from(group.querySelectorAll(".manifest-row")).map((row) => row.dataset.manifestId);
        openModal(ids);
      });
    });
  }

  wireOneAndGroupButtons(
    ".deploy-one-btn",
    ".deploy-group-btn",
    setupPreviewTriggerModal({
      modalId: "deploy-trigger-modal",
      contentId: "deploy-trigger-content",
      submitId: "deploy-trigger-submit",
      idsContainerId: "deploy-trigger-manifest-ids",
    })
  );

  wireOneAndGroupButtons(
    ".update-one-btn",
    ".update-group-btn",
    setupPreviewTriggerModal({
      modalId: "update-trigger-modal",
      contentId: "update-trigger-content",
      submitId: "update-trigger-submit",
      idsContainerId: "update-trigger-manifest-ids",
    })
  );

  wireOneAndGroupButtons(
    ".stop-one-btn",
    ".stop-group-btn",
    setupTeardownTriggerModal({
      modalId: "stop-trigger-modal",
      contentId: "stop-trigger-content",
      submitId: "stop-trigger-submit",
      idsContainerId: "stop-trigger-manifest-ids",
      previewUrl: "/deployment-manifests/api/stop-preview",
      nothingText: "Not currently deployed anywhere — nothing to stop.",
      willText: "Will stop on",
    })
  );

  wireOneAndGroupButtons(
    ".restart-one-btn",
    ".restart-group-btn",
    setupTeardownTriggerModal({
      modalId: "restart-trigger-modal",
      contentId: "restart-trigger-content",
      submitId: "restart-trigger-submit",
      idsContainerId: "restart-trigger-manifest-ids",
      previewUrl: "/deployment-manifests/api/restart-preview",
      nothingText: "Not currently deployed anywhere — nothing to restart.",
      willText: "Will restart on",
    })
  );

  // --- Drag-and-drop manifest ordering within a group — same Sortable
  // pattern as menu-reorder.js, one flat list per group (no nesting). ---
  if (typeof Sortable !== "undefined") {
    document.querySelectorAll(".manifest-group-body").forEach((tbody) => {
      const reorderUrl = tbody.dataset.reorderUrl;
      const groupName = tbody.closest(".manifest-group")?.dataset.groupName;

      new Sortable(tbody, {
        handle: ".drag-handle",
        animation: 150,
        onEnd: async () => {
          const manifestIds = Array.from(tbody.querySelectorAll(".manifest-row")).map((row) => row.dataset.manifestId);
          try {
            const response = await fetch(reorderUrl, {
              method: "POST",
              headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
              body: JSON.stringify({ group_name: groupName, manifest_ids: manifestIds }),
            });
            if (!response.ok) throw new Error("Failed to save the new order.");
          } catch (err) {
            // Reload so the table falls back to the last-saved order rather
            // than silently drifting from what the server actually has.
            location.reload();
          }
        },
      });
    });
  }
});
