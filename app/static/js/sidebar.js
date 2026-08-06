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

  toggle.addEventListener("click", () => {
    if (isDesktop()) {
      const isOpen = wrapper.classList.toggle("lg:drawer-open");
      localStorage.setItem(STORAGE_KEY, String(!isOpen));
    } else {
      checkbox.checked = !checkbox.checked;
    }
  });
});
