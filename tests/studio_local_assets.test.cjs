const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const crypto = require("node:crypto");
const path = "nexus/studio_static/", vendor = path + "vendor/";
const manifest = JSON.parse(fs.readFileSync(vendor + "studio-assets.json", "utf8"));
const digest = data => crypto.createHash("sha256").update(data).digest("hex");

test("bundled complete font faces retain their recorded bytes and original license", () => {
  const packages = manifest.packages.filter(item => item.files);
  assert.equal(packages.length, 2);
  assert.equal(packages.flatMap(item => item.files).length, 8);
  for (const item of packages) {
    assert.equal(item.license, "OFL-1.1");
    assert.match(fs.readFileSync(vendor + item.license_file, "utf8"), /SIL OPEN FONT LICENSE/i);
    for (const file of item.files) {
      const bytes = fs.readFileSync(vendor + file.file);
      assert.equal(bytes.subarray(0, 4).toString(), "wOF2");
      assert.equal(bytes.length, file.bytes);
      assert.equal(digest(bytes), file.sha256);
    }
  }
});

test("font declarations are local, swapped and backed by the pinned files", () => {
  const css = fs.readFileSync(path + "studio.css", "utf8");
  assert.doesNotMatch(css, /fonts\.googleapis|fonts\.gstatic|@import/);
  const faces = [...css.matchAll(/@font-face\{([^{}]+)\}/g)];
  const files = manifest.packages.flatMap(item => item.files || []);
  assert.equal(faces.length, files.length);
  for (const [, declarations] of faces) {
    assert.match(declarations, /font-display:swap/);
    const filename = declarations.match(/src:url\("\/vendor\/([^"/]+\.woff2)"\)/)?.[1];
    const file = files.find(item => item.file === filename);
    assert.ok(file, filename);
    assert.ok(declarations.includes(`font-weight:${file.weight}`));
    assert.ok(declarations.includes(`font-style:${file.style}`));
  }
});

test("the licensed sprite preserves the selected canonical outline paths", () => {
  const item = manifest.packages.find(item => item.sprite);
  const sprite = fs.readFileSync(vendor + item.sprite, "utf8");
  assert.equal(digest(sprite), item.sprite_sha256);
  assert.match(fs.readFileSync(vendor + item.license_file, "utf8"), /MIT License/);
  for (const icon of item.icons) {
    const inner = sprite.match(new RegExp(`<symbol id="${icon.id}" viewBox="0 0 24 24">([\\s\\S]*?)</symbol>`))?.[1].trim();
    assert.ok(inner, icon.id);
    assert.equal(digest(inner), icon.paths_sha256);
    assert.doesNotMatch(inner, /<script|<style|<foreignObject|<image|\bhref=|\bon\w+=|url\(/i);
  }
});

test("all first-party literal UI SVG icons resolve to the local family", () => {
  const item = manifest.packages.find(item => item.sprite);
  const ids = new Set(item.icons.map(icon => icon.id));
  let count = 0;
  for (const filename of ["index.html", "features.js", "workspace.js", "studio.js"]) {
    const source = fs.readFileSync(path + filename, "utf8");
    for (const [svg] of source.matchAll(/<svg\b[^>]*>[\s\S]*?<\/svg>/g)) {
      if (svg.includes('id="graph-canvas"')) continue; // Data visualization, not an icon.
      const id = svg.match(/<use href="\/vendor\/tabler-icons\.svg#([\w-]+)">/)?.[1];
      assert.ok(ids.has(id), `${filename}: unresolved glyph ${id}`);
      assert.match(svg, /aria-hidden="true"/);
      assert.match(svg, /stroke-width="1\.75"/);
      assert.doesNotMatch(svg, /<path|<polygon|<rect|<circle/);
      count++;
    }
  }
  assert.ok(count >= 26);
});

test("offline shell includes every local font and the icon sprite", () => {
  const source = fs.readFileSync(path + "service-worker.js", "utf8");
  const urls = JSON.parse(source.match(/const SHELL=(\[[^;]+\]);/)[1]);
  for (const item of manifest.packages) {
    for (const font of item.files || []) assert.ok(urls.includes("/vendor/" + font.file));
    if (item.sprite) assert.ok(urls.includes("/vendor/" + item.sprite));
  }
  assert.equal(new Set(urls).size, urls.length);
});
