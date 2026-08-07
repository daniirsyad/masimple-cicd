document.addEventListener("DOMContentLoaded", () => {
  const STORAGE_KEY = "theme";
  const toggle = document.getElementById("theme-toggle");
  if (!toggle) return;

  const lightIcon = toggle.querySelector(".theme-icon-light");
  const darkIcon = toggle.querySelector(".theme-icon-dark");

  function applyIcons(theme) {
    const isDark = theme === "dark";
    lightIcon.classList.toggle("hidden", isDark);
    darkIcon.classList.toggle("hidden", !isDark);
  }

  // The <html data-theme> attribute itself was already set synchronously in
  // <head> (see base.html) before this script ever runs, to avoid a flash
  // of the wrong theme — this just syncs the icon to whatever that ended up
  // being (stored preference, or the OS preference as a fallback).
  applyIcons(document.documentElement.getAttribute("data-theme"));

  toggle.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem(STORAGE_KEY, next);
    applyIcons(next);
  });
});
