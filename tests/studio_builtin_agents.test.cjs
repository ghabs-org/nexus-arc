const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

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
  `<span class="badge ${kind}">${escapeHtml(text)}</span>`;
const providerName = (p) => p || "Default provider";

test("agentOptions renders built-in group and custom specialists group", () => {
  const context = vm.createContext({
    escapeHtml,
    state: {
      agents: [
        { id: "researcher", document: { metadata: { name: "Researcher Agent" } } },
        { id: "writer", document: { metadata: { name: "Writer Agent" } } },
      ],
    },
  });

  const fnSource = studioSource.slice(
    studioSource.indexOf("function agentOptions("),
    studioSource.indexOf("function renderAgents(")
  );
  vm.runInContext(fnSource, context);

  const html = context.agentOptions("studio-assistant");
  assert.match(html, /<optgroup label="Built-in specialists">/);
  assert.match(html, /<option value="" >General assistant<\/option>/);
  assert.match(html, /<option value="studio-assistant" selected>Studio assistant<\/option>/);
  assert.match(html, /<optgroup label="Custom specialists">/);
  assert.match(html, /<option value="researcher" >Researcher Agent<\/option>/);
  assert.match(html, /<option value="writer" >Writer Agent<\/option>/);
});

test("agentOptions selects General assistant by default", () => {
  const context = vm.createContext({
    escapeHtml,
    state: { agents: [] },
  });

  const fnSource = studioSource.slice(
    studioSource.indexOf("function agentOptions("),
    studioSource.indexOf("function renderAgents(")
  );
  vm.runInContext(fnSource, context);

  const html = context.agentOptions("");
  assert.match(html, /<option value="" selected>General assistant<\/option>/);
  assert.match(html, /<option value="studio-assistant" >Studio assistant<\/option>/);
  assert.doesNotMatch(html, /<optgroup label="Custom specialists">/);
});

test("renderAgents displays Built-in specialists section and custom agents", () => {
  const elements = new Map();
  const getEl = (id) => {
    if (!elements.has(id)) {
      elements.set(id, { textContent: "", innerHTML: "", value: "" });
    }
    return elements.get(id);
  };

  const context = vm.createContext({
    $: getEl,
    escapeHtml,
    badge,
    providerName,
    empty: (title) => `<div class="empty">${title}</div>`,
    state: {
      agents: [
        {
          id: "custom-reviewer",
          document: {
            metadata: { name: "Custom Reviewer", description: "Reviews code" },
            spec: { provider: "codex", model: "code-model", timeout_seconds: 300, skill_ids: ["code-review"] },
          },
        },
      ],
    },
  });

  const sourceSlice = studioSource.slice(
    studioSource.indexOf("function agentOptions("),
    studioSource.indexOf("function renderWorkflows(")
  );
  vm.runInContext(sourceSlice, context);

  context.renderAgents();

  const content = getEl("agents-content").innerHTML;
  assert.match(content, /Built-in specialists/);
  assert.match(content, /General assistant/);
  assert.match(content, /data-agent=""/);
  assert.match(content, /Studio assistant/);
  assert.match(content, /data-agent="studio-assistant"/);
  assert.match(content, /href="#assistant"/);
  assert.match(content, /Custom specialists/);
  assert.match(content, /Custom Reviewer/);
  assert.match(content, /data-agent="custom-reviewer"/);

  const select = getEl("chat-agent");
  assert.match(select.innerHTML, /Built-in specialists/);
  assert.match(select.innerHTML, /Custom specialists/);
  assert.match(select.innerHTML, /Custom Reviewer/);
});

test("selectAgent sets worker_tools to none for studio-assistant", () => {
  const elements = new Map();
  elements.set("chat-agent", { value: "studio-assistant" });
  elements.set("chat-tools", { value: "web" });
  elements.set("chat-provider", { value: "codex" });
  elements.set("chat-model", { value: "" });

  const context = vm.createContext({
    $: (id) => elements.get(id) || { value: "" },
    state: { agents: [] },
  });

  const fnSource = studioSource.slice(
    studioSource.indexOf("function selectAgent("),
    studioSource.indexOf("function newChat(")
  );
  vm.runInContext(fnSource, context);

  context.selectAgent();
  assert.equal(elements.get("chat-tools").value, "none");
});

