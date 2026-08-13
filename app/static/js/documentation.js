document.addEventListener("DOMContentLoaded", () => {
  function parseJsonData(elementId) {
    const dataEl = document.getElementById(elementId);
    if (!dataEl) return [];
    try {
      return JSON.parse(dataEl.textContent);
    } catch (err) {
      return [];
    }
  }

  // --- Multi-select "Object" picker — same widget as builders.js's
  // build-trigger modal (duplicated per this codebase's per-page JS
  // convention), seeded here with the doc's already-saved Objects instead
  // of starting empty. Picking an existing Object adds "object_ids", typing
  // an unmatched value and pressing Enter (or the dropdown's "+ Add ... as
  // new") adds "new_object_names" — get-or-created server-side in
  // Object.resolve(). ---
  function setupMultiObjectPicker(input, dropdown, pillsContainer, hiddenContainer, suggestions, initialSelected) {
    if (!input || !dropdown || !pillsContainer || !hiddenContainer) return;

    const selected = (initialSelected || []).map((obj) => ({ kind: "existing", id: obj.id, name: obj.name }));

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

    function renderOptions() {
      const query = input.value.trim().toLowerCase();
      const matches = suggestions.filter(
        (s) => !isSelected(s.name) && (!query || s.name.toLowerCase().includes(query))
      );

      dropdown.innerHTML = "";
      matches.forEach((s) => {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.textContent = s.name;
        a.addEventListener("mousedown", (event) => {
          event.preventDefault();
          addExisting(s);
          input.value = "";
          renderOptions();
        });
        li.appendChild(a);
        dropdown.appendChild(li);
      });

      const trimmed = input.value.trim();
      const exactMatch = suggestions.some((s) => s.name.toLowerCase() === trimmed.toLowerCase());
      if (trimmed && !exactMatch && !isSelected(trimmed)) {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.textContent = `+ Add "${trimmed}" as new`;
        a.addEventListener("mousedown", (event) => {
          event.preventDefault();
          addNew(trimmed);
          input.value = "";
          renderOptions();
        });
        li.appendChild(a);
        dropdown.appendChild(li);
      }

      dropdown.classList.toggle("hidden", dropdown.children.length === 0);
    }

    input.addEventListener("focus", renderOptions);
    input.addEventListener("input", renderOptions);
    input.addEventListener("blur", () => dropdown.classList.add("hidden"));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        dropdown.classList.add("hidden");
      } else if (event.key === "Enter") {
        event.preventDefault();
        const trimmed = input.value.trim();
        if (!trimmed) return;
        const match = suggestions.find((s) => s.name.toLowerCase() === trimmed.toLowerCase());
        if (match) addExisting(match);
        else addNew(trimmed);
        input.value = "";
        dropdown.classList.add("hidden");
      }
    });

    renderPills();
  }

  setupMultiObjectPicker(
    document.getElementById("doc-object-input"),
    document.getElementById("doc-object-dropdown"),
    document.getElementById("doc-object-pills"),
    document.getElementById("doc-object-hidden"),
    parseJsonData("doc-object-suggestions-data"),
    parseJsonData("doc-object-selected-data")
  );
});
