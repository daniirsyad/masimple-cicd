onPageReady(() => {
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
  // Each editor is built when its modal first opens (lazyYamlEditor): this
  // page has one per manifest's edit/view modal.
  const editorGetters = {}; // textarea id -> () => CodeMirror instance or null
  document.querySelectorAll(".yaml-content-input").forEach((textarea) => {
    // A disabled <fieldset> = the read-only View modal for users without deployment_manifest.manage.
    editorGetters[textarea.id] = window.YamlEditor.lazyYamlEditor(textarea, {
      readOnly: !!textarea.closest("fieldset[disabled]"),
    });
  });
  const editorFor = (textareaId) => editorGetters[textareaId]?.() || null;

  // --- Type switch: Kubernetes YAML vs. Helm chart. The Helm fields and
  // the "values" wording only show for Helm; the editor stays the same. ---
  document.querySelectorAll(".deployment-manifest-form").forEach((form) => {
    const sync = () => {
      const isHelm = form.querySelector(".manifest-type-input:checked")?.value === "helm";
      form.querySelectorAll(".helm-only").forEach((el) => el.classList.toggle("hidden", !isHelm));
      const label = form.querySelector(".yaml-content-label");
      if (label) label.textContent = isHelm ? "Values (values.yaml)" : "YAML";
    };
    form.querySelectorAll(".manifest-type-input").forEach((input) => input.addEventListener("change", sync));

    const syncChartSource = () => {
      const isUpload = form.querySelector(".chart-source-input:checked")?.value === "upload";
      form.querySelectorAll(".chart-repo-only").forEach((el) => el.classList.toggle("hidden", isUpload));
      form.querySelectorAll(".chart-upload-only").forEach((el) => el.classList.toggle("hidden", !isUpload));
    };
    form.querySelectorAll(".chart-source-input").forEach((input) => input.addEventListener("change", syncChartSource));
  });

  document.querySelectorAll(".helm-load-defaults-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const form = btn.closest("form");
      const status = form.querySelector(".helm-defaults-status");
      const field = (name) => form.querySelector(`[name$="${name}"]`)?.value.trim() || "";
      const cm = editorFor(btn.dataset.yamlInputId);
      if (cm && cm.getValue().trim() && !confirm("Replace what's in the editor with the chart's default values?")) return;

      setBusy(btn, true);
      showLoading(status, "Loading chart defaults...");
      status.classList.remove("text-error");
      try {
        // Multipart, so a chart file picked but not saved yet can go along.
        const body = new FormData();
        const source = form.querySelector(".chart-source-input:checked")?.value || "repo";
        body.append("source", source);
        body.append("manifest_id", btn.dataset.manifestId);
        if (source === "upload") {
          const file = form.querySelector(".helm-chart-file-input")?.files[0];
          if (file) body.append("chart_file", file);
          body.append("upload_version", form.querySelector(".helm-chart-upload-version")?.value || "");
        } else {
          body.append("repo", field("helm_chart_repo"));
          body.append("name", field("helm_chart_name"));
          body.append("version", field("helm_chart_version"));
          body.append("username", field("helm_repo_username"));
          body.append("password", form.querySelector('[name$="helm_repo_password"]')?.value || "");
        }
        const response = await fetch("/deployment-manifests/api/helm-default-values", {
          method: "POST",
          headers: { "X-CSRFToken": csrfToken },
          body,
        });
        const data = await response.json().catch(() => ({}));
        if (!data.ok) throw new Error(data.error || "Couldn't load the chart's default values.");
        if (cm) cm.setValue(data.values);
        status.textContent = "";
      } catch (err) {
        status.textContent = err.message;
        status.classList.add("text-error");
      } finally {
        setBusy(btn, false);
      }
    });
  });

  // "+ version": {{SYS:VERSION[:name]}} (full image) or {{SYS:TAG[:name]}}
  // (tag only) at the cursor, then a binding row for it.
  document.querySelectorAll(".yaml-insert-placeholder-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const cm = editorFor(btn.dataset.yamlInputId);
      const menu = btn.closest(".dropdown-content");
      const keyInput = menu.querySelector(".yaml-placeholder-key");
      const key = keyInput.value.trim();
      const invalid = key !== "" && !/^[A-Za-z0-9_.-]+$/.test(key);
      menu.querySelector(".yaml-placeholder-key-error").classList.toggle("hidden", !invalid);
      if (!cm || invalid) return;
      cm.replaceSelection(`{{SYS:${btn.dataset.kind}${key ? `:${key}` : ""}}}`);
      keyInput.value = "";
      document.activeElement?.blur(); // closes the dropdown
      cm.focus();
      scanPlaceholders(btn.closest("form"));
    });
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
    row.querySelector(".binding-key-label").textContent = key === "default" ? "(unnamed)" : key;
    row.querySelector(".binding-key-label").title =
      key === "default" ? "{{SYS:VERSION}} / {{SYS:TAG}}" : `{{SYS:VERSION:${key}}} / {{SYS:TAG:${key}}}`;

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

  // The editor's live content: the textarea only catches up on change.
  function yamlOf(form) {
    const textarea = form.querySelector(".yaml-content-input");
    const editor = textarea && editorFor(textarea.id);
    return editor ? editor.getValue() : textarea?.value || "";
  }

  // One binding row per placeholder name in the YAML. seedByKey preselects
  // a Builder on rows it adds.
  function scanPlaceholders(form, seedByKey) {
    const container = form?.querySelector(".bindings-rows");
    if (!container) return Promise.resolve();
    return fetch("/deployment-manifests/api/placeholder-keys", {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken, "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ yaml_content: yamlOf(form) }),
    })
      .then((r) => r.json())
      .then((data) => syncBindingRows(container, data.keys || [], seedByKey))
      .catch(() => {});
  }

  document.querySelectorAll(".scan-placeholders-btn").forEach((btn) => {
    btn.addEventListener("click", () => scanPlaceholders(btn.closest("form")));
  });

  // --- Find images: list each image in the YAML with a name and a Builder,
  // then turn the ticked ones into placeholders (the server edits the
  // text by position, so comments and layout stay as they were). ---
  // From the binding row <template>: its content isn't part of the page, so
  // a page-wide querySelectorAll can't see it.
  const builderOptions = Array.from(
    document.getElementById("binding-row-template")?.content.querySelectorAll(".binding-builder-select option") || []
  )
    .filter((option) => option.value)
    .map((option) => ({ id: option.value, name: option.textContent.trim() }));

  function findImagesRow(image) {
    const row = document.createElement("div");
    row.className = "find-images-row grid grid-cols-1 items-center gap-2 rounded-lg bg-base-100 p-2 sm:grid-cols-[auto_1fr_8rem_10rem]";
    row.dataset.id = image.id;

    const use = document.createElement("input");
    use.type = "checkbox";
    use.className = "find-images-use checkbox checkbox-primary checkbox-sm";
    use.checked = true;
    use.setAttribute("aria-label", `Use ${image.path}`);

    const where = document.createElement("div");
    where.className = "min-w-0";
    const path = document.createElement("div");
    path.className = "truncate font-mono text-xs font-medium";
    path.textContent = image.path;
    const value = document.createElement("div");
    value.className = "truncate text-xs text-base-content/60";
    value.textContent = `${image.kind === "tag" ? "Tag only" : "Full image"}, now ${image.value === "" ? "empty" : image.value}`;
    where.append(path, value);

    const key = document.createElement("input");
    key.type = "text";
    key.className = "find-images-key input input-bordered input-xs w-full font-mono";
    key.value = image.key;
    key.setAttribute("aria-label", "Placeholder name");

    const builder = document.createElement("select");
    builder.className = "find-images-builder select select-bordered select-xs w-full";
    builder.setAttribute("aria-label", "Builder");
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "Pick a Builder later";
    builder.append(none);
    builderOptions.forEach(({ id, name }) => {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = name;
      option.selected = id === image.builder_id;
      builder.append(option);
    });

    row.append(use, where, key, builder);
    return row;
  }

  document.querySelectorAll(".find-images-btn").forEach((btn) => {
    const form = btn.closest("form");
    const panel = form.querySelector(".find-images-panel");
    const list = panel.querySelector(".find-images-list");
    const applyBtn = panel.querySelector(".find-images-apply");
    const post = (url, params) =>
      fetch(url, {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken, "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams(params),
      }).then((r) => r.json());

    btn.addEventListener("click", () => {
      panel.classList.remove("hidden");
      applyBtn.classList.add("hidden");
      showLoading(list, "Looking for images...");
      post("/deployment-manifests/api/find-images", { yaml_content: yamlOf(form) })
        .then((data) => {
          list.replaceChildren();
          if (data.error) list.textContent = data.error;
          else if (!data.images.length) list.textContent = "No images left to turn into placeholders: every image: and image.tag already has one, or there are none.";
          else {
            const intro = document.createElement("p");
            intro.className = "text-xs text-base-content/60";
            intro.textContent = "Each ticked image gets a placeholder with this name, bound to the Builder you pick. For a chart's image.tag, image.repository is set to that Builder's image too.";
            list.append(intro, ...data.images.map(findImagesRow));
            applyBtn.classList.remove("hidden");
          }
        })
        .catch(() => {
          list.textContent = "Couldn't read the YAML. Try again.";
        });
    });

    panel.querySelector(".find-images-cancel").addEventListener("click", () => panel.classList.add("hidden"));

    applyBtn.addEventListener("click", () => {
      const choices = Array.from(list.querySelectorAll(".find-images-row"))
        .filter((row) => row.querySelector(".find-images-use").checked)
        .map((row) => ({
          id: Number(row.dataset.id),
          key: row.querySelector(".find-images-key").value.trim(),
          builder_id: row.querySelector(".find-images-builder").value,
        }));
      if (!choices.length) return;
      setBusy(applyBtn, true);
      post("/deployment-manifests/api/apply-images", { yaml_content: yamlOf(form), choices: JSON.stringify(choices) })
        .then((data) => {
          if (data.error) {
            list.textContent = data.error;
            return;
          }
          const textarea = form.querySelector(".yaml-content-input");
          const editor = editorFor(textarea.id);
          if (editor) editor.setValue(data.yaml_content);
          else textarea.value = data.yaml_content;
          const seeds = {};
          data.bindings.forEach((binding) => {
            if (binding.builder_id) seeds[binding.key] = { builder_id: binding.builder_id, pinned_image_build_id: "" };
          });
          panel.classList.add("hidden");
          return scanPlaceholders(form, seeds);
        })
        .catch(() => {
          list.textContent = "Couldn't change the YAML. Try again.";
        })
        .finally(() => setBusy(applyBtn, false));
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
      showLoading(contentEl, "Resolving versions...");
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
      showLoading(contentEl, "Checking what's currently deployed...");
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

  // --- Detect on servers: a read-only check of what's already running,
  // then tick the servers to mark as deployed. Everything from the cluster
  // goes in as text, never HTML. ---
  const DETECT_STATES = {
    found: ["status-dot-success", "Found"],
    partial: ["status-dot-warning", "Partly found, so it can't be marked"],
    missing: ["", "Not found"],
    already: ["status-dot-success", "Already deployed by MASIMPLE CICD"],
    unhealthy: ["status-dot-warning", "Found, but not healthy"],
    unsupported: ["", "Can't check"],
    error: ["status-dot-error", "Couldn't check"],
  };
  const VERSION_STATES = {
    outside: "not a build of its Builder; Update replaces it",
    mixed: "different values in different places",
    "chart default": "not set, so the chart's default is used",
    unreadable: "couldn't read it",
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Yellow text is hard to read on white: a dot carries the warning instead.
  function warn(...parts) {
    const line = el("div", "flex items-baseline gap-1.5");
    const dot = el("span", "status-dot status-dot-warning shrink-0 translate-y-[-1px]");
    const text = el("span");
    text.append(...parts);
    line.append(dot, text);
    return line;
  }

  function detectServerBlock(manifestId, server, onToggle) {
    const block = el("div", "flex flex-col gap-1 border-t border-base-200 pt-2 first:border-t-0 first:pt-0");
    const [dotClass, word] = DETECT_STATES[server.state] || ["", server.state];
    const head = el("label", "flex items-center gap-2");
    if (server.state === "found") {
      const box = el("input", "checkbox checkbox-primary checkbox-sm");
      box.type = "checkbox";
      box.name = "pairs";
      box.value = `${manifestId}:${server.server_id}`;
      box.checked = true;
      box.setAttribute("form", "detect-trigger-form");
      box.addEventListener("change", onToggle);
      head.append(box);
    } else {
      head.append(el("span", `status-dot ${dotClass}`));
    }
    head.append(el("span", "font-medium", server.server_name), el("span", "text-base-content/60", word));
    block.append(head);

    const details = el("div", "flex flex-col gap-1 pl-6 text-xs text-base-content/70");
    if (server.message) details.append(el("div", "", server.message));
    if (server.found.length && server.state !== "already") details.append(el("div", "", `Running: ${server.found.join(", ")}`));
    if (server.missing.length) details.append(warn(`Missing: ${server.missing.join(", ")}`));
    server.versions.forEach((version) => {
      const name = el("span", "font-mono", version.key === "default" ? "version" : version.key);
      if (version.state === "build") {
        const line = el("div", "pl-3");
        line.append(name, `: ${version.label}`);
        details.append(line);
      } else {
        const parts = [name, ": "];
        if (version.value) parts.push(el("span", "font-mono", version.value), " ");
        parts.push(`(${VERSION_STATES[version.state] || version.state})`);
        details.append(warn(...parts));
      }
    });
    if (server.chart_note) details.append(warn(server.chart_note));
    if (server.diff_note) details.append(el("div", "", server.diff_note));
    if (server.diff === "") details.append(el("div", "", "Update would change nothing."));
    if (server.diff) {
      const more = el("details", "");
      more.append(el("summary", "cursor-pointer", "What Update would change"));
      more.append(el("pre", "mt-1 max-h-64 overflow-auto rounded bg-base-200 p-2 font-mono text-[0.7rem] leading-snug", server.diff));
      details.append(more);
    }
    if (details.childNodes.length) block.append(details);
    return block;
  }

  function setupDetectModal() {
    const modal = document.getElementById("detect-trigger-modal");
    if (!modal) return null;
    const contentEl = document.getElementById("detect-trigger-content");
    const submitBtn = document.getElementById("detect-trigger-submit");
    const syncSubmit = () => {
      submitBtn.disabled = !modal.querySelector('input[name="pairs"]:checked');
    };

    return (manifestIds) => {
      showLoading(contentEl, "Checking the servers. This reads from each one and can take a few seconds...");
      submitBtn.disabled = true;
      modal.showModal();

      const body = new URLSearchParams();
      manifestIds.forEach((id) => body.append("manifest_ids", id));
      fetch("/deployment-manifests/api/detect-preview", {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken, "Content-Type": "application/x-www-form-urlencoded" },
        body,
      })
        .then((r) => {
          if (!r.ok) throw new Error();
          return r.json();
        })
        .then((data) => {
          contentEl.replaceChildren();
          (data.results || []).forEach((entry) => {
            const card = el("div", "flex flex-col gap-2 rounded-box border border-base-300 p-3");
            card.append(el("div", "font-semibold", entry.name));
            if (!entry.servers.length) card.append(el("div", "text-xs text-base-content/60", "No target servers."));
            entry.servers.forEach((server) => card.append(detectServerBlock(entry.manifest_id, server, syncSubmit)));
            contentEl.append(card);
          });
          syncSubmit();
        })
        .catch(() => {
          contentEl.textContent = "Couldn't check the servers. Try again.";
        });
    };
  }

  wireOneAndGroupButtons(".detect-one-btn", ".detect-group-btn", setupDetectModal());

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
            if (!response.ok) {
              const data = await response.json().catch(() => ({}));
              throw new Error(data.error || "Failed to save the new order.");
            }
          } catch (err) {
            alert(err.message);
            // Reload so the table falls back to the last-saved order rather
            // than silently drifting from what the server actually has.
            refreshPage();
          }
        },
      });
    });
  }
});
