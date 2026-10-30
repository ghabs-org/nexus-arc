// These checks prove declared palette contracts, not browser-computed layouts.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const css = fs.readFileSync("nexus/studio_static/studio.css", "utf8");
const dark = css.match(/@media[^{}]*prefers-color-scheme:\s*dark[^{}]*\{\s*:root\s*\{([^{}]*)\}/);
const lightSource = dark ? css.slice(0, dark.index) : css;

function declarations(source) {
  return Object.fromEntries([...source.matchAll(/(--[\w-]+)\s*:\s*([^;{}]+)/g)].map(match => [match[1], match[2].trim()]));
}
const light = Object.assign({}, ...[...lightSource.matchAll(/:root\s*\{([^{}]*)\}/g)].map(match => declarations(match[1])));

function palette(mode) {
  if (mode === "dark") assert.ok(dark, "Studio must declare a system dark palette");
  return mode === "dark" ? {...light, ...declarations(dark[1])} : light;
}
function color(tokens, name, seen = []) {
  assert.ok(!seen.includes(name), `Cyclic palette token: ${name}`);
  const value = tokens[name];
  assert.ok(value, `Missing palette token: ${name}`);
  const alias = value.match(/^var\((--[\w-]+)\)$/);
  if (alias) return color(tokens, alias[1], [...seen, name]);
  assert.match(value, /^#(?:[\da-f]{3}|[\da-f]{6})$/i, `Expected an opaque text/surface color for ${name}`);
  const hex = value.length === 4 ? value.slice(1).split("").map(part => part + part).join("") : value.slice(1);
  return [0, 2, 4].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255);
}
function luminance(channels) {
  return channels.map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
    .reduce((total, value, index) => total + value * [0.2126, 0.7152, 0.0722][index], 0);
}
function contrast(tokens, foreground, background, minimum) {
  const values = [luminance(color(tokens, foreground)), luminance(color(tokens, background))].sort((a, b) => a - b);
  const ratio = (values[1] + 0.05) / (values[0] + 0.05);
  assert.ok(ratio >= minimum, `${foreground} on ${background}: ${ratio.toFixed(3)}:1 must reach ${minimum}:1`);
}

for (const mode of ["light", "dark"]) {
  test(`${mode} shared text, selected controls and semantic states meet 4.5:1`, () => {
    const tokens = palette(mode);
    for (const surface of ["--surface", "--canvas", "--raised", "--selected"]) {
      for (const text of ["--ink", "--muted", "--accent", "--builder-muted"]) contrast(tokens, text, surface, 4.5);
    }
    for (const background of ["--primary", "--primary-hover", "--ink"]) contrast(tokens, "--primary-ink", background, 4.5);
    for (const [foreground, background] of [
      ["--green", "--green-bg"], ["--green-strong", "--green-bg"],
      ["--red", "--red-bg"], ["--blue", "--blue-bg"],
      ["--warning", "--warning-bg"], ["--node-ink", "--node-fill"],
      ["--ink", "--surface-muted"], ["--ink", "--surface-alt"],
    ]) contrast(tokens, foreground, background, 4.5);
  });

  test(`${mode} enabled control boundaries and diagram edges meet 3:1`, () => {
    const tokens = palette(mode);
    for (const surface of ["--surface", "--canvas", "--raised", "--selected"]) {
      contrast(tokens, "--control", surface, 3);
      contrast(tokens, "--control-hover", surface, 3);
    }
    contrast(tokens, "--node-border", "--node-fill", 3);
    contrast(tokens, "--graph-edge", "--canvas", 3);
  });
}

test("native startup and the workspace retain the same system palette", () => {
  const startup = fs.readFileSync("apps/studio-desktop/ui/startup.css", "utf8");
  const startupDark = startup.match(/@media[^{}]*prefers-color-scheme:\s*dark[^{}]*\{\s*:root\s*\{([^{}]*)\}/);
  assert.ok(startupDark, "Native startup needs the matching dark palette");
  const startupLight = Object.assign({}, ...[...startup.slice(0, startupDark.index).matchAll(/:root\s*\{([^{}]*)\}/g)].map(match => declarations(match[1])));
  for (const mode of ["light", "dark"]) {
    const native = mode === "dark" ? {...startupLight, ...declarations(startupDark[1])} : startupLight;
    for (const name of ["--canvas", "--ink", "--muted", "--accent"]) assert.deepEqual(color(native, name), color(palette(mode), name), `${mode} startup ${name} differs from workspace`);
    contrast(native, "--muted", "--canvas", 4.5);
  }
});

test("restricted HTML previews inherit the system theme without relaxing isolation", () => {
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; }
    append(...children) { this.children.push(...children); }
    setAttribute(key, value) { this.attributes[key] = value; }
  }
  let sanitized;
  const context = vm.createContext({
    document: {createElement: tag => new Element(tag)},
    DOMPurify: {sanitize: (text, options) => { sanitized = {text, options}; return "<p>Sanitized document</p>"; }},
  });
  const source = fs.readFileSync("nexus/studio_static/content.js", "utf8");
  vm.runInContext(source.slice(source.indexOf("function previewContent(")), context);
  const root = context.previewContent({kind: "html", text: "<style>untrusted</style><p>Document</p>"});
  const frame = root.children[0];
  assert.equal(frame.tag, "iframe");
  assert.equal(frame.attributes.sandbox, "");
  assert.equal(frame.referrerPolicy, "no-referrer");
  assert.match(frame.srcdoc, /<meta name=['"]color-scheme['"] content=['"]light dark['"]>/);
  assert.match(frame.srcdoc, /default-src 'none'; style-src 'none'; img-src 'none'; form-action 'none'; base-uri 'none'/);
  assert.ok(frame.srcdoc.endsWith("<p>Sanitized document</p>"));
  assert.ok(!frame.srcdoc.includes("untrusted"));
  for (const tag of ["meta", "style", "script", "form", "input", "iframe", "img"]) assert.ok(sanitized.options.FORBID_TAGS.includes(tag));
  for (const name of ["style", "href", "src", "srcset"]) assert.ok(sanitized.options.FORBID_ATTR.includes(name));
});
