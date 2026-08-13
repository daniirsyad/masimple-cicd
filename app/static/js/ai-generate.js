document.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("ai-generate");
  const generateBtn = document.getElementById("generate-description-btn");
  if (!container || !generateBtn) return;

  const generateUrl = container.dataset.generateUrl;
  const csrfToken = container.dataset.csrf;
  const providerSelect = document.getElementById("ai-provider-select");
  const promptField = document.getElementById("ai-prompt-preview");
  const errorEl = document.getElementById("ai-generate-error");
  const descriptionField = document.querySelector('textarea[name="description"]');
  const aiDescriptionField = document.querySelector('input[name="ai_description"]');
  const aiProviderUsedField = document.querySelector('input[name="ai_provider_used"]');

  function showError(message, errorLogUrl) {
    errorEl.textContent = message;
    if (errorLogUrl) {
      errorEl.appendChild(document.createElement("br"));
      const link = document.createElement("a");
      link.href = errorLogUrl;
      link.target = "_blank";
      link.className = "link link-primary";
      link.textContent = "View error details";
      errorEl.appendChild(link);
    }
    errorEl.classList.remove("hidden");
  }

  function hideError() {
    errorEl.classList.add("hidden");
  }

  generateBtn.addEventListener("click", () => {
    hideError();
    generateBtn.disabled = true;
    const originalLabel = generateBtn.textContent;
    generateBtn.textContent = "Generating...";

    fetch(generateUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
      },
      body: JSON.stringify({
        prompt: promptField.value,
        provider_type: providerSelect ? providerSelect.value : null,
      }),
    })
      .then((response) => response.json().then((data) => ({ ok: response.ok, data })))
      .then(({ ok, data }) => {
        if (!ok) {
          showError(data.error || "Failed to generate a description.", data.error_log_url);
          return;
        }
        // Written straight into the real Description field — no separate
        // draft box to review/copy from first, per the "don't show the AI
        // Draft" UI decision. Still fully editable there before saving.
        if (descriptionField) descriptionField.value = data.description;
        if (aiDescriptionField) aiDescriptionField.value = data.description;
        if (aiProviderUsedField) aiProviderUsedField.value = data.provider;
      })
      .catch(() => showError("Failed to generate a description."))
      .finally(() => {
        generateBtn.disabled = false;
        generateBtn.textContent = originalLabel;
      });
  });
});
