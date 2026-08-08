document.addEventListener("DOMContentLoaded", () => {
  // --- YAML editor (CodeMirror) for the kubeconfig field — same
  // progressive-enhancement approach as deployment_manifests.js's
  // yaml_content editor: wraps the plain <textarea> the server actually
  // reads on submit, and falls back to it untouched if the vendored script
  // failed to load. ---
  const yamlEditors = {}; // textarea id -> CodeMirror instance

  function currentCodeMirrorTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "material-darker" : "default";
  }

  if (typeof CodeMirror !== "undefined") {
    document.querySelectorAll(".yaml-content-input").forEach((textarea) => {
      const cm = CodeMirror.fromTextArea(textarea, {
        mode: "yaml",
        lineNumbers: true,
        lineWrapping: true,
        tabSize: 2,
        // YAML indentation must be spaces — a literal tab character is a
        // syntax error to any YAML parser ("found character that cannot
        // start any token"). CodeMirror's default Tab-key behavior inserts
        // a real tab, so it's rebound here to insert spaces instead.
        indentWithTabs: false,
        extraKeys: { Tab: (editor) => editor.execCommand("insertSoftTab") },
        theme: currentCodeMirrorTheme(),
      });
      yamlEditors[textarea.id] = cm;

      // CodeMirror only writes back into the underlying textarea when
      // .save() is called.
      const form = textarea.closest("form");
      if (form) form.addEventListener("submit", () => cm.save());

      // CodeMirror measures its container's layout at creation time — both
      // the closed <dialog> around every server modal, and (for an "api"-
      // type server) the connection-fields-kube div starting hidden, mean
      // the editor can be born with zero size until refreshed once actually
      // visible. The dialog case is handled once here per editor; the
      // connection-type-toggle case is handled below, in syncVisibility().
      const dialog = textarea.closest("dialog");
      if (dialog) {
        new MutationObserver(() => {
          if (dialog.open) cm.refresh();
        }).observe(dialog, { attributes: true, attributeFilter: ["open"] });
      }
    });

    if (Object.keys(yamlEditors).length) {
      // theme.js only ever flips document.documentElement's data-theme
      // attribute directly (no event dispatched), so mirror that instead of
      // hooking a nonexistent theme-change event.
      new MutationObserver(() => {
        const theme = currentCodeMirrorTheme();
        Object.values(yamlEditors).forEach((cm) => cm.setOption("theme", theme));
      }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    }
  }

  // --- Show only the credential fields relevant to the chosen connection
  // type (kube vs api), per form (create + each edit form). ---
  document.querySelectorAll(".deployment-server-form").forEach((form) => {
    const select = form.querySelector(".connection-type-select");
    const kubeFields = form.querySelector(".connection-fields-kube");
    const apiFields = form.querySelector(".connection-fields-api");
    if (!select) return;

    const kubeconfigTextarea = kubeFields.querySelector(".yaml-content-input");
    const kubeconfigEditor = kubeconfigTextarea ? yamlEditors[kubeconfigTextarea.id] : null;

    function syncVisibility() {
      const isKube = select.value === "kube";
      kubeFields.classList.toggle("hidden", !isKube);
      apiFields.classList.toggle("hidden", isKube);
      // Becoming visible after being born hidden (e.g. an "api"-type server
      // whose connection_type is switched to "kube") needs the same
      // zero-size-layout refresh as the dialog-open case above.
      if (isKube && kubeconfigEditor) kubeconfigEditor.refresh();
    }

    select.addEventListener("change", syncVisibility);
    syncVisibility();
  });
});
