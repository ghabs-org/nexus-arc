const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("nexus/studio_static/theme.js", "utf8");

function fixture({saved = null, dark = false, denied = false, writeDenied = false} = {}) {
  const events = {}, mediaEvents = {}, writes = [], frames = [];
  const store = {getItem: () => saved, setItem: (key, value) => { if (writeDenied) throw Error("Storage blocked"); writes.push([key, value]); saved = value; }};
  const pickers = [0, 1].map(() => ({value: "", events: {}, addEventListener(name, callback) { this.events[name] = callback; }}));
  const metas = [{content: ""}, {content: ""}], scheme = {content: "light dark"};
  const media = {matches: dark, addEventListener: (name, callback) => { mediaEvents[name] = callback; }};
  const root = {dataset: {}};
  const document = {
    readyState: "loading", documentElement: root,
    addEventListener: (name, callback) => { events[name] = callback; },
    querySelectorAll: selector => selector === "[data-theme-picker]" ? pickers : selector === 'meta[name="theme-color"]' ? metas : selector === 'meta[name="color-scheme"]' ? [scheme] : selector === "iframe" ? frames : [],
  };
  const window = {get localStorage() { if (denied) throw Error("Storage unavailable"); return store; }, matchMedia: () => media, addEventListener: (name, callback) => { events[name] = callback; }};
  vm.runInContext(source, vm.createContext({window, document}));
  return {theme: window.NexusTheme, root, pickers, events, writes, metas, scheme, store, frames,
    ready: () => events.DOMContentLoaded(),
    system(value) { media.matches = value; mediaEvents.change(); },
    stored: () => saved,
  };
}

test("saved theme applies before DOM ready and remains selected after reload", () => {
  const f = fixture({saved: "dark"});
  assert.equal(f.root.dataset.theme, "dark");
  assert.equal(f.root.dataset.themePreference, "dark");
  assert.equal(f.scheme.content, "dark");
  assert.ok(f.metas.every(meta => meta.content === "#15191c"));
  assert.equal(f.writes.length, 0);
  f.ready();
  f.pickers[0].value = "light"; f.pickers[0].events.change();
  assert.equal(f.pickers[1].value, "light");
  assert.deepEqual(f.writes, [["nexus-studio-theme", "light"]]);
  const reload = fixture({saved: f.stored(), dark: true});
  assert.equal(reload.root.dataset.theme, "light");
});

test("system follows OS changes while explicit light/dark ignore them", () => {
  const f = fixture(); f.ready();
  assert.equal(f.pickers[0].value, "system");
  f.system(true); assert.equal(f.root.dataset.theme, "dark");
  f.theme.setPreference("light"); f.system(true);
  assert.equal(f.root.dataset.theme, "light");
  f.theme.setPreference("dark"); f.system(false);
  assert.equal(f.root.dataset.theme, "dark");
  f.theme.setPreference("system");
  assert.equal(f.root.dataset.theme, "light");
  assert.equal(f.pickers[0].value, "system");
});

test("blocked reads/writes and invalid values retain a usable bounded picker", () => {
  for (const options of [{denied: true}, {writeDenied: true}, {saved: '<img onerror=bad>'}]) {
    const f = fixture(options); f.ready();
    assert.equal(f.theme.getPreference(), "system");
    f.theme.setPreference("dark"); assert.equal(f.root.dataset.theme, "dark");
    f.theme.setPreference("unexpected"); assert.equal(f.theme.getPreference(), "system");
  }
});

test("same-origin tabs synchronize choices without writing them back", () => {
  const f = fixture(); f.ready();
  f.events.storage({key: "unrelated", newValue: "dark", storageArea: f.store});
  assert.equal(f.root.dataset.theme, "light");
  f.events.storage({key: "nexus-studio-theme", newValue: "dark", storageArea: {}});
  assert.equal(f.root.dataset.theme, "light");
  f.events.storage({key: "nexus-studio-theme", newValue: "dark", storageArea: f.store});
  assert.equal(f.root.dataset.theme, "dark");
  assert.equal(f.pickers[1].value, "dark");
  f.events.storage({key: null, newValue: null, storageArea: f.store});
  assert.equal(f.theme.getPreference(), "system");
  assert.equal(f.writes.length, 0);
});

test("only registered inert HTML previews update their owned scheme metadata", () => {
  const f = fixture();
  const original = "<!doctype html><meta name='color-scheme' content='light dark'><meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'none'\"><p>Saved content</p>";
  const frame = {srcdoc: original}, other = {srcdoc: "Unrelated frame"};
  f.frames.push(frame, other); f.theme.trackPreview(frame);
  assert.match(frame.srcdoc, /content='light'/);
  f.theme.trackPreview(frame); f.theme.setPreference("dark");
  assert.equal(frame.srcdoc, original.replace("content='light dark'", "content='dark'"));
  assert.equal(other.srcdoc, "Unrelated frame");
  f.theme.setPreference("system");
  assert.equal(frame.srcdoc, original.replace("content='light dark'", "content='light'"));
});

test("forced dark and automatic dark retain identical palette declarations", () => {
  const css = fs.readFileSync("nexus/studio_static/studio.css", "utf8");
  const automatic = css.match(/:root:not\(\[data-theme="light"\]\)\{([^{}]*)\}/);
  const forced = css.match(/:root\[data-theme="dark"\]\{([^{}]*)\}/);
  assert.ok(automatic && forced);
  assert.equal(automatic[1].replace(/\s/g, ""), forced[1].replace(/\s/g, ""));
});
