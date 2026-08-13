document.addEventListener("DOMContentLoaded", () => {
  // --- YAML editor (CodeMirror) for the kubeconfig field — same
  // progressive-enhancement approach as deployment_manifests.js's
  // yaml_content editor; shared init lives in yaml_editor.js. Unlike that
  // file, nothing here needs to look an instance back up by textarea id
  // afterward, so the return value isn't kept. ---
  document.querySelectorAll(".yaml-content-input").forEach((textarea) => window.YamlEditor.initYamlEditor(textarea));

  // --- Show only the credential fields relevant to the chosen connection
  // type (kube vs api), per form (create + each edit form). ---
  document.querySelectorAll(".deployment-server-form").forEach((form) => {
    const select = form.querySelector(".connection-type-select");
    const kubeFields = form.querySelector(".connection-fields-kube");
    const apiFields = form.querySelector(".connection-fields-api");
    if (!select) return;

    function syncVisibility() {
      const isKube = select.value === "kube";
      kubeFields.classList.toggle("hidden", !isKube);
      apiFields.classList.toggle("hidden", isKube);
      // No manual refresh call needed here — yaml_editor.js's
      // ResizeObserver watches the kubeconfig editor's own wrapper element
      // and recomputes automatically the moment this toggle actually
      // changes its real rendered size (zero while hidden, real once
      // shown), the same fix that covers the <dialog>-open case.
    }

    select.addEventListener("change", syncVisibility);
    syncVisibility();
  });
});
