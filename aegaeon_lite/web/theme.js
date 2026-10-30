"use strict";
(() => {
  const key = "aegaeon-theme", choices = ["system", "light", "dark"];
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  let preference = "system";
  try { const saved = localStorage.getItem(key); if (choices.includes(saved)) preference = saved; } catch {}
  // The installer chooses a free port on launch. A host-scoped preference cookie
  // preserves appearance across those launches; it contains no project data.
  try {
    const saved = document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith("aegaeon_theme="))?.split("=")[1];
    if (choices.includes(saved)) preference = saved;
  } catch {}
  function apply() {
    document.documentElement.dataset.theme = preference === "system" ? (system.matches ? "dark" : "light") : preference;
    document.documentElement.dataset.themePreference = preference;
    const picker = document.getElementById("theme-choice");
    if (picker) picker.value = preference;
  }
  apply();
  system.addEventListener("change", apply);
  window.addEventListener("storage", event => {
    if (event.key === key || event.key === null) {
      preference = choices.includes(event.newValue) ? event.newValue : "system";
      apply();
    }
  });
  document.addEventListener("DOMContentLoaded", () => {
    const picker = document.getElementById("theme-choice");
    apply();
    picker.addEventListener("change", () => {
      preference = choices.includes(picker.value) ? picker.value : "system";
      try { localStorage.setItem(key, preference); } catch {}
      try { document.cookie = `aegaeon_theme=${preference}; Path=/; Max-Age=31536000; SameSite=Strict`; } catch {}
      apply();
    });
  });
})();
