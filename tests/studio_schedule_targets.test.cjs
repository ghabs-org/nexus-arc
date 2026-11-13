const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const operationsSource = fs.readFileSync("nexus/studio_static/operations.js", "utf8");
const studioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

const badge = (text, kind = "") =>
  `<span class="badge ${escapeHtml(kind)}">${escapeHtml(text)}</span>`;

test("targetField renders optgroups distinguishing workflows and agents", () => {
  const context = vm.createContext({
    escapeHtml,
    state: {
      workflows: [
        { id: "review-flow", document: { metadata: { name: "Review Pipeline" } } },
      ],
      agents: [
        { id: "reviewer", document: { metadata: { name: "Code Reviewer" } } },
      ],
    },
  });

  const fnSource = operationsSource.slice(
    operationsSource.indexOf("function targetField("),
    operationsSource.indexOf("function localDate(")
  );
  vm.runInContext(fnSource, context);

  const html = context.targetField({ target_kind: "workflow", target_id: "review-flow" });
  assert.match(html, /<optgroup label="Ready-made workflows">/);
  assert.match(html, /<optgroup label="Specialist agents">/);
  assert.match(html, /value="workflow:review-flow" selected/);
  assert.match(html, /value="agent:reviewer"/);
});

test("operationFields renders schedule fields with optional task and workflow input for workflows", () => {
  const context = vm.createContext({
    escapeHtml,
    targetField: () => '<select name="target"></select>',
    localDate: () => "2026-10-22T10:00",
    inputField: (name, label, val) => `<input name="${name}" value="${val}">`,
    library: { kind: "schedules" },
  });

  const fnSource = operationsSource.slice(
    operationsSource.indexOf("function operationFields("),
    operationsSource.indexOf("function operationFormData(")
  );
  vm.runInContext(fnSource, context);

  const wfHtml = context.operationFields({
    data: {
      target_kind: "workflow",
      target_id: "review-flow",
      workflow_input: { foo: "bar" },
    },
  });
  assert.match(wfHtml, /Task \/ input \(optional for workflows\)/);
  assert.match(wfHtml, /Optional task text \(leave blank to run the workflow directly\)/);
  assert.doesNotMatch(wfHtml, /<textarea name="task" id="schedule-task" required/);
  assert.match(wfHtml, /name="workflow_input_json"/);
  assert.match(wfHtml, /&quot;foo&quot;:\s*&quot;bar&quot;/);

  const agentHtml = context.operationFields({
    data: {
      target_kind: "agent",
      target_id: "reviewer",
    },
  });
  assert.match(agentHtml, /Task instructions/);
  assert.match(agentHtml, /<textarea name="task" id="schedule-task" required/);
  assert.match(agentHtml, /style="display:none"/);
});

test("operationFormData parses schedule target, optional task and workflow_input", () => {
  const context = vm.createContext({
    library: { kind: "schedules" },
  });

  const fnSource = operationsSource.slice(
    operationsSource.indexOf("function operationFormData("),
    operationsSource.indexOf("function operationActions(")
  );
  vm.runInContext(fnSource, context);

  const mockFormWf = {
    get: (key) => {
      const vals = {
        target: "workflow:my-pipeline",
        task: "  ",
        workflow_input_json: '{"flag": true}',
        run_at: "2026-10-22T12:00:00.000Z",
        interval_minutes: "30",
      };
      return vals[key] || null;
    },
    has: (key) => key === "enabled",
  };

  const wfData = context.operationFormData(mockFormWf);
  assert.equal(wfData.target_kind, "workflow");
  assert.equal(wfData.target_id, "my-pipeline");
  assert.equal(wfData.task, "");
  assert.deepEqual(JSON.parse(JSON.stringify(wfData.workflow_input)), { flag: true });
  assert.equal(wfData.interval_seconds, 1800);
  assert.equal(wfData.enabled, true);
});

test("operationActions displays badge distinguishing Workflow vs Agent schedules", () => {
  const context = vm.createContext({
    badge,
    escapeHtml,
    library: { kind: "schedules" },
  });

  const fnSource = operationsSource.slice(
    operationsSource.indexOf("function operationActions("),
    operationsSource.indexOf("async function refreshOperationResults(")
  );
  vm.runInContext(fnSource, context);

  const wfActions = context.operationActions({
    id: "wf-sched",
    data: { target_kind: "workflow", enabled: true },
  });
  assert.match(wfActions, />Workflow<\/span>/);

  const agentActions = context.operationActions({
    id: "agent-sched",
    data: { target_kind: "agent", enabled: false },
  });
  assert.match(agentActions, />Agent<\/span>/);
});

test("renderAgents and renderWorkflows include Schedule buttons", () => {
  assert.match(studioSource, /data-schedule-agent=/);
  assert.match(studioSource, /data-schedule-workflow=/);
});
