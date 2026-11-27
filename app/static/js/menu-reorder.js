onPageReady(() => {
  // --- New / Edit form: redraw the sidebar preview as the label and icon change. ---
  // The icon is the admin's own SVG (the sidebar renders it as-is too);
  // scripts and on* handlers are still dropped from the preview.
  function svgFrom(markup) {
    const doc = new DOMParser().parseFromString(markup, "image/svg+xml");
    const svg = doc.documentElement;
    if (svg.nodeName !== "svg" || doc.querySelector("parsererror")) return null;
    svg.querySelectorAll("script, foreignObject").forEach((el) => el.remove());
    [svg, ...svg.querySelectorAll("*")].forEach((el) =>
      [...el.attributes].filter((a) => a.name.startsWith("on")).forEach((a) => el.removeAttribute(a.name))
    );
    return document.importNode(svg, true);
  }

  document.querySelectorAll(".menu-form").forEach((form) => {
    const label = form.querySelector(".menu-label-input");
    const icon = form.querySelector(".menu-icon-input");
    const previewLabel = form.querySelector(".menu-preview-label");
    const previewIcon = form.querySelector(".menu-preview-icon");
    function redraw() {
      previewLabel.textContent = label.value.trim() || "Untitled";
      previewLabel.classList.toggle("text-base-content/40", !label.value.trim());
      const svg = icon.value.trim() ? svgFrom(icon.value.trim()) : null;
      previewIcon.replaceChildren(...(svg ? [svg] : []));
    }
    label.addEventListener("input", redraw);
    icon.addEventListener("input", redraw);
    redraw();
  });

  const tree = document.getElementById("menu-tree");
  if (!tree || typeof Sortable === "undefined") return;

  const reorderUrl = tree.dataset.reorderUrl;
  const csrfToken = tree.dataset.csrf;
  const statusEl = document.getElementById("reorder-status");

  function buildPayload() {
    const topLevel = Array.from(tree.children)
      .filter((card) => card.dataset.menuId)
      .map((card) => card.dataset.menuId);

    const children = {};
    tree.querySelectorAll(".menu-children").forEach((list) => {
      const parentId = list.dataset.parentId;
      children[parentId] = Array.from(list.children)
        .filter((item) => item.dataset.menuId)
        .map((item) => item.dataset.menuId);
    });

    return { top_level: topLevel, children };
  }

  async function submitOrder() {
    const payload = buildPayload();
    try {
      const response = await fetch(reorderUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || "Failed to save the new order.");
      }

      if (statusEl) {
        statusEl.textContent = "Order saved. The sidebar shows it on the next page you open.";
        statusEl.className = "mt-2 text-sm text-success";
      }
    } catch (err) {
      if (statusEl) {
        statusEl.textContent = err.message;
        statusEl.className = "mt-2 text-sm text-error";
      }
    }
  }

  // Top-level cards reorder among themselves only.
  new Sortable(tree, {
    handle: ".drag-handle",
    animation: 150,
    group: "menu-top-level",
    onEnd: submitOrder,
  });

  // Every parent's children list shares one group so sub-items can be
  // reordered or moved between different parents (still depth 1 either way).
  // While one is dragged, every group shows a drop zone (empty ones are
  // otherwise zero-height): .is-dragging-child in input.css.
  tree.querySelectorAll(".menu-children").forEach((list) => {
    new Sortable(list, {
      handle: ".drag-handle",
      animation: 150,
      group: "menu-children",
      onStart: () => tree.classList.add("is-dragging-child"),
      onEnd: () => {
        tree.classList.remove("is-dragging-child");
        submitOrder();
      },
    });
  });
});
