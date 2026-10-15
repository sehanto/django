// Follow the device's light or dark setting (Bootstrap colour modes). Loaded in <head>, before
// the page is drawn, so it never flashes in the wrong mode.
(function () {
  "use strict";
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const apply = () => document.documentElement.setAttribute("data-bs-theme", query.matches ? "dark" : "light");
  apply();
  query.addEventListener("change", apply);
})();
