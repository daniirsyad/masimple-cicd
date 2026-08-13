document.addEventListener("DOMContentLoaded", () => {
  // --- Object filter (/documentation index page): a hand-rolled
  // search+pills multi-select, backed by a plain <input type="hidden"
  // name="object_id"> per selection so the surrounding GET form submits
  // multiple values with no JS framework/third-party lib involved. Object
  // gets its own full-width row (see index.html) so a growing pill list
  // never pushes the single-line filters above it out of alignment.
  const root = document.getElementById("doc-filter-object");
  if (!root) return;

  const pillsWrap = document.getElementById("doc-filter-object-pills");
  const searchInput = document.getElementById("doc-filter-object-search");
  const dropdown = document.getElementById("doc-filter-object-dropdown");
  const suggestionsData = document.getElementById("doc-filter-object-suggestions-data");

  // The filter section lives inside a daisyUI .collapse, which sets
  // overflow: hidden on itself to make its grid-template-rows open/close
  // animation work — that clips any absolutely-positioned dropdown the
  // moment it extends past the collapse's own content flow. Moving the
  // dropdown to a direct child of <body> and driving it with position:
  // fixed (recomputed from the pills row's own bounding rect) escapes that
  // clip entirely instead of fighting the collapse's overflow.
  document.body.appendChild(dropdown);
  dropdown.classList.remove("absolute");
  dropdown.style.position = "fixed";
  dropdown.style.zIndex = "50";

  let suggestions = [];
  try {
    suggestions = JSON.parse(suggestionsData.textContent);
  } catch (err) {
    suggestions = [];
  }

  const selectedIds = new Set(
    Array.from(pillsWrap.querySelectorAll("[data-object-pill]")).map((pill) => pill.dataset.objectId)
  );

  const MAX_RESULTS = 20;

  // currentOptions/highlightedIndex back the Up/Down/Enter keyboard
  // handling below — kept in parallel with whatever renderDropdown last
  // drew, since the DOM gets fully rebuilt (innerHTML) on every render.
  let currentOptions = [];
  let highlightedIndex = -1;

  function updatePlaceholder() {
    searchInput.placeholder = selectedIds.size === 0 ? "Search Objects..." : "";
  }

  function positionDropdown() {
    const rect = pillsWrap.getBoundingClientRect();
    dropdown.style.top = `${rect.bottom + 4}px`;
    dropdown.style.left = `${rect.left}px`;
    dropdown.style.width = `${rect.width}px`;
  }

  function applyHighlight() {
    dropdown.querySelectorAll("[data-object-option]").forEach((el, i) => {
      const isHighlighted = i === highlightedIndex;
      el.classList.toggle("active", isHighlighted);
      el.classList.toggle("bg-base-200", isHighlighted);
      if (isHighlighted) el.scrollIntoView({ block: "nearest" });
    });
  }

  function hideDropdown() {
    dropdown.classList.add("hidden");
    dropdown.innerHTML = "";
    currentOptions = [];
    highlightedIndex = -1;
  }

  // Shown any time the cursor is in the search input (see the "focus"
  // listener below) — including right after picking an item, so choosing
  // several Objects in a row never needs a click-out/click-back-in detour.
  function renderDropdown(query) {
    const normalized = query.trim().toLowerCase();
    const matches = suggestions
      .filter((s) => !selectedIds.has(s.id))
      .filter((s) => !normalized || s.name.toLowerCase().includes(normalized))
      .slice(0, MAX_RESULTS);

    currentOptions = matches;

    if (matches.length === 0) {
      hideDropdown();
      return;
    }

    dropdown.innerHTML = matches
      .map(
        (s, i) =>
          `<li><a data-object-option data-index="${i}" data-id="${s.id}" data-name="${s.name.replace(/"/g, "&quot;")}">${s.name}</a></li>`
      )
      .join("");
    highlightedIndex = 0;
    applyHighlight();
    positionDropdown();
    dropdown.classList.remove("hidden");
  }

  // Pills are daisyUI badges (badge-primary badge-sm) — solid fill so a
  // growing selection stays visually distinct from the surrounding
  // bordered field, gap-1 to breathe around the × without ballooning
  // pill width, and a hover/active state on the × itself (not the whole
  // pill) so it reads as its own tappable target.
  function addPill(id, name) {
    if (selectedIds.has(id)) return;
    selectedIds.add(id);

    const pill = document.createElement("span");
    pill.className = "badge badge-primary badge-sm gap-1 transition-transform";
    pill.dataset.objectPill = "";
    pill.dataset.objectId = id;
    pill.innerHTML = `
      ${name}
      <input type="hidden" name="object_id" value="${id}" />
      <button type="button" class="leading-none opacity-70 transition-opacity hover:opacity-100" data-remove-pill aria-label="Remove ${name}">&times;</button>
    `;
    pillsWrap.insertBefore(pill, searchInput);

    searchInput.value = "";
    updatePlaceholder();
    searchInput.focus();
    renderDropdown("");
  }

  function removePill(id) {
    const pill = pillsWrap.querySelector(`[data-object-pill][data-object-id="${id}"]`);
    if (pill) pill.remove();
    selectedIds.delete(id);
    updatePlaceholder();
  }

  searchInput.addEventListener("input", () => renderDropdown(searchInput.value));
  searchInput.addEventListener("focus", () => renderDropdown(searchInput.value));

  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      hideDropdown();
    } else if (event.key === "Backspace" && searchInput.value === "") {
      const lastPill = pillsWrap.querySelector("[data-object-pill]:last-of-type");
      if (lastPill) removePill(lastPill.dataset.objectId);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      if (dropdown.classList.contains("hidden")) {
        renderDropdown(searchInput.value);
        return;
      }
      if (currentOptions.length === 0) return;
      highlightedIndex = (highlightedIndex + 1) % currentOptions.length;
      applyHighlight();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (dropdown.classList.contains("hidden")) {
        renderDropdown(searchInput.value);
        return;
      }
      if (currentOptions.length === 0) return;
      highlightedIndex = (highlightedIndex - 1 + currentOptions.length) % currentOptions.length;
      applyHighlight();
    } else if (event.key === "Enter") {
      // Prevent the surrounding <form method="get"> from submitting early
      // while the dropdown has a highlighted match — Enter should pick it,
      // same as a click, not fire "Filter".
      if (!dropdown.classList.contains("hidden") && currentOptions[highlightedIndex]) {
        event.preventDefault();
        const opt = currentOptions[highlightedIndex];
        addPill(opt.id, opt.name);
      }
    }
  });

  dropdown.addEventListener("click", (event) => {
    const option = event.target.closest("[data-object-option]");
    if (!option) return;
    addPill(option.dataset.id, option.dataset.name);
  });

  pillsWrap.addEventListener("click", (event) => {
    const removeBtn = event.target.closest("[data-remove-pill]");
    if (!removeBtn) return;
    const pill = removeBtn.closest("[data-object-pill]");
    if (pill) removePill(pill.dataset.objectId);
  });

  // event.target here can be a node that renderDropdown() has *already
  // replaced* by the time this bubbles up from a dropdown click (addPill
  // rebuilds dropdown.innerHTML synchronously), which would make a plain
  // root.contains(event.target)/dropdown.contains(event.target) check
  // fail on the now-detached original node and immediately re-close the
  // list addPill just reopened. event.composedPath() is captured at
  // dispatch time, before any of that mutation, so it stays accurate.
  document.addEventListener("click", (event) => {
    const path = event.composedPath();
    if (!path.includes(root) && !path.includes(dropdown)) hideDropdown();
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
});
