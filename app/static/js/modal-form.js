// Opening a native <dialog> modally makes the browser block page scrolling,
// which hides the scrollbar and reflows the whole page by its width. Patch
// showModal() globally (rather than per-template) so every dialog in the app
// compensates with matching padding, and nothing visibly shifts.
(function () {
  const nativeShowModal = HTMLDialogElement.prototype.showModal;

  HTMLDialogElement.prototype.showModal = function (...args) {
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    if (scrollbarWidth > 0) {
      document.documentElement.style.paddingRight = `${scrollbarWidth}px`;
    }

    this.addEventListener(
      "close",
      () => {
        document.documentElement.style.paddingRight = "";
      },
      { once: true }
    );

    return nativeShowModal.apply(this, args);
  };
})();

onEveryPage(() => {
  // A dialog removed by an htmx page swap never fires "close".
  document.documentElement.style.paddingRight = "";

  const modalId = document.querySelector("main[data-open-modal]")?.dataset.openModal;
  if (!modalId) return;

  const dialog = document.getElementById(modalId);
  if (dialog) dialog.showModal();
});
