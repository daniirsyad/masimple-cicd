document.addEventListener("DOMContentLoaded", () => {
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
        statusEl.textContent = "Order saved.";
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
  tree.querySelectorAll(".menu-children").forEach((list) => {
    new Sortable(list, {
      handle: ".drag-handle",
      animation: 150,
      group: "menu-children",
      onEnd: submitOrder,
    });
  });
});