test("sendMessage sets studio_assistant: true and worker_tools: none when Studio assistant is selected", async () => {
  const elements = new Map();
  elements.set("chat-task", { value: "Where are files?" });
  elements.set("chat-agent", { value: "studio-assistant" });
  elements.set("chat-provider", { value: "codex" });
  elements.set("chat-model", { value: "" });
  elements.set("chat-tools", { value: "shell" });
  elements.set("chat-effort", { value: "" });
  elements.set("chat-project", { value: "" });
  elements.set("chat-artifacts", { selectedOptions: [] });
  elements.set("send-message", { disabled: false });
  elements.set("messages", {
    scrollTop: 0,
    scrollHeight: 0,
    replaceChildren() {},
    appendChild() {},
  });

  const runsSubmitted = [];
  const context = vm.createContext({
    $: (id) => elements.get(id) || { value: "", disabled: false },
    state: { busy: false, sessionId: null, agents: [] },
    location: { pathname: "/" },
    resolvePromptValues: async (t) => undefined,
    addMessage: (role, text, prov) => ({
      querySelector: () => ({ textContent: "" }),
      classList: { add() {} },
    }),
    providerName: (p) => p,
    api: async (url, payload) => {
      runsSubmitted.push({ url, payload });
      return { id: "run-123", session_id: "session-abc" };
    },
    pollRun: async (id, cb) => ({ status: "completed", output: "Answer" }),
    window: { dispatchEvent() {} },
    CustomEvent: class {},
    refreshSessions: async () => {},
    refreshRuns: async () => {},
  });

  const fnSource = studioSource.slice(
    studioSource.indexOf("async function sendMessage("),
    studioSource.indexOf("function inputField(")
  );
  vm.runInContext(fnSource, context);

  await context.sendMessage({ preventDefault() {} });

  assert.equal(runsSubmitted.length, 1);
  assert.equal(runsSubmitted[0].url, "/api/runs");
  const payload = runsSubmitted[0].payload;
  assert.equal(payload.task, "Where are files?");
  assert.equal(payload.studio_assistant, true);
  assert.equal(payload.agent_id, null);
  assert.equal(payload.worker_tools, "none");
});

test("click handler supports data-agent='' for General assistant and data-agent='studio-assistant'", () => {
  let newChatCalled = 0;
  let selectedVal = null;
  const elements = new Map();
  elements.set("chat-agent", { value: "" });

  const context = vm.createContext({
    $: (id) => elements.get(id) || { value: "" },
    state: { busy: false, agents: [] },
    notify() {},
    newChat() {
      newChatCalled++;
    },
    selectAgent() {
      selectedVal = elements.get("chat-agent").value;
    },
  });

  // Test General assistant click (data-agent="")
  let button = { dataset: { agent: "" } };
  if (button.dataset.agent !== undefined) {
    if (context.state.busy) return;
    context.newChat();
    context.$("chat-agent").value = button.dataset.agent;
    context.selectAgent();
  }
  assert.equal(newChatCalled, 1);
  assert.equal(selectedVal, "");

  // Test Studio assistant click (data-agent="studio-assistant")
  button = { dataset: { agent: "studio-assistant" } };
  if (button.dataset.agent !== undefined) {
    if (context.state.busy) return;
    context.newChat();
    context.$("chat-agent").value = button.dataset.agent;
    context.selectAgent();
  }
  assert.equal(newChatCalled, 2);
  assert.equal(selectedVal, "studio-assistant");
});

test("session restoration restores studio-assistant agent selection", () => {
  const session = {
    id: "sess-1",
    studio_assistant: true,
    agent_id: null,
    provider: "codex",
    messages: [],
  };

  const agentVal = session.studio_assistant ? "studio-assistant" : (session.agent_id || "");
  assert.equal(agentVal, "studio-assistant");

  const regularSession = {
    id: "sess-2",
    studio_assistant: false,
    agent_id: "my-agent",
    provider: "codex",
    messages: [],
  };
  const regularAgentVal = regularSession.studio_assistant ? "studio-assistant" : (regularSession.agent_id || "");
  assert.equal(regularAgentVal, "my-agent");
});

test("studio.js defines watchRun as an alias to pollRun on window and in scope", () => {
  const windowObj = {};
  const context = vm.createContext({
    window: windowObj,
    watchedRuns: new Map(),
    disconnectedRuns: new Set(),
    sessionStorage: { getItem() { return "[]"; }, setItem() {} },
    navigator: { onLine: true },
    document: { hidden: false },
    $: () => ({ hidden: false, textContent: "" }),
    api: async () => ({ status: "completed" }),
  });

  const fnSource = studioSource.slice(
    studioSource.indexOf("async function pollRun("),
    studioSource.indexOf("async function restoreRunMonitoring(")
  );
  vm.runInContext(fnSource, context);

  assert.equal(typeof windowObj.pollRun, "function");
  assert.equal(typeof windowObj.watchRun, "function");
  assert.equal(windowObj.watchRun, windowObj.pollRun);
  assert.equal(vm.runInContext("typeof watchRun", context), "function");
  assert.equal(vm.runInContext("watchRun === pollRun", context), true);
});
