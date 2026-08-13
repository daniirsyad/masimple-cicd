document.addEventListener("DOMContentLoaded", () => {
  // --- Repo -> branch/dockerfile cascading (create + each edit form) ---
  document.querySelectorAll(".builder-form").forEach((form) => {
    const repoSelect = form.querySelector(".repo-select");
    const branchSelect = form.querySelector(".branch-select");
    const dockerfileDatalist = form.querySelector(".dockerfile-datalist");
    if (!repoSelect) return;

    function loadRepoInfo() {
      const repoId = repoSelect.value;
      if (!repoId) return;
      fetch(`/builders/api/repo-info/${repoId}`)
        .then((r) => r.json())
        .then((data) => {
          branchSelect.innerHTML = "";
          (data.branches || []).forEach((branch) => {
            const option = document.createElement("option");
            option.value = branch;
            option.textContent = branch;
            if (branch === data.default_branch) option.selected = true;
            branchSelect.appendChild(option);
          });

          if (dockerfileDatalist) {
            dockerfileDatalist.innerHTML = "";
            (data.dockerfiles || []).forEach((path) => {
              const option = document.createElement("option");
              option.value = path;
              dockerfileDatalist.appendChild(option);
            });
          }
        });
    }

    repoSelect.addEventListener("change", loadRepoInfo);

    // The create form's branch select starts with zero server-rendered
    // options (unlike edit forms, which are pre-populated from the saved
    // Builder) — it only ever gets populated by the "change" handler above.
    // That's a problem when there's exactly one repository to choose from:
    // the browser auto-selects the sole <option> and never fires "change"
    // (the user never has to change anything), so the branch select would
    // otherwise stay permanently empty and unusable. Load eagerly whenever
    // there's a selected repo but no branches loaded yet.
    if (repoSelect.value && branchSelect.options.length === 0) {
      loadRepoInfo();
    }
  });

  // --- Dockerfile source toggle (create + each edit form): shows either
  // the repo-path field or the managed-Dockerfile picker depending on the
  // select, never both — matches which one BuilderForm/_apply_dockerfile_source
  // actually reads server-side. ---
  document.querySelectorAll(".builder-form").forEach((form) => {
    const sourceSelect = form.querySelector(".dockerfile-source-select");
    const repoFields = form.querySelector(".dockerfile-repo-fields");
    const managedFields = form.querySelector(".dockerfile-managed-fields");
    if (!sourceSelect || !repoFields || !managedFields) return;

    function syncVisibility() {
      const isManaged = sourceSelect.value === "managed";
      repoFields.classList.toggle("hidden", isManaged);
      managedFields.classList.toggle("hidden", !isManaged);
    }

    sourceSelect.addEventListener("change", syncVisibility);
    syncVisibility();
  });

  // --- Build args repeatable rows (one rows-container + add-button pair per form) ---
  document.querySelectorAll(".add-build-arg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const container = document.getElementById(btn.dataset.rowsFor);
      const row = document.createElement("div");
      row.className = "flex items-center gap-2";
      row.innerHTML =
        '<input type="text" name="build_arg_key" placeholder="KEY" class="input input-bordered input-sm w-1/3" />' +
        '<input type="text" name="build_arg_value" placeholder="value" class="input input-bordered input-sm flex-1" />' +
        '<button type="button" class="btn btn-sm btn-ghost">&times;</button>';
      row.querySelector("button").addEventListener("click", () => row.remove());
      container.appendChild(row);
    });
  });

  // --- Searchable dropdown of previously-used values, layered on top of a
  // plain text input — typing a brand-new value is always still accepted on
  // submit, this only adds a filtered "history" list on top. Shared by the
  // Object field (build-trigger modal, one instance) and every Group field
  // (create form + one per Builder's edit form, several instances). ---
  function parseSuggestionsData(elementId) {
    const dataEl = document.getElementById(elementId);
    if (!dataEl) return [];
    try {
      return JSON.parse(dataEl.textContent);
    } catch (err) {
      return [];
    }
  }

  function setupSearchDropdown(input, dropdown, suggestions) {
    if (!input || !dropdown) return;

    function renderOptions() {
      const query = input.value.trim().toLowerCase();
      const matches = query
        ? suggestions.filter((value) => value.toLowerCase().includes(query))
        : suggestions;

      dropdown.innerHTML = "";
      if (matches.length === 0) {
        dropdown.classList.add("hidden");
        return;
      }

      matches.forEach((value) => {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.textContent = value;
        // mousedown (not click) fires before the input's own "blur" handler
        // below, so the dropdown doesn't hide itself out from under the click.
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

  // --- Multi-select "Object" picker: same search-dropdown UX as above, but
  // accumulates several picks as removable pills instead of overwriting a
  // single value, and lets an unmatched typed value be added as a new
  // Object (get-or-created server-side in Object.resolve()) rather than
  // only ever picking from the existing list. ---
  function setupMultiObjectPicker(input, dropdown, pillsContainer, hiddenContainer, suggestions) {
    if (!input || !dropdown || !pillsContainer || !hiddenContainer) return;

    const selected = []; // {kind: "existing"|"new", id, name}

    // This field lives inside a daisyUI modal (.modal-box has its own
    // overflow-y: auto, and the outer <dialog class="modal"> clips with
    // overflow-y: hidden) — an absolutely-positioned dropdown nested inside
    // either gets clipped or forces the whole modal to scroll just to reveal
    // it. Portaling the dropdown to be a direct child of the <dialog> itself
    // (sibling of .modal-box, not a descendant of it) sidesteps both: it
    // stays inside the dialog's own top-layer stacking (so it still paints
    // above the backdrop, unlike a portal to document.body would once the
    // dialog is open), and position: fixed is computed straight from the
    // field's own bounding rect so it never depends on modal-box's overflow.
    const anchor = input.closest(".relative") || input;
    const dialog = input.closest("dialog");
    (dialog || document.body).appendChild(dropdown);
    dropdown.classList.remove("absolute");
    dropdown.style.position = "fixed";
    dropdown.style.zIndex = "10";

    let currentOptions = []; // [{kind: "existing"|"new", id, name, apply()}]
    let highlightedIndex = -1;

    function isSelected(name) {
      return selected.some((item) => item.name.toLowerCase() === name.toLowerCase());
    }

    function renderPills() {
      pillsContainer.innerHTML = "";
      hiddenContainer.innerHTML = "";
      selected.forEach((item, index) => {
        const pill = document.createElement("span");
        pill.className = `badge badge-sm gap-1 ${item.kind === "new" ? "badge-secondary" : "badge-primary"}`;
        pill.append(document.createTextNode(item.name + (item.kind === "new" ? " (new)" : "")));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.className = "ml-1";
        remove.addEventListener("click", () => {
          selected.splice(index, 1);
          renderPills();
        });
        pill.appendChild(remove);
        pillsContainer.appendChild(pill);

        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = item.kind === "existing" ? "object_ids" : "new_object_names";
        hidden.value = item.kind === "existing" ? item.id : item.name;
        hiddenContainer.appendChild(hidden);
      });
    }

    function addExisting(obj) {
      if (isSelected(obj.name)) return;
      selected.push({ kind: "existing", id: obj.id, name: obj.name });
      renderPills();
    }

    function addNew(name) {
      name = name.trim();
      if (!name || isSelected(name)) return;
      // Case-insensitive exact match to an existing suggestion is treated as
      // that existing Object, not a new near-duplicate.
      const match = suggestions.find((s) => s.name.toLowerCase() === name.toLowerCase());
      if (match) {
        addExisting(match);
        return;
      }
      selected.push({ kind: "new", id: null, name });
      renderPills();
    }

    function positionDropdown() {
      const rect = anchor.getBoundingClientRect();
      dropdown.style.top = `${rect.bottom + 4}px`;
      dropdown.style.left = `${rect.left}px`;
      dropdown.style.width = `${rect.width}px`;
    }

    function applyHighlight() {
      dropdown.querySelectorAll("[data-picker-option]").forEach((el, i) => {
        el.classList.toggle("active", i === highlightedIndex);
        el.classList.toggle("bg-base-200", i === highlightedIndex);
        if (i === highlightedIndex) el.scrollIntoView({ block: "nearest" });
      });
    }

    function renderOptions() {
      const query = input.value.trim().toLowerCase();
      const matches = suggestions.filter(
        (s) => !isSelected(s.name) && (!query || s.name.toLowerCase().includes(query))
      );

      currentOptions = matches.map((s) => ({ kind: "existing", id: s.id, name: s.name, apply: () => addExisting(s) }));

      const trimmed = input.value.trim();
      const exactMatch = suggestions.some((s) => s.name.toLowerCase() === trimmed.toLowerCase());
      const canAddNew = trimmed && !exactMatch && !isSelected(trimmed);
      if (canAddNew) {
        currentOptions.push({ kind: "new", name: trimmed, apply: () => addNew(trimmed) });
      }

      dropdown.innerHTML = currentOptions
        .map(
          (opt, i) =>
            `<li><a data-picker-option data-index="${i}">${
              opt.kind === "new" ? `+ Add &quot;${opt.name.replace(/"/g, "&quot;")}&quot; as new` : opt.name
            }</a></li>`
        )
        .join("");

      if (currentOptions.length === 0) {
        dropdown.classList.add("hidden");
        highlightedIndex = -1;
        return;
      }

      highlightedIndex = 0;
      applyHighlight();
      positionDropdown();
      dropdown.classList.remove("hidden");
    }

    function selectHighlighted() {
      const opt = currentOptions[highlightedIndex];
      if (!opt) return;
      opt.apply();
      input.value = "";
      renderOptions();
    }

    // mousedown (not click) fires before the input's "blur" handler below,
    // and preventDefault stops that blur from happening at all — so
    // renderOptions() below runs with the input still focused, keeping the
    // dropdown open (now excluding this pick) instead of it closing and
    // needing a fresh click into the input to pick a second Object.
    dropdown.addEventListener("mousedown", (event) => {
      const option = event.target.closest("[data-picker-option]");
      if (!option) return;
      event.preventDefault();
      highlightedIndex = Number(option.dataset.index);
      selectHighlighted();
    });

    input.addEventListener("focus", renderOptions);
    input.addEventListener("input", renderOptions);
    input.addEventListener("blur", () => dropdown.classList.add("hidden"));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        dropdown.classList.add("hidden");
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        if (dropdown.classList.contains("hidden")) {
          renderOptions();
          return;
        }
        if (currentOptions.length === 0) return;
        highlightedIndex = (highlightedIndex + 1) % currentOptions.length;
        applyHighlight();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        if (dropdown.classList.contains("hidden")) {
          renderOptions();
          return;
        }
        if (currentOptions.length === 0) return;
        highlightedIndex = (highlightedIndex - 1 + currentOptions.length) % currentOptions.length;
        applyHighlight();
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (!dropdown.classList.contains("hidden") && currentOptions[highlightedIndex]) {
          selectHighlighted();
        } else {
          const trimmed = input.value.trim();
          if (!trimmed) return;
          const match = suggestions.find((s) => s.name.toLowerCase() === trimmed.toLowerCase());
          if (match) addExisting(match);
          else addNew(trimmed);
          input.value = "";
          dropdown.classList.add("hidden");
        }
      }
    });

    window.addEventListener(
      "scroll",
      () => {
        if (!dropdown.classList.contains("hidden")) positionDropdown();
      },
      true
    );
    window.addEventListener("resize", () => {
      if (!dropdown.classList.contains("hidden")) positionDropdown();
    });

    renderPills();

    return {
      addExisting,
      addNew,
      clear: () => {
        selected.length = 0;
        renderPills();
      },
    };
  }

  const objectPicker = setupMultiObjectPicker(
    document.getElementById("build-trigger-object-input"),
    document.getElementById("build-trigger-object-dropdown"),
    document.getElementById("build-trigger-object-pills"),
    document.getElementById("build-trigger-object-hidden"),
    parseSuggestionsData("build-trigger-object-suggestions-data")
  );

  // --- "Preview from Git": reads every commit since each selected
  // Builder's last successful build and pre-fills Bump Type/Object(s)/
  // Change Type/Additional Description from them — a heuristic Bump Type
  // guess plus an AI-assisted draft for the rest (see builders.routes.
  // build_preview()). Every field stays editable; nothing is submitted
  // until "Build" is actually clicked. ---
  const previewBtn = document.getElementById("build-trigger-preview-btn");
  const previewStatus = document.getElementById("build-trigger-preview-status");
  if (previewBtn) {
    previewBtn.addEventListener("click", () => {
      const builderIds = Array.from(
        document.querySelectorAll('#build-trigger-builders input[name="builder_ids"]')
      ).map((input) => input.value);
      if (builderIds.length === 0) {
        previewStatus.textContent = "Select Builder(s) first.";
        return;
      }

      const csrfToken = previewBtn.closest("form").querySelector('input[name="csrf_token"]').value;
      const formData = new FormData();
      formData.append("csrf_token", csrfToken);
      builderIds.forEach((id) => formData.append("builder_ids", id));
      const existingNotes = document.getElementById("build-trigger-additional-description");
      if (existingNotes && existingNotes.value.trim()) {
        formData.append("additional_description", existingNotes.value.trim());
      }

      previewBtn.disabled = true;
      previewStatus.textContent = "Reading commits since the last build...";

      fetch("/builders/build/preview", { method: "POST", body: formData })
        .then((response) => response.json().then((data) => ({ ok: response.ok, data })))
        .then(({ ok, data }) => {
          if (!ok) {
            previewStatus.textContent = data.error || "Preview failed.";
            return;
          }

          const bumpSelect = document.getElementById("build-trigger-bump-type");
          if (bumpSelect && data.bump_type) bumpSelect.value = data.bump_type;

          objectPicker.clear();
          (data.matched_objects || []).forEach((obj) => objectPicker.addExisting(obj));
          (data.new_object_names || []).forEach((name) => objectPicker.addNew(name));

          const changeTypeSelect = document.getElementById("build-trigger-change-type");
          if (changeTypeSelect && data.change_type_id) changeTypeSelect.value = data.change_type_id;

          const descriptionField = document.getElementById("build-trigger-additional-description");
          if (descriptionField && data.description) descriptionField.value = data.description;

          previewStatus.textContent =
            data.commit_count > 0
              ? `Pre-filled from ${data.commit_count} commit(s) since the last build — review before building.`
              : "No new commits found since the last build — fields left as-is.";
        })
        .catch(() => {
          previewStatus.textContent = "Preview failed — check the server logs.";
        })
        .finally(() => {
          previewBtn.disabled = false;
        });
    });
  }

  const groupNameSuggestions = parseSuggestionsData("group-name-suggestions-data");
  document.querySelectorAll(".group-name-input").forEach((input) => {
    setupSearchDropdown(input, input.parentElement.querySelector(".group-name-dropdown"), groupNameSuggestions);
  });

  // --- Build trigger: populate the modal from a row's own "Build" button
  // (single builder) or a group's "Build Group" button (every builder in
  // that Version's group, in one batch) — groups are already constrained to
  // one Version server-side, so no same-Version check is needed here. ---
  // Clears every field a previous open of this modal (for a *different*
  // builder/group) could have left behind — the Builders list itself is
  // always rebuilt fresh by openBuildModal below, but Bump Type/Object(s)/
  // Change Type/Additional Description/the preview status line are plain
  // form state that otherwise survives a close+reopen untouched, showing
  // stale data (most visibly whatever "Preview from Git" last filled in)
  // for a build it was never actually generated for.
  function resetBuildForm() {
    const bumpSelect = document.getElementById("build-trigger-bump-type");
    if (bumpSelect) bumpSelect.value = "";

    objectPicker.clear();

    const changeTypeSelect = document.getElementById("build-trigger-change-type");
    if (changeTypeSelect) changeTypeSelect.value = "";

    const descriptionField = document.getElementById("build-trigger-additional-description");
    if (descriptionField) descriptionField.value = "";

    const previewStatusEl = document.getElementById("build-trigger-preview-status");
    if (previewStatusEl) previewStatusEl.textContent = "";
  }

  function openBuildModal(builders) {
    const container = document.getElementById("build-trigger-builders");
    container.innerHTML = "";
    builders.forEach((builder) => {
      const row = document.createElement("div");
      row.className = "flex items-center gap-2";
      const nameSpan = document.createElement("span");
      nameSpan.className = "w-40 truncate text-sm";
      nameSpan.textContent = builder.name;
      const hiddenInput = document.createElement("input");
      hiddenInput.type = "hidden";
      hiddenInput.name = "builder_ids";
      hiddenInput.value = builder.id;
      // The branch to build is fixed to the Builder's own configured
      // default branch — not editable here. Change it via Edit Builder
      // if it needs to differ.
      const branchSpan = document.createElement("span");
      branchSpan.className = "flex-1 text-sm text-base-content/60";
      branchSpan.textContent = builder.defaultBranch || "no branch set";
      row.append(nameSpan, hiddenInput, branchSpan);
      container.appendChild(row);
    });
    resetBuildForm();
    document.getElementById("build-trigger-modal").showModal();
  }

  document.querySelectorAll(".build-one-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      openBuildModal([
        {
          id: btn.dataset.builderId,
          name: btn.dataset.builderName,
          defaultBranch: btn.dataset.defaultBranch,
        },
      ]);
    });
  });

  document.querySelectorAll(".build-group-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const group = btn.closest(".builder-group");
      // Read builder identity off each row itself, not its (grouped rows
      // have no) individual Build button — grouped builders are only ever
      // built via this group action, never one at a time.
      const builders = Array.from(group.querySelectorAll(".builder-row")).map((row) => ({
        id: row.dataset.builderId,
        name: row.dataset.builderName,
        defaultBranch: row.dataset.defaultBranch,
      }));
      openBuildModal(builders);
    });
  });

  // --- Live status polling ---
  const statusEl = document.getElementById("builder-status-content");
  if (statusEl) {
    // Tracks whether the last poll was "busy", so the skeleton (including the
    // log <pre>) is only rebuilt on an idle<->busy transition, not on every
    // poll — rebuilding it every 3s would wipe both the user's manual resize
    // (see resize-y below) and any scroll position each time.
    let wasBusy = null;

    function buildBusySkeleton() {
      statusEl.innerHTML = `
        <div class="flex items-center gap-2 text-sm">
          <span class="loading loading-spinner loading-sm text-warning"></span>
          <span>
            Building <strong id="builder-status-name"></strong> for batch <code id="builder-status-batch"></code>
          </span>
        </div>
        <div class="mt-2 flex items-center gap-2">
          <progress id="builder-status-progress-bar" class="progress progress-warning w-full"></progress>
          <span id="builder-status-progress" class="whitespace-nowrap text-xs text-base-content/60"></span>
        </div>
        <pre id="builder-status-log" class="mt-2 h-40 min-h-20 resize-y overflow-auto rounded bg-base-300 p-2 text-xs whitespace-pre-wrap"></pre>
      `;
    }

    function poll() {
      fetch("/builders/status")
        .then((r) => r.json())
        .then((data) => {
          if (!data.busy) {
            if (wasBusy === true) {
              // A build that was running when this page loaded just
              // finished — each row's "Last Build" column is static
              // server-rendered HTML from page load, so it wouldn't pick
              // up the new status/version without a reload.
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
          const progress = running.batch_progress;
          document.getElementById("builder-status-name").textContent = running.builder;
          document.getElementById("builder-status-batch").textContent = running.batch_version;
          document.getElementById("builder-status-progress").textContent =
            `${progress.finished} of ${progress.total} built`;
          const progressBar = document.getElementById("builder-status-progress-bar");
          progressBar.value = progress.finished;
          progressBar.max = progress.total;

          const logEl = document.getElementById("builder-status-log");
          logEl.textContent = running.log_tail || "";
          // Always stick to the latest output on refresh, per the whole
          // point of this view — no manual scrolling needed to see it.
          logEl.scrollTop = logEl.scrollHeight;
        })
        .catch(() => {});
    }
    poll();
    setInterval(poll, 3000);
  }
});
