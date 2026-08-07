document.addEventListener("DOMContentLoaded", () => {
  const STORAGE_KEY = "sidebarCollapsed";

  const wrapper = document.getElementById("app-drawer-wrapper");
  const checkbox = document.getElementById("app-drawer");
  const toggle = document.getElementById("sidebar-toggle");
  if (!wrapper || !checkbox || !toggle) return;

  const isDesktop = () => window.matchMedia("(min-width: 1024px)").matches;

  if (localStorage.getItem(STORAGE_KEY) === "true") {
    wrapper.classList.remove("lg:drawer-open");
  }

  // The hamburger animates into a cross whenever the sidebar is open, back
  // into three bars when it's closed. Kept in sync via the drawer
  // checkbox's own "change" event, not just this button's click handler —
  // the checkbox can also be unchecked natively by tapping the
  // drawer-overlay backdrop on mobile (see base.html), bypassing any click
  // handler on this button entirely.
  const navbarTitle = document.getElementById("navbar-title");

  function updateIcon() {
    const isOpen = isDesktop() ? wrapper.classList.contains("lg:drawer-open") : checkbox.checked;
    toggle.classList.toggle("is-active", isOpen);
    toggle.setAttribute("aria-expanded", String(isOpen));

    // The navbar and sidebar both show the app title — redundant whenever
    // the sidebar is visible. Configurable via /config (System
    // Configuration); see navbar.html's data-hide-when-sidebar-open.
    if (navbarTitle && navbarTitle.dataset.hideWhenSidebarOpen === "true") {
      navbarTitle.classList.toggle("hidden", isOpen);
    }
  }

  toggle.addEventListener("click", () => {
    if (isDesktop()) {
      const isOpen = wrapper.classList.toggle("lg:drawer-open");
      localStorage.setItem(STORAGE_KEY, String(!isOpen));
    } else {
      checkbox.checked = !checkbox.checked;
    }
    updateIcon();
  });

  checkbox.addEventListener("change", updateIcon);
  // Desktop vs. mobile read different state (the wrapper class vs. the
  // checkbox) for what "open" means — crossing that breakpoint via resize
  // must re-evaluate both the icon and the title, not just clicks/toggles.
  window.addEventListener("resize", updateIcon);

  updateIcon();
});
