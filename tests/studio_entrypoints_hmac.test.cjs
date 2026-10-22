"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "../nexus/studio_static/operations.js"),
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

function inputField(name, label, val = "", type = "text") {
  return `<label>${label}<input name="${name}" type="${type}" value="${val}"></label>`;
}

function targetField(data) {
  return `<label>Target<select name="target"><option value="workflow:triage">Workflow: Triage</option></select></label>`;
}

test("operationFields renders HMAC authentication and routing controls for entrypoints", () => {
  const context = vm.createContext({
    escapeHtml,
    inputField,
    targetField,
    queueMicrotask: (fn) => fn(),
    updateIncomingServiceFields: () => {},
    library: { kind: "entrypoints" },
  });

  const fnSource = source.slice(
    source.indexOf("function operationFields("),
    source.indexOf("function operationFormData(")
  );
  vm.runInContext(fnSource, context);

  const html = context.operationFields({
    data: {
      auth_mode: "hmac",
      slug: "acme-tickets",
      route_path: "/tickets",
      hmac_header: "X-Hub-Signature-256",
      hmac_algorithm: "sha256",
      hmac_prefix: "sha256=",
      hmac_encoding: "hex",
    },
  });

  assert.match(html, /name="auth_mode"/);
  assert.match(html, /<option value="hmac" selected>HMAC signature<\/option>/);
  assert.match(html, /name="slug"/);
  assert.match(html, /value="acme-tickets"/);
  assert.match(html, /name="route_path"/);
  assert.match(html, /value="\/tickets"/);
  assert.match(html, /name="hmac_header"/);
  assert.match(html, /value="X-Hub-Signature-256"/);
  assert.match(html, /name="hmac_algorithm"/);
  assert.match(html, /<option value="sha256" selected>SHA-256<\/option>/);
  assert.match(html, /name="hmac_prefix"/);
  assert.match(html, /value="sha256="/);
  assert.match(html, /name="hmac_encoding"/);
});

test("operationFormData parses HMAC webhook fields and leaves legacy bearer intact", () => {
  const context = vm.createContext({
    library: { kind: "entrypoints" },
    JSON,
  });

  const fnSource = source.slice(
    source.indexOf("function operationFormData("),
    source.indexOf("function operationActions(")
  );
  vm.runInContext(fnSource, context);

  // HMAC form
  const hmacForm = new Map([
    ["target", "workflow:triage"],
    ["entrypoint_type", "webhook"],
    ["auth_mode", "hmac"],
    ["token_env", "ACME_SECRET"],
    ["slug", "acme-helpdesk"],
    ["route_path", "/tickets"],
    ["hmac_header", "X-Hub-Signature-256"],
    ["hmac_algorithm", "sha256"],
    ["hmac_prefix", "sha256="],
    ["hmac_encoding", "hex"],
    ["identity_map", "{}"],
    ["allowed_channels", ""],
  ]);

  const parsedHmac = context.operationFormData(hmacForm);
  assert.equal(parsedHmac.target_kind, "workflow");
  assert.equal(parsedHmac.target_id, "triage");
  assert.equal(parsedHmac.auth_mode, "hmac");
  assert.equal(parsedHmac.slug, "acme-helpdesk");
  assert.equal(parsedHmac.route_path, "/tickets");
  assert.equal(parsedHmac.hmac_header, "X-Hub-Signature-256");
  assert.equal(parsedHmac.hmac_algorithm, "sha256");
  assert.equal(parsedHmac.hmac_prefix, "sha256=");
  assert.equal(parsedHmac.hmac_encoding, "hex");

  // Legacy bearer form
  const bearerForm = new Map([
    ["target", "agent:reviewer"],
    ["entrypoint_type", "webhook"],
    ["token_env", "NEXUS_TOKEN"],
    ["identity_map", "{}"],
    ["allowed_channels", ""],
  ]);

  const parsedBearer = context.operationFormData(bearerForm);
  assert.equal(parsedBearer.target_kind, "agent");
  assert.equal(parsedBearer.target_id, "reviewer");
  assert.equal(parsedBearer.auth_mode, "bearer");
  assert.equal(Object.hasOwn(parsedBearer, "slug"), false);
});
