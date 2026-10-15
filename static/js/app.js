// Behaviour shared by every page, without inline scripts (the Content-Security-Policy allows
// only scripts from this site).
(function () {
  "use strict";

  // Forms with data-confirm="…" ask first, in a Bootstrap modal (#confirmModal in base.html).
  const modalElement = document.getElementById("confirmModal");
  if (modalElement && window.bootstrap) {
    const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
    const text = modalElement.querySelector("[data-confirm-text]");
    const accept = modalElement.querySelector("[data-confirm-accept]");
    let pending = null;
    document.addEventListener("submit", (event) => {
      const form = event.target;
      if (!form.dataset.confirm || form.dataset.confirmed === "yes") return;
      event.preventDefault();
      pending = { form, submitter: event.submitter };
      text.textContent = form.dataset.confirm;
      accept.className = `btn ${form.dataset.confirmButton || "btn-danger"}`;
      modal.show();
    });
    accept.addEventListener("click", () => {
      if (!pending) return;
      const { form, submitter } = pending;
      pending = null;
      modal.hide();
      form.dataset.confirmed = "yes";
      form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
      delete form.dataset.confirmed;
    });
  }

  // <select data-autosubmit> submits its form when changed (data-autosubmit-clear="name" empties a field first).
  document.addEventListener("change", (event) => {
    const field = event.target.closest("[data-autosubmit]");
    if (!field || !field.form) return;
    const clear = field.dataset.autosubmitClear;
    if (clear && field.form.elements[clear]) field.form.elements[clear].value = "";
    field.form.requestSubmit();
  });

  // <button data-print> prints the page (the print styles leave out the menu and buttons).
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-print]")) window.print();
  });

  // Chart bars carry their height as data (a percentage), not as an inline style the CSP would refuse.
  document.querySelectorAll("[data-height]").forEach((bar) => {
    const percent = Number(bar.dataset.height);
    if (Number.isFinite(percent)) bar.style.height = `${Math.min(Math.max(percent, 0), 100)}%`;
  });

  // A button that opens a modal can fill its form: data-fill-month="3" sets the field named "month".
  document.addEventListener("show.bs.modal", (event) => {
    const trigger = event.relatedTarget;
    if (!trigger) return;
    for (const [key, value] of Object.entries(trigger.dataset)) {
      if (!key.startsWith("fill") || key.length === 4) continue;
      const name = key.charAt(4).toLowerCase() + key.slice(5);
      event.target.querySelectorAll(`[name="${CSS.escape(name)}"]`).forEach((field) => { field.value = value; });
    }
  });
})();
