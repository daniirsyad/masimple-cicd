// Instant search over a page's lists (Deployment Manifests, Builders,
// Workflows), all of which render every item on one page.
//
// <input data-list-search="#lists" data-list-search-empty="#no-matches">:
// inside #lists, each [data-search-card] (a group card) holds rows with a
// lowercase [data-search] text. A row shows when it contains every typed
// word; a card hides when none of its rows show, unless its own
// data-search-card text (the group name) matches, which shows the whole
// group. The query survives the page re-rendering after a save or a deploy;
// going to another page clears it (page.js).
onPageReady(() => {
  document.querySelectorAll("input[data-list-search]").forEach((input) => {
    const container = document.querySelector(input.dataset.listSearch);
    if (!container) return;
    const empty = document.querySelector(input.dataset.listSearchEmpty);
    const storageKey = `list-search:${location.pathname}`;

    function apply() {
      const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      const matches = (text) => words.every((word) => text.includes(word));
      let shown = 0;

      // The container can itself be the one card (Workflows).
      const cards = container.matches("[data-search-card]")
        ? [container]
        : container.querySelectorAll("[data-search-card]");
      cards.forEach((card) => {
        const wholeCard = words.length > 0 && matches(card.dataset.searchCard);
        let shownInCard = 0;
        card.querySelectorAll("[data-search]").forEach((row) => {
          const show = !words.length || wholeCard || matches(row.dataset.search);
          row.classList.toggle("hidden", !show);
          if (show) shownInCard++;
        });
        card.classList.toggle("hidden", shownInCard === 0);
        shown += shownInCard;
      });

      if (empty) {
        empty.classList.toggle("hidden", !(words.length && shown === 0));
        const echo = empty.querySelector("[data-list-search-query]");
        if (echo) echo.textContent = input.value.trim();
      }
      sessionStorage.setItem(storageKey, input.value);
    }

    input.value = sessionStorage.getItem(storageKey) || "";
    input.addEventListener("input", apply);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && input.value) {
        input.value = "";
        apply();
      }
    });
    apply();
  });
});
