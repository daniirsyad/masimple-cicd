// Page lifecycle for hx-boost navigation. htmx swaps <body> instead of
// reloading, so DOMContentLoaded fires once per real page load, and timers
// and window/document listeners outlive the page that started them.
//
// - Page scripts (in <body>, re-run by htmx after every swap): wrap init in
//   onPageReady(fn). Stop timers with onPageLeave(() => clearInterval(id));
//   pass { signal: pageSignal() } to window/document addEventListener and
//   to polling fetch() calls. pageInterval(fn, ms) = setInterval that stops
//   when the page is left and pauses while the tab is hidden.
// - Head scripts (loaded once, e.g. sidebar.js): onEveryPage(fn) runs fn on
//   the first load and right after every swap, before the browser paints.
// - refreshPage(): re-fetch the current page in place, keeping the scroll.
(function () {
  let controller = new AbortController();
  const everyPage = [];
  let pathBeforeRequest = location.pathname;

  function onPageReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn, { once: true });
    else fn();
  }

  window.onPageReady = onPageReady;
  window.pageSignal = () => controller.signal;
  window.onPageLeave = (fn) => controller.signal.addEventListener("abort", fn, { once: true });
  // A hidden tab skips its polls (they'd only take server threads), and
  // runs one at once when it's shown again.
  window.pageInterval = (fn, ms) => {
    const tick = () => {
      if (!document.hidden) fn();
    };
    const id = setInterval(tick, ms);
    document.addEventListener("visibilitychange", tick, { signal: controller.signal });
    window.onPageLeave(() => clearInterval(id));
    return id;
  };
  window.onEveryPage = (fn) => {
    everyPage.push(fn);
    onPageReady(fn);
  };
  window.refreshPage = () => {
    if (window.htmx) window.htmx.ajax("GET", location.href, { target: "body", swap: "innerHTML" });
    else location.reload();
  };

  const isPageSwap = (event) => event.detail.target === document.body;

  document.addEventListener("htmx:beforeRequest", () => {
    pathBeforeRequest = location.pathname;
  });

  document.addEventListener("htmx:beforeSwap", (event) => {
    if (!isPageSwap(event) || !event.detail.shouldSwap) return;
    // An expired session redirects to the login page, which has its own
    // layout: load it for real instead of putting it inside this one.
    const responseUrl = new URL(event.detail.xhr.responseURL || location.href);
    if (responseUrl.pathname.startsWith("/login")) {
      event.detail.shouldSwap = false;
      location.assign(responseUrl.href);
      return;
    }
    controller.abort();
    controller = new AbortController();
    // Going to another page starts with an empty list search (list-search.js);
    // a save that redirects back here keeps it. Cleared before the new
    // page's scripts run.
    if (responseUrl.pathname !== pathBeforeRequest) {
      Object.keys(sessionStorage)
        .filter((key) => key.startsWith("list-search:"))
        .forEach((key) => sessionStorage.removeItem(key));
    }
  });

  document.addEventListener("htmx:afterSwap", (event) => {
    if (!isPageSwap(event)) return;
    // A form that saves and redirects back here keeps its scroll position;
    // going to another page starts at the top.
    const responsePath = new URL(event.detail.xhr.responseURL || location.href).pathname;
    if (responsePath !== pathBeforeRequest) window.scrollTo(0, 0);
    everyPage.forEach((fn) => fn());
  });
})();
