const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const studioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");
const librarySource = fs.readFileSync("nexus/studio_static/library.js", "utf8");

test("resolveLibraryKind and libraryKindHash keep library tabs and hash routing in sync", () => {
  const context = vm.createContext({
    location: { hash: "" },
  });
  const helper = librarySource.slice(
    librarySource.indexOf("const allLibraryKinds="),
    librarySource.indexOf("const libraryDescriptions=")
  );
  vm.runInContext(helper, context);

  // Default hash
  context.location.hash = "";
  assert.equal(context.resolveLibraryKind(), "prompts");

  // #library defaults to prompts
  context.location.hash = "#library";
  assert.equal(context.resolveLibraryKind(), "prompts");

  // #library/subkind
  context.location.hash = "#library/skills";
  assert.equal(context.resolveLibraryKind(), "skills");
  context.location.hash = "#library/artifacts";
  assert.equal(context.resolveLibraryKind(), "artifacts");
  context.location.hash = "#library/decisionmodels";
  assert.equal(context.resolveLibraryKind(), "decisionmodels");
  context.location.hash = "#library/unknown";
  assert.equal(context.resolveLibraryKind(), "prompts");

  // Standalone sidebar kinds
  context.location.hash = "#experiments";
  assert.equal(context.resolveLibraryKind(), "experiments");
  context.location.hash = "#evaluations";
  assert.equal(context.resolveLibraryKind(), "evaluations");
  context.location.hash = "#schedules";
  assert.equal(context.resolveLibraryKind(), "schedules");
  context.location.hash = "#connectors";
  assert.equal(context.resolveLibraryKind(), "connectors");
  context.location.hash = "#entrypoints";
  assert.equal(context.resolveLibraryKind(), "entrypoints");

  // Reverse mapping via libraryKindHash
  assert.equal(context.libraryKindHash("experiments"), "experiments");
  assert.equal(context.libraryKindHash("prompts"), "library");
  assert.equal(context.libraryKindHash("connectors"), "connectors");
  assert.equal(context.libraryKindHash("entrypoints"), "entrypoints");
  assert.equal(context.libraryKindHash("schedules"), "schedules");
  assert.equal(context.libraryKindHash("evaluations"), "evaluations");
  assert.equal(context.libraryKindHash("skills"), "library/skills");
  assert.equal(context.libraryKindHash("artifacts"), "library/artifacts");
  assert.equal(context.libraryKindHash("decisionmodels"), "library/decisionmodels");
});

test("view() and showView() in studio.js synchronize active nav and view-title with #experiments", () => {
  const elements = new Map();
  elements.set("team-login", { hidden: true });
  const navLinks = [
    { href: "#chat", classes: new Set() },
    { href: "#experiments", classes: new Set() },
    { href: "#library", classes: new Set() },
    { href: "#connectors", classes: new Set() },
  ];
  navLinks.forEach(link => {
    link.getAttribute = attr => (attr === "href" ? link.href : null);
    link.classList = {
      toggle(cls, on) {
        if (on) link.classes.add(cls);
        else link.classes.delete(cls);
      },
      contains(cls) {
        return link.classes.has(cls);
      },
    };
  });

  const views = [
    { id: "view-chat", hidden: false },
    { id: "view-library", hidden: false },
  ];

  const dom = {
    location: { hash: "#experiments" },
    document: {
      querySelectorAll(selector) {
        if (selector === ".view") return views;
        if (selector === "nav a") return navLinks;
        return [];
      },
      querySelector(selector) {
        const match = selector.match(/nav a\[href="([^"]+)"\]/);
        if (match) return navLinks.find(l => l.href === match[1]) || null;
        return null;
      },
    },
    $: id => {
      if (!elements.has(id)) elements.set(id, { hidden: true, textContent: "" });
      return elements.get(id);
    },
    state: {},
    api: async () => ({ items: [] }),
    loadSkills: async () => {},
    refreshRuns: async () => {},
    loadKnowledgeProjects: async () => {},
    renderTools: () => {},
    notify: () => {},
  };

  const context = vm.createContext(dom);
  const helpers = studioSource.slice(
    studioSource.indexOf("function view(){"),
    studioSource.indexOf("function agentOptions(")
  );
  vm.runInContext(helpers, context);

  // #experiments -> view() should be library
  assert.equal(context.view(), "library");

  // showView() on #experiments
  context.showView();
  const expLink = navLinks.find(l => l.href === "#experiments");
  const libLink = navLinks.find(l => l.href === "#library");
  assert.ok(expLink.classList.contains("active"), "Experiment Lab sidebar link must be active");
  assert.ok(!libLink.classList.contains("active"), "Library sidebar link must not be active");
  assert.equal(elements.get("view-title").textContent, "Experiment Lab");

  // showView() on #library
  dom.location.hash = "#library";
  assert.equal(context.view(), "library");
  context.showView();
  assert.ok(!expLink.classList.contains("active"), "Experiment Lab sidebar link must not be active");
  assert.ok(libLink.classList.contains("active"), "Library sidebar link must be active");
  assert.equal(elements.get("view-title").textContent, "Library");

  // showView() on #library/skills falls back to #library nav link
  dom.location.hash = "#library/skills";
  assert.equal(context.view(), "library");
  context.showView();
  assert.ok(libLink.classList.contains("active"), "Library sidebar link must be active for sub-resources");
  assert.ok(!expLink.classList.contains("active"));
  assert.equal(elements.get("view-title").textContent, "Library");
});

test("library tab click dispatches hash change to synchronize navigation rail and library collection", async () => {
  let clickListener = null;
  let refreshed = 0;
  const elements = new Map();
  const context = vm.createContext({
    location: { hash: "#library" },
    document: {
      addEventListener(name, fn) {
        if (name === "click") clickListener = fn;
      },
    },
    api: async () => ({ items: [] }),
    $: id => {
      if (!elements.has(id)) {
        elements.set(id, {
          showModal() {},
          close() {},
          addEventListener() {},
          textContent: "",
        });
      }
      return elements.get(id);
    },
    renderLibrary: () => {},
    refreshContextChoices: async () => {},
    refreshLibrary: async () => { refreshed++; },
    library: { kind: "prompts", items: [] },
  });

  const helper = librarySource.slice(
    librarySource.indexOf("const allLibraryKinds="),
    librarySource.indexOf("async function refreshLibrary(){")
  );
  const clickStart = librarySource.indexOf('document.addEventListener("click",async event=>{');
  const clickEnd = librarySource.indexOf('window.addEventListener("hashchange"', clickStart);
  const clickBlock = librarySource.slice(clickStart, clickEnd);

  vm.runInContext(helper + clickBlock, context);

  assert.ok(typeof clickListener === "function", "click listener must be registered");

  // Click Experiment Lab tab when at #library
  await clickListener({
    target: {
      closest(sel) {
        if (sel === "button") return { dataset: { libraryKind: "experiments" } };
        return null;
      },
    },
  });
  assert.equal(context.location.hash, "experiments", "Clicking Experiment Lab tab must update location.hash to experiments");

  // Click Prompts tab when at #experiments
  context.location.hash = "#experiments";
  await clickListener({
    target: {
      closest(sel) {
        if (sel === "button") return { dataset: { libraryKind: "prompts" } };
        return null;
      },
    },
  });
  assert.equal(context.location.hash, "library", "Clicking Prompts tab must update location.hash to library");
});
