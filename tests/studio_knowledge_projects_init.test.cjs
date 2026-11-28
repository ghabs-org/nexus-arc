const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const studioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");
const featuresSource = fs.readFileSync("nexus/studio_static/features.js", "utf8");

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

test("loadKnowledgeProjects populates chat-project select with non-archived projects", async () => {
  let selectHtml = "";
  const selectElem = {
    value: "",
    set innerHTML(val) { selectHtml = val; },
    get innerHTML() { return selectHtml; },
  };

  const context = vm.createContext({
    escapeHtml,
    state: {},
    $: (id) => (id === "chat-project" ? selectElem : null),
    api: async (path) => {
      if (path === "/api/studio/projects") {
        return {
          items: [
            { id: "proj-1", name: "Alpha Knowledge", archived: false },
            { id: "proj-2", name: "Beta Knowledge", archived: true },
            { id: "proj-3", name: "Gamma Knowledge", archived: false },
          ],
          retrieval_options: { embedding_kind: "local" },
        };
      }
      return {};
    },
  });

  const fnSource = studioSource.slice(
    studioSource.indexOf("async function loadKnowledgeProjects("),
    studioSource.indexOf("function skillOptions(")
  );
  vm.runInContext(fnSource, context);

  await context.loadKnowledgeProjects();

  assert.equal(context.state.knowledgeProjects.length, 3);
  assert.match(selectHtml, /<option value="">No project<\/option>/);
  assert.match(selectHtml, /<option value="proj-1">Alpha Knowledge<\/option>/);
  assert.doesNotMatch(selectHtml, /Beta Knowledge/);
  assert.match(selectHtml, /<option value="proj-3">Gamma Knowledge<\/option>/);
});

test("showView triggers loadKnowledgeProjects when view is chat", () => {
  assert.match(studioSource, /if\(name==="chat"\)loadKnowledgeProjects\(\)\.catch/);
});

test("bootstrap runs loadKnowledgeProjects alongside definitions and sessions", () => {
  assert.match(studioSource, /loadKnowledgeProjects\(\)/);
});

test("features.js triggers refreshFeatureViews for chat on hashchange", () => {
  assert.match(featuresSource, /\["chat","projects","feedback","sharing"\]\.includes\(view\(\)\)/);
});

test("features.js initializes immediately if state.ready is true", () => {
  assert.match(featuresSource, /if\(state\.ready\)initializeFeatures\(\);else window\.addEventListener\("studio-ready",initializeFeatures\)/);
});
