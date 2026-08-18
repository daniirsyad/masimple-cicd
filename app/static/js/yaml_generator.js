document.addEventListener("DOMContentLoaded", () => {
  const csrfToken = document.getElementById("yaml-generator-page")?.dataset.csrf;

  // --- Kind switcher: exactly one of the 5 per-kind <form> fieldsets is
  // visible at a time — same mechanism as deployment_servers.js's kube/api
  // connection-type toggle. ---
  const kindSelect = document.getElementById("kind-select");
  const generatorForms = document.querySelectorAll(".generator-form");

  function syncKindVisibility() {
    const kind = kindSelect.value;
    generatorForms.forEach((form) => form.classList.toggle("hidden", form.dataset.kind !== kind));
  }

  // --- Dynamic key-value/port rows — same __INDEX__-template-clone
  // approach as deployment_pods.js's addEntryRow, generalized here since
  // both row shapes (key/value, port/target_port/protocol) clone the same
  // way regardless of their field names. ---
  function addRow(container, template) {
    const index = parseInt(container.dataset.nextIndex || "0", 10);
    const html = template.innerHTML.replaceAll("__INDEX__", String(index));
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html.trim();
    const row = wrapper.firstElementChild;
    container.appendChild(row);
    container.dataset.nextIndex = String(index + 1);
    return row;
  }

  document.querySelectorAll(".add-row-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const container = document.getElementById(button.dataset.container);
      const template = document.getElementById(button.dataset.template);
      if (!container || !template) return;
      addRow(container, template);
      scheduleGenerate();
    });
  });

  // Delegated (rows are added dynamically after page load). `.peer-rows`
  // (NetworkPolicy's peer-type + labels/CIDR rows) clones the same way as
  // the other two despite its different field shape — addRow()/the
  // __INDEX__ template-clone approach above doesn't care what's inside a row.
  document.querySelectorAll(".key-value-rows, .port-rows, .peer-rows").forEach((container) => {
    container.addEventListener("click", (event) => {
      const removeButton = event.target.closest(".remove-row-btn");
      if (!removeButton) return;
      removeButton.closest(".key-value-row, .port-row, .peer-row")?.remove();
      scheduleGenerate();
    });
  });

  // --- Live preview pane — read-only CodeMirror via the shared module
  // (app/static/js/yaml_editor.js), the same fix that closed the
  // cursor-position bug in the Manifest/Server editors applies here too. ---
  const previewTextarea = document.getElementById("preview-yaml");
  const previewEditor = window.YamlEditor ? window.YamlEditor.initYamlEditor(previewTextarea, { readOnly: true }) : null;

  const copyBtn = document.getElementById("copy-btn");
  const downloadBtn = document.getElementById("download-btn");
  const saveBtn = document.getElementById("save-as-manifest-btn");
  const errorsEl = document.getElementById("generate-errors");
  const saveYamlContentInput = document.getElementById("save-yaml-content");
  const saveManifestNameInput = document.getElementById("save-manifest-name");

  function currentYamlText() {
    return previewEditor ? previewEditor.getValue() : previewTextarea.value;
  }

  function setPreview(text) {
    if (previewEditor) previewEditor.setValue(text);
    else previewTextarea.value = text;
    saveYamlContentInput.value = text;
    copyBtn.disabled = false;
    downloadBtn.disabled = false;
    saveBtn.disabled = false;
  }

  function renderErrors(errors) {
    if (!errors) {
      errorsEl.textContent = "";
      return;
    }
    const lines = [];
    Object.entries(errors).forEach(([field, messages]) => {
      (Array.isArray(messages) ? messages : [messages]).forEach((message) => lines.push(`${field}: ${message}`));
    });
    errorsEl.textContent = lines.join(" — ");
  }

  function activeForm() {
    return document.querySelector(`.generator-form[data-kind="${kindSelect.value}"]`);
  }

  function generateNow() {
    const form = activeForm();
    if (!form) return;
    const kind = form.dataset.kind;

    fetch(`/yaml-generator/generate/${kind}`, {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken, "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(new FormData(form)),
    })
      .then((response) => response.json().then((data) => ({ ok: response.ok, data })))
      .then(({ ok, data }) => {
        if (ok) {
          setPreview(data.yaml);
          renderErrors(null);
          // Suggest the generated resource's own name as the manifest name,
          // without ever overwriting something the user already typed.
          const nameInput = form.querySelector('input[name="name"]');
          if (nameInput && !saveManifestNameInput.value) saveManifestNameInput.value = nameInput.value;
        } else {
          renderErrors(data.errors || { error: "Could not generate YAML." });
        }
      })
      .catch(() => renderErrors({ error: "Request failed." }));
  }

  let debounceTimer = null;
  function scheduleGenerate() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(generateNow, 400);
  }

  kindSelect.addEventListener("change", () => {
    syncKindVisibility();
    scheduleGenerate();
  });
  syncKindVisibility();

  generatorForms.forEach((form) => {
    form.addEventListener("input", scheduleGenerate);
    form.addEventListener("change", scheduleGenerate);
  });

  document.getElementById("generate-btn")?.addEventListener("click", () => {
    clearTimeout(debounceTimer);
    generateNow();
  });

  copyBtn?.addEventListener("click", () => {
    // navigator.clipboard only exists in a secure context (HTTPS, or the
    // literal hostname `localhost`) — plain HTTP via any other hostname/IP
    // leaves it undefined entirely, not just erroring on use.
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(currentYamlText());
      return;
    }

    // document.execCommand("copy") was tried here as a fallback, but on an
    // insecure origin some Chromium builds report success while silently
    // never reaching the OS clipboard. window.prompt()'s text field is
    // real browser-native UI, so a manual Ctrl+C/Cmd+C out of it always
    // works, unlike a scripted copy on an insecure origin.
    window.prompt("Copy with Ctrl+C / Cmd+C, then press Enter:", currentYamlText());
  });

  downloadBtn?.addEventListener("click", () => {
    const blob = new Blob([currentYamlText()], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${kindSelect.value}.yaml`;
    a.click();
    URL.revokeObjectURL(url);
  });
});
