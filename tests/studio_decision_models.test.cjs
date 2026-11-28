"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const librarySource = fs.readFileSync(
  path.join(__dirname, "../nexus/studio_static/library.js"),
  "utf8"
);
const operationsSource = fs.readFileSync(
  path.join(__dirname, "../nexus/studio_static/operations.js"),
  "utf8"
);
const indexHtml = fs.readFileSync(
  path.join(__dirname, "../nexus/studio_static/index.html"),
  "utf8"
);
const workbenchSource = fs.readFileSync(
  path.join(__dirname, "../nexus/studio_static/workbench.js"),
  "utf8"
);

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function badge(text, kind = "") {
  return `<span class="badge ${kind}">${escapeHtml(text)}</span>`;
}

test("library.js registers decisionmodels in operationKinds, libraryNames, and libraryDescriptions", () => {
  assert.match(librarySource, /operationKinds\s*=\s*\[[^\]]*"decisionmodels"[^\]]*\]/);
  assert.match(librarySource, /decisionmodels:\s*"decision model"/);
  assert.match(librarySource, /decisionmodels:\s*"Save decision models with backend/);
});

test("index.html surfaces decisionmodels library tab and workbench preset select", () => {
  assert.match(
    indexHtml,
    /<button data-library-kind="decisionmodels"[^>]*>Decision models<\/button>/
  );
  assert.match(
    indexHtml,
    /<select id="wb-decision-preset">/
  );
  assert.match(
    indexHtml,
    /<option value="openai">OpenAI<\/option>/
  );
});

test("operations.js operationFields renders decisionmodel form controls", () => {
  const context = vm.createContext({
    escapeHtml,
    inputField: (name, label, val = "", type = "text") =>
      `<label>${label}<input name="${name}" type="${type}" value="${val}"></label>`,
    library: { kind: "decisionmodels" },
  });

  const fnSource = operationsSource.slice(
    operationsSource.indexOf("function operationFields("),
    operationsSource.indexOf("function operationFormData(")
  );
  vm.runInContext(fnSource, context);

  const html = context.operationFields({
    data: {
      backend: "openrouter",
      model: "anthropic/claude-3.5-sonnet",
      timeout_seconds: 25,
    },
  });

  assert.match(html, /name="backend"/);
  assert.match(html, /<option value="openrouter" selected>OpenRouter<\/option>/);
  assert.match(html, /<option value="opencode" >OpenCode<\/option>/);
  assert.match(html, /<option value="openai" >OpenAI \(gpt-6-luna\)<\/option>/);
  assert.match(html, /name="model"/);
  assert.match(html, /value="anthropic\/claude-3.5-sonnet"/);
  assert.match(html, /name="timeout_seconds"/);
  assert.match(html, /value="25"/);
});

test("operations.js operationFormData parses decision model fields", () => {
  const context = vm.createContext({
    library: { kind: "decisionmodels" },
  });

  const fnSource = operationsSource.slice(
    operationsSource.indexOf("function operationFormData("),
    operationsSource.indexOf("function operationActions(")
  );
  vm.runInContext(fnSource, context);

  const fakeForm = {
    get: (key) => {
      const map = {
        backend: "openai",
        model: "gpt-6-luna ",
        timeout_seconds: "30",
      };
      return map[key] ?? null;
    },
    has: () => false,
  };

  const parsed = context.operationFormData(fakeForm);
  assert.equal(parsed.backend, "openai");
  assert.equal(parsed.model, "gpt-6-luna");
  assert.equal(parsed.timeout_seconds, 30);
});

test("operations.js operationActions renders badge and workbench launch button", () => {
  const context = vm.createContext({
    badge,
    escapeHtml,
    library: { kind: "decisionmodels" },
  });

  const fnSource = operationsSource.slice(
    operationsSource.indexOf("function operationActions("),
    operationsSource.indexOf("async function refreshOperationResults(")
  );
  vm.runInContext(fnSource, context);

  const actions = context.operationActions({
    id: "my-model",
    data: { backend: "openrouter" },
  });

  assert.match(actions, /class="badge ">openrouter<\/span>/);
  assert.match(actions, /data-decisionmodel-test="my-model"/);
  assert.match(actions, /Use in Workbench/);
});

test("workbench.js defines refreshDecisionPresets and updates form on preset selection", () => {
  assert.match(workbenchSource, /async function refreshDecisionPresets\(/);
  assert.match(workbenchSource, /wb-decision-preset/);
  assert.match(workbenchSource, /loadDecisionModels/);
});

test("studio.js and graph.js support decision model presets in workflow canvas", () => {
  const studioSource = fs.readFileSync(
    path.join(__dirname, "../nexus/studio_static/studio.js"),
    "utf8"
  );
  const graphSource = fs.readFileSync(
    path.join(__dirname, "../nexus/studio_static/graph.js"),
    "utf8"
  );

  assert.match(studioSource, /async function loadDecisionModels\(\)/);
  assert.match(studioSource, /state\.decisionmodels/);
  assert.match(graphSource, /graph-node-decision-model/);
  assert.match(graphSource, /node\.decision\.decision_model_id\s*=\s*chosen\.id/);
});
