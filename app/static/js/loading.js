// Loading feedback, so a slow action never looks like it did nothing.
//
// - Every form htmx submits (hx-boost): the button that submitted it gets a
//   spinner and is disabled until the response arrives, which also stops a
//   double submit. Opt a button out with data-no-busy.
// - Every htmx request: a thin bar at the top of the window, shown only if
//   the request takes longer than SHOW_BAR_AFTER_MS (no flicker on fast ones).
// - Page scripts doing their own fetch(): setBusy(button, true/false) around
//   it, or showLoading(statusEl, "Doing x...") for a status line the result
//   overwrites.
(function () {
  const SHOW_BAR_AFTER_MS = 300;
  const submitters = new WeakMap();
  let pending = 0;
  let barTimer = null;

  function spinner() {
    const el = document.createElement("span");
    el.className = "loading loading-spinner loading-xs self-center";
    el.dataset.busySpinner = "";
    el.setAttribute("aria-hidden", "true");
    return el;
  }

  window.setBusy = (button, busy) => {
    if (!button) return;
    if (busy) {
      if (button.getAttribute("aria-busy") === "true") return;
      button.dataset.wasDisabled = button.disabled ? "1" : "";
      // WTForms renders <input type="submit">, which can't hold children.
      if (button.tagName === "INPUT") button.before(spinner());
      else button.prepend(spinner());
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    } else {
      if (button.getAttribute("aria-busy") !== "true") return;
      const sibling = button.previousElementSibling;
      if (button.tagName === "INPUT" && sibling?.hasAttribute("data-busy-spinner")) sibling.remove();
      button.querySelector(":scope > [data-busy-spinner]")?.remove();
      button.disabled = button.dataset.wasDisabled === "1";
      button.removeAttribute("aria-busy");
    }
  };

  window.showLoading = (el, text) => {
    if (el) el.replaceChildren(spinner(), ` ${text}`);
  };

  // Remember which button submitted each form: htmx's events only name the form.
  document.addEventListener(
    "submit",
    (event) => {
      if (event.submitter) submitters.set(event.target, event.submitter);
    },
    true
  );

  function submitButtonFor(elt) {
    const form = elt.closest("form");
    if (!form) return null;
    const button =
      submitters.get(form) || form.querySelector('button[type="submit"], button:not([type]), input[type="submit"]');
    return button && !button.hasAttribute("data-no-busy") ? button : null;
  }

  function requestDone() {
    pending = Math.max(0, pending - 1);
    if (pending === 0) {
      clearTimeout(barTimer);
      barTimer = null;
      document.documentElement.classList.remove("page-loading");
    }
  }

  // beforeSend, not beforeRequest: htmx has already read the form's values
  // by now, so disabling a named button doesn't drop its name/value.
  // Also where the bar's request count goes up. It comes back down on the
  // request's own loadend (fires for success, error, abort and timeout), not
  // on htmx:afterRequest: htmx fires that on the element that made the
  // request, and if a page change has removed it (the GitHub repo list or a
  // cluster page's loading page, then the user clicks away), the event never
  // reaches document and the bar stayed on for good.
  document.addEventListener("htmx:beforeSend", (event) => {
    const button = submitButtonFor(event.detail.elt);
    if (button) {
      window.setBusy(button, true);
      event.detail.elt.dataset.busyButtonPending = "";
    }
    pending += 1;
    event.detail.xhr.addEventListener("loadend", requestDone, { once: true });
    if (barTimer === null) {
      barTimer = setTimeout(() => document.documentElement.classList.add("page-loading"), SHOW_BAR_AFTER_MS);
    }
  });

  // Fires for success, error and network failure alike. If the page was
  // swapped, the old button is already gone; if not (a 204, a failed
  // request), it comes back.
  document.addEventListener("htmx:afterRequest", (event) => {
    const elt = event.detail.elt;
    if (elt && "busyButtonPending" in elt.dataset) {
      delete elt.dataset.busyButtonPending;
      const form = elt.closest("form");
      if (form) {
        window.setBusy(submitters.get(form), false);
        form.querySelectorAll('[aria-busy="true"]').forEach((button) => window.setBusy(button, false));
      }
    }
  });
})();
