// Indonesian addresses from the Kemendagri list: cascading selects (province → regency/city →
// district → kelurahan/desa) and the postcode. Choosing one level loads the next from the server
// and clears those below; the kelurahan/desa fixes the postcode. Typing a postcode fills the
// selects when one kelurahan/desa has it, or narrows them when several share it.
(function () {
  "use strict";

  const LEVELS = ["province", "regency", "district", "village"];

  document.querySelectorAll("form").forEach((form) => {
    const selects = Object.fromEntries(
      LEVELS.map((level) => [level, form.querySelector(`select[data-region-level="${level}"]`)])
    );
    if (!selects.province) return;
    const postal = form.querySelector("[data-region-postal-code]");
    const url = selects.province.dataset.regionUrl;

    const reset = (select) => {
      const empty = select.options[0] && select.options[0].value === "" ? select.options[0].text : "—";
      select.replaceChildren(new Option(empty, ""));
    };
    const showPostal = () => {
      const option = selects.village.selectedOptions[0];
      if (postal && option && option.value) postal.value = option.dataset.postalCode || "";
    };
    const fetchJson = async (query) => {
      const response = await fetch(`${url}?${query}`, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      return response.ok ? response.json() : null;
    };

    // Fill ``level`` with the children of ``parent``; keep only ``only`` codes when given.
    const load = async (level, parent, only) => {
      reset(selects[level]);
      const data = await fetchJson(`level=${level}&parent=${encodeURIComponent(parent)}`);
      if (!data) return;
      data.choices
        .filter((choice) => !only || only.has(choice.code))
        .forEach((choice) => {
          const option = new Option(choice.name, choice.code);
          if (choice.postal_code) option.dataset.postalCode = choice.postal_code;
          selects[level].add(option);
        });
    };

    LEVELS.slice(0, -1).forEach((level, index) => {
      selects[level].addEventListener("change", async () => {
        const below = LEVELS.slice(index + 1);
        below.forEach((name) => reset(selects[name]));
        if (postal) postal.value = "";
        const parent = selects[level].value;
        if (parent) await load(below[0], parent);
      });
    });
    selects.village.addEventListener("change", showPostal);

    if (postal) {
      postal.addEventListener("input", async () => {
        const postcode = postal.value.trim();
        if (!/^\d{5}$/.test(postcode)) return;
        const data = await fetchJson(`postcode=${postcode}`);
        if (!data || !data.villages.length || postal.value.trim() !== postcode) return;
        const villages = data.villages;
        const shared = (key) => (villages.every((v) => v[key] === villages[0][key]) ? villages[0][key] : null);
        const province = shared("province"), regency = shared("regency"), district = shared("district");
        if (!province) return; // the postcode spans provinces: choose by hand
        selects.province.value = province;
        await load("regency", province);
        if (!regency) return;
        selects.regency.value = regency;
        await load("district", regency);
        if (!district) return;
        selects.district.value = district;
        await load("village", district, new Set(villages.map((v) => v.code))); // only those with the postcode
        if (villages.length === 1) selects.village.value = villages[0].code;
      });
    }
  });
})();
