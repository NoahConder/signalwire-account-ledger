// Applies a saved light/dark choice before the page paints. Without one, the page follows
// the system setting through CSS.
(function () {
  try {
    var saved = localStorage.getItem("ledger-theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (e) {
    /* storage is unavailable: follow the system setting */
  }
})();
