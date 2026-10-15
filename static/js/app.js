// Behaviour shared by every page, without inline scripts (the Content-Security-Policy allows
// only scripts from this site). Dialogs are native <dialog> elements; no framework script.
(function () {
  "use strict";

  // --- Dialogs: <button data-dialog-open="id"> opens <dialog id="id">; [data-dialog-close] closes it.
  // A button that opens a dialog can fill its form: data-fill-month="3" sets the field named "month".
  function openDialog(dialog, trigger) {
    if (trigger) {
      for (const [key, value] of Object.entries(trigger.dataset)) {
        if (!key.startsWith("fill") || key.length === 4) continue;
        const name = key.charAt(4).toLowerCase() + key.slice(5);
        dialog.querySelectorAll(`[name="${CSS.escape(name)}"]`).forEach((field) => { field.value = value; });
      }
    }
    dialog.showModal();
  }

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-dialog-open]");
    if (opener) {
      const dialog = document.getElementById(opener.dataset.dialogOpen);
      if (dialog) openDialog(dialog, opener);
      return;
    }
    const closer = event.target.closest("[data-dialog-close]");
    if (closer) {
      closer.closest("dialog")?.close();
      return;
    }
    // A click on the backdrop (outside the dialog's box) closes it.
    if (event.target instanceof HTMLDialogElement && event.target.open) {
      const box = event.target.getBoundingClientRect();
      const inside = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
      if (!inside) event.target.close();
    }
  });

  // --- Forms with data-confirm="…" ask first, in #confirmModal (base.html).
  const confirmDialog = document.getElementById("confirmModal");
  if (confirmDialog) {
    const text = confirmDialog.querySelector("[data-confirm-text]");
    const accept = confirmDialog.querySelector("[data-confirm-accept]");
    let pending = null;
    document.addEventListener("submit", (event) => {
      const form = event.target;
      if (!form.dataset.confirm || form.dataset.confirmed === "yes") return;
      event.preventDefault();
      pending = { form, submitter: event.submitter };
      text.textContent = form.dataset.confirm;
      accept.className = `btn ${form.dataset.confirmButton || "btn-danger"}`;
      confirmDialog.showModal();
    });
    confirmDialog.addEventListener("close", () => { if (confirmDialog.returnValue !== "accepted") pending = null; });
    accept.addEventListener("click", () => {
      if (!pending) return;
      const { form, submitter } = pending;
      pending = null;
      confirmDialog.close("accepted");
      form.dataset.confirmed = "yes";
      form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
      delete form.dataset.confirmed;
    });
  }

  // --- The menu below lg: a drawer opened by [data-drawer-open], closed by its button, the backdrop or Escape.
  function setDrawer(id, open) {
    const drawer = document.getElementById(id);
    if (!drawer) return;
    const backdrop = document.querySelector(`[data-drawer-backdrop="${CSS.escape(id)}"]`);
    const opener = document.querySelector(`[data-drawer-open="${CSS.escape(id)}"]`);
    for (const element of [drawer, backdrop]) element?.toggleAttribute("data-open", open);
    opener?.setAttribute("aria-expanded", String(open));
    if (open) drawer.focus();
    else if (opener && drawer.contains(document.activeElement)) opener.focus();
  }
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-drawer-open]");
    if (opener) return setDrawer(opener.dataset.drawerOpen, true);
    const closer = event.target.closest("[data-drawer-close], [data-drawer-backdrop]");
    if (closer) setDrawer(closer.dataset.drawerClose || closer.dataset.drawerBackdrop, false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    document.querySelectorAll("nav[data-open]").forEach((drawer) => setDrawer(drawer.id, false));
    document.querySelectorAll("details[data-dropdown][open]").forEach((menu) => {
      menu.open = false;
      menu.querySelector("summary")?.focus();
    });
  });

  // --- Dropdowns (<details data-dropdown>) close when clicking elsewhere.
  document.addEventListener("click", (event) => {
    document.querySelectorAll("details[data-dropdown][open]").forEach((menu) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });

  // --- Messages with [data-dismiss] can be closed.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-dismissible] [data-dismiss]");
    if (button) button.closest("[data-dismissible]").remove();
  });

  // <select data-autosubmit> submits its form when changed (data-autosubmit-clear="name" empties a field first).
  document.addEventListener("change", (event) => {
    const field = event.target.closest("[data-autosubmit]");
    if (!field || !field.form) return;
    const clear = field.dataset.autosubmitClear;
    if (clear && field.form.elements[clear]) field.form.elements[clear].value = "";
    field.form.requestSubmit();
  });

  // <button data-print> prints the page (print:hidden leaves out the menu and buttons).
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-print]")) window.print();
  });

  // Chart bars carry their height as data (a percentage), not as an inline style the CSP would refuse.
  document.querySelectorAll("[data-height]").forEach((bar) => {
    const percent = Number(bar.dataset.height);
    if (Number.isFinite(percent)) bar.style.height = `${Math.min(Math.max(percent, 0), 100)}%`;
  });
})();
