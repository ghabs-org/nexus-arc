"use strict";
// Run in the document head so a saved choice is applied before the first paint.
(() => {
  const key = "nexus-studio-theme", choices = new Set(["system", "light", "dark"]);
  const root = document.documentElement, previews = new WeakMap();
  let storage = null, preference = "system";
  try { storage = window.localStorage; preference = normalize(storage.getItem(key)); } catch {}
  try {
    if (
      window.__TAURI__ ||
      window.__TAURI_INTERNALS__ ||
      String(window.location?.search || "").includes("desktop=1") ||
      String(document.cookie || "").includes("nexus_desktop=1") ||
      window.sessionStorage?.getItem("nexus-desktop") === "1"
    ) {
      root.classList?.add("desktop-app");
    }
  } catch {}
  const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function normalize(value) { return choices.has(value) ? value : "system"; }
  function resolved() { return preference === "system" ? (media?.matches ? "dark" : "light") : preference; }
  function updatePreview(frame) {
    const source = previews.get(frame);
    if (typeof source !== "string") return;
    const next = source.replace("<meta name='color-scheme' content='light dark'>", `<meta name='color-scheme' content='${resolved()}'>`);
    if (frame.srcdoc !== next) frame.srcdoc = next;
  }
  function apply() {
    const theme = resolved();
    root.dataset.theme = theme;
    root.dataset.themePreference = preference;
    document.querySelectorAll("[data-theme-picker]").forEach(picker => { picker.value = preference; });
    document.querySelectorAll('meta[name="theme-color"]').forEach(meta => { meta.content = theme === "dark" ? "#15191c" : "#173a36"; });
    document.querySelectorAll('meta[name="color-scheme"]').forEach(meta => { meta.content = theme; });
    document.querySelectorAll("iframe").forEach(updatePreview);
  }
  function setPreference(value) {
    preference = normalize(value);
    try { storage?.setItem(key, preference); } catch {}
    apply();
  }
  function bind() {
    document.querySelectorAll("[data-theme-picker]").forEach(picker => {
      picker.value = preference;
      picker.addEventListener("change", () => setPreference(picker.value));
    });
  }
  window.NexusTheme = {
    setPreference,
    getPreference: () => preference,
    getResolvedTheme: resolved,
    trackPreview(frame) { if (!previews.has(frame)) previews.set(frame, frame.srcdoc); updatePreview(frame); },
  };
  const systemChanged = () => { if (preference === "system") apply(); };
  if (media?.addEventListener) media.addEventListener("change", systemChanged);
  else media?.addListener?.(systemChanged);
  window.addEventListener("storage", event => {
    if (event.key !== null && event.key !== key) return;
    if (event.storageArea && storage && event.storageArea !== storage) return;
    preference = normalize(event.newValue);
    apply();
  });
  apply();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind, {once: true});
  else bind();
})();
