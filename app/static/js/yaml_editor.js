// Shared CodeMirror YAML-editor init, used by deployment_manifests.js,
// deployment_servers.js, and yaml_generator.js — extracted out of two
// independently duplicated per-page copies to fix a cursor-position bug
// that existed in both (see initYamlEditor's lineWrapping comment and
// refreshAfterVisible below) and to give the YAML generator page's preview
// pane the same fix for free instead of becoming a third divergent copy.
//
// No bundler in this project (plain <script> tags) — exposed as a
// window.YamlEditor namespace, same convention as vendored CodeMirror/
// Sortable being consumed as globals.
(function () {
  const themeSyncedEditors = new Set();
  let themeObserverStarted = false;

  function currentCodeMirrorTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "material-darker" : "default";
  }

  function registerForThemeSync(cm) {
    themeSyncedEditors.add(cm);
    if (themeObserverStarted) return;
    themeObserverStarted = true;
    // theme.js only ever flips document.documentElement's data-theme
    // attribute directly (no event dispatched), so mirror that instead of
    // hooking a nonexistent theme-change event — one shared observer for
    // every editor on the page, instead of one per call site.
    new MutationObserver(() => {
      const theme = currentCodeMirrorTheme();
      themeSyncedEditors.forEach((editor) => editor.setOption("theme", theme));
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }

  // CodeMirror measures its container's layout at creation time — a
  // <dialog> that hasn't been shown yet (every manifest/server modal starts
  // closed) or a hidden fieldset (e.g. the api/kube connection-type
  // toggle) has zero size, so the editor is born with stale internal
  // click-position offsets until it recomputes them once actually visible.
  //
  // cm.refresh() alone is NOT sufficient to fix this — confirmed live: the
  // cursor-offset bug persisted through a refresh() fired well after the
  // dialog had visibly finished opening, but scrolling the editor down and
  // back up fixed it immediately, every time. refresh() recomputes line
  // heights/gutter width but apparently doesn't recompute whatever cached
  // absolute page-offset coordsChar() uses to turn a click's page X/Y into
  // a line/char position — a genuine scroll does, via a different internal
  // code path. So instead of guessing, this automates exactly that
  // confirmed-working manual workaround: nudge the scroll position by 1px
  // and back, forcing the same internal recompute a real scroll triggers.
  function forceCoordinateRecompute(cm) {
    cm.refresh();

    // Scroll nudge — automates the confirmed-working manual workaround.
    // Deliberately NOT paired with a resize-based nudge (e.g. cm.setSize())
    // as an extra trigger: watchForRealSize below observes this same
    // editor's own wrapper element via ResizeObserver, and a setSize() call
    // here would resize that exact observed element from inside its own
    // callback — a classic ResizeObserver feedback loop (each resize
    // re-triggers the observer, which resizes again...), which is what
    // made the editor stop rendering entirely the first time this was
    // tried. Scrolling doesn't resize anything, so it can't self-trigger.
    const info = cm.getScrollInfo();
    cm.scrollTo(null, info.top + 1);
    cm.scrollTo(null, info.top);
  }

  // ResizeObserver (fires only once the browser has actually committed a
  // new layout size, not a guessed frame count) is the trigger for the
  // above — robust regardless of how long a dialog's open animation/paint
  // takes. Falls back to a double requestAnimationFrame after a <dialog>
  // opens, for browsers without ResizeObserver.
  function refreshAfterVisible(cm) {
    requestAnimationFrame(() => requestAnimationFrame(() => forceCoordinateRecompute(cm)));
  }

  function watchForRealSize(cm) {
    if (typeof ResizeObserver === "undefined") return;
    const wrapper = cm.getWrapperElement();
    let lastWidth = 0;
    let lastHeight = 0;
    new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      if (width === lastWidth && height === lastHeight) return;
      lastWidth = width;
      lastHeight = height;
      if (width > 0 && height > 0) forceCoordinateRecompute(cm);
    }).observe(wrapper);
  }

  function initYamlEditor(textarea, options = {}) {
    if (typeof CodeMirror === "undefined") return null;

    const cm = CodeMirror.fromTextArea(textarea, {
      mode: "yaml",
      lineNumbers: true,
      // Wrapping made the gutter number a *logical* (\n-delimited) line
      // while a long wrapped line still rendered as 2+ *visual* rows —
      // anyone visually counting rows top-to-bottom against the gutter
      // would miscount by however many wrapped rows preceded their click.
      // Disabling it makes every gutter number map to exactly one visual
      // row; long lines (deep indentation, long image tags, base64
      // kubeconfig blobs) scroll horizontally instead, which is normal for
      // a monospace code editor.
      lineWrapping: false,
      tabSize: 2,
      // YAML indentation must be spaces — a literal tab character is a
      // syntax error to any YAML parser ("found character that cannot
      // start any token"). CodeMirror's default Tab-key behavior inserts
      // a real tab, so it's rebound here to insert spaces instead.
      indentWithTabs: false,
      extraKeys: { Tab: (editor) => editor.execCommand("insertSoftTab") },
      theme: currentCodeMirrorTheme(),
      readOnly: options.readOnly || false,
    });

    // CodeMirror only writes back into the underlying textarea when
    // .save() is called — without this, the server would always see
    // whatever the textarea had at page load, not the edited YAML. Only
    // relevant when the textarea actually lives inside a <form> (the YAML
    // generator's read-only preview pane doesn't).
    const form = textarea.closest("form");
    if (form) form.addEventListener("submit", () => cm.save());

    const dialog = textarea.closest("dialog");
    if (dialog) {
      new MutationObserver(() => {
        if (dialog.open) refreshAfterVisible(cm);
      }).observe(dialog, { attributes: true, attributeFilter: ["open"] });
    }

    // Primary fix — see forceCoordinateRecompute's comment. Runs
    // regardless of whether the editor started inside a <dialog>, a hidden
    // fieldset (deployment_servers.js's kube/api toggle), or was already
    // visible on page load (still fires once with the real initial size).
    watchForRealSize(cm);

    registerForThemeSync(cm);
    return cm;
  }

  window.YamlEditor = { initYamlEditor, refreshAfterVisible, currentCodeMirrorTheme };
})();
