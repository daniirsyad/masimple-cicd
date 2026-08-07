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

  setupSearchDropdown(
    document.getElementById("build-trigger-object-input"),
    document.getElementById("build-trigger-object-dropdown"),
    parseSuggestionsData("build-trigger-object-suggestions-data")
  );

  const groupNameSuggestions = parseSuggestionsData("group-name-suggestions-data");
  document.querySelectorAll(".group-name-input").forEach((input) => {
    setupSearchDropdown(input, input.parentElement.querySelector(".group-name-dropdown"), groupNameSuggestions);
  });

  // --- Build trigger: populate the modal from a row's own "Build" button
  // (single builder) or a group's "Build Group" button (every builder in
  // that Version's group, in one batch) — groups are already constrained to
  // one Version server-side, so no same-Version check is needed here. ---
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
