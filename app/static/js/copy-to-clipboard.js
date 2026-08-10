document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".copy-to-clipboard-btn").forEach((btn) => {
    const target = document.getElementById(btn.dataset.copyTarget);
    const copyIcon = btn.querySelector(".copy-icon");
    const checkIcon = btn.querySelector(".check-icon");
    if (!target || !copyIcon || !checkIcon) return;

    let resetTimer = null;

    btn.addEventListener("click", () => {
      navigator.clipboard.writeText(target.textContent).then(() => {
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
