// Cascading address selects: province → regency/city → district → kelurahan/desa (Kemendagri
// codes). Choosing one level loads the next from the server and clears those below; the
// kelurahan/desa fixes the postcode.
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
      if (postal) postal.value = (option && option.dataset.postalCode) || "";
    };

    LEVELS.slice(0, -1).forEach((level, index) => {
      selects[level].addEventListener("change", async () => {
        const below = LEVELS.slice(index + 1);
        below.forEach((name) => reset(selects[name]));
        showPostal();
        const parent = selects[level].value;
        if (!parent) return;
        const next = below[0];
        const response = await fetch(`${url}?level=${next}&parent=${encodeURIComponent(parent)}`, {
          headers: { Accept: "application/json" }, credentials: "same-origin",
        });
        if (!response.ok || selects[level].value !== parent) return; // changed again meanwhile
        const { choices } = await response.json();
        choices.forEach((choice) => {
          const option = new Option(choice.name, choice.code);
          if (choice.postal_code) option.dataset.postalCode = choice.postal_code;
          selects[next].add(option);
        });
      });
    });
    selects.village.addEventListener("change", showPostal);
  });
})();
