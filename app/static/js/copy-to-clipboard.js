// navigator.clipboard only exists in a "secure context" — HTTPS, or the
// literal hostname `localhost`. Opening this app over plain HTTP via any
// other hostname/IP (e.g. a LAN address, or a container's exposed port
// reached by something other than `localhost`) leaves `navigator.clipboard`
// undefined entirely, not just erroring on use.
//
// document.execCommand("copy") was tried here as a fallback, but on an
// insecure origin some Chromium builds report success (returns true, no
// exception) while silently never actually reaching the OS clipboard — so
// its return value can't be trusted to mean anything. window.prompt()'s
// text field is real browser-native UI, not page content, so a manual
// Ctrl+C/Cmd+C out of it always reaches the OS clipboard regardless of
// secure-context restrictions; less slick than an automatic copy, but
// actually works instead of silently lying about it.
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }

  window.prompt("Copy with Ctrl+C / Cmd+C, then press Enter:", text);
  return Promise.resolve();
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".copy-to-clipboard-btn").forEach((btn) => {
    const target = document.getElementById(btn.dataset.copyTarget);
    const copyIcon = btn.querySelector(".copy-icon");
    const checkIcon = btn.querySelector(".check-icon");
    if (!target || !copyIcon || !checkIcon) return;

    let resetTimer = null;

    btn.addEventListener("click", () => {
      copyText(target.textContent).then(() => {
        copyIcon.classList.add("hidden");
        checkIcon.classList.remove("hidden");
        btn.classList.add("btn-success");

        clearTimeout(resetTimer);
        resetTimer = setTimeout(() => {
          copyIcon.classList.remove("hidden");
          checkIcon.classList.add("hidden");
          btn.classList.remove("btn-success");
        }, 1500);
      });
    });
  });
});
