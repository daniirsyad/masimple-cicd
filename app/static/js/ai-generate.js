document.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("ai-generate");
  const generateBtn = document.getElementById("generate-description-btn");
  if (!container || !generateBtn) return;

  const generateUrl = container.dataset.generateUrl;
  const csrfToken = container.dataset.csrf;
  const providerSelect = document.getElementById("ai-provider-select");
  const promptField = document.getElementById("ai-prompt-preview");
  const errorEl = document.getElementById("ai-generate-error");
  const draftContainer = document.getElementById("ai-draft-container");
  const draftPreview = document.getElementById("ai-draft-preview");
  const useDraftBtn = document.getElementById("use-ai-draft-btn");
  const descriptionField = document.querySelector('textarea[name="description"]');
  const aiDescriptionField = document.querySelector('input[name="ai_description"]');
  const aiProviderUsedField = document.querySelector('input[name="ai_provider_used"]');

  let lastDraft = "";

  function showError(message) {
    errorEl.textContent = message;
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
          showError(data.error || "Failed to generate a description.");
          return;
        }
        lastDraft = data.description;
        draftPreview.textContent = data.description;
        draftContainer.classList.remove("hidden");
        if (aiDescriptionField) aiDescriptionField.value = data.description;
        if (aiProviderUsedField) aiProviderUsedField.value = data.provider;
      })
      .catch(() => showError("Failed to generate a description."))
      .finally(() => {
        generateBtn.disabled = false;
        generateBtn.textContent = originalLabel;
      });
  });

  if (useDraftBtn) {
    useDraftBtn.addEventListener("click", () => {
      if (descriptionField) descriptionField.value = lastDraft;
    });
  }
});
