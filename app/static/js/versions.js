document.addEventListener("DOMContentLoaded", () => {
  // --- Version Type field: a searchable dropdown of previously-used values
  // layered on top of a plain text input — typing a brand-new value is
  // always still accepted on submit (routes.py creates it if unseen), this
  // only adds a filtered "history" list on top. Shared by the create form
  // and every Version's edit form. ---
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

  const versionTypeSuggestions = parseSuggestionsData("version-type-suggestions-data");
  document.querySelectorAll(".version-type-input").forEach((input) => {
    setupSearchDropdown(input, input.parentElement.querySelector(".version-type-dropdown"), versionTypeSuggestions);
  });
});
