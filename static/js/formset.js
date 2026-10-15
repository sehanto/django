// Dynamic formset rows: add/remove lines, item auto-fill and live totals.
(function () {
  "use strict";

  const parseNum = (v) => {
    const n = parseFloat(String(v || "").replace(",", "."));
    return Number.isFinite(n) ? n : 0;
  };
  // Digit grouping follows the page language (<html lang>), matching the server-side |rupiah filter.
  const locale = document.documentElement.lang === "en" ? "en-US" : "id-ID";
  const fmt = (n) => "Rp " + n.toLocaleString(locale, { minimumFractionDigits: 0, maximumFractionDigits: 2 });

  document.querySelectorAll("[data-formset]").forEach((root) => {
    const prefix = root.dataset.formset;
    const body = root.querySelector("[data-formset-body]");
    const template = root.querySelector("template[data-formset-empty]");
    const total = root.querySelector(`#id_${prefix}-TOTAL_FORMS`);
    const items = JSON.parse(document.getElementById("items-data")?.textContent || "{}");

    const recalc = () => {
      let debit = 0, credit = 0, subtotal = 0;
      body.querySelectorAll("tr[data-formset-row]").forEach((row) => {
        const deleted = row.querySelector('input[name$="-DELETE"]');
        if (deleted && deleted.checked) return;
        const val = (suffix) => parseNum(row.querySelector(`[name$="-${suffix}"]`)?.value);
        debit += val("debit");
        credit += val("credit");
        const qty = val("quantity"), price = val("unit_price"), disc = val("discount_percent");
        const amount = qty * price * (1 - disc / 100);
        const cell = row.querySelector("[data-line-amount]");
        if (cell) { cell.textContent = fmt(amount); subtotal += amount; }
      });
      const set = (sel, v) => { const el = root.querySelector(sel); if (el) el.textContent = fmt(v); };
      set("[data-total-debit]", debit);
      set("[data-total-credit]", credit);
      set("[data-subtotal]", subtotal);
      const balance = root.querySelector("[data-balance-status]");
      if (balance) {
        const ok = Math.abs(debit - credit) < 0.005 && debit > 0;
        // Labels come from the template so they follow the interface language.
        balance.textContent = ok ? root.dataset.labelBalanced : root.dataset.labelDifference + " " + fmt(Math.abs(debit - credit));
        balance.className = "badge " + (ok ? "badge-success" : "badge-warning");
      }
    };

    const fillFromItem = (select) => {
      const data = items[select.value];
      if (!data) return;
      const row = select.closest("tr");
      const setVal = (suffix, value) => {
        const el = row.querySelector(`[name$="-${suffix}"]`);
        if (el && value !== null && value !== undefined && !el.disabled) el.value = value;
      };
      setVal("description", data.description);
      setVal("unit_price", data.price);
      setVal("account", data.account);
      setVal("tax_rate", data.tax ?? "");
      recalc();
    };

    root.querySelector("[data-formset-add]")?.addEventListener("click", () => {
      const index = parseInt(total.value, 10);
      const html = template.innerHTML.replace(/__prefix__/g, index);
      body.insertAdjacentHTML("beforeend", html);
      total.value = index + 1;
      recalc();
    });

    body.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-formset-remove]");
      if (!btn) return;
      const row = btn.closest("tr");
      const del = row.querySelector('input[name$="-DELETE"]');
      if (del) { del.checked = true; row.classList.add("hidden"); }
      else { row.querySelectorAll("input, select").forEach((el) => { if (el.type !== "hidden") el.value = ""; }); }
      recalc();
    });

    body.addEventListener("input", recalc);
    body.addEventListener("change", (event) => {
      if (event.target.matches('select[name$="-item"]')) fillFromItem(event.target);
      recalc();
    });
    recalc();
  });
})();
