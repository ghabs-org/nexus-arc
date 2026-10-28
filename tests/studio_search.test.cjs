const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const studioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");
const indexHtml = fs.readFileSync("nexus/studio_static/index.html", "utf8");
const studioCss = fs.readFileSync("nexus/studio_static/studio.css", "utf8");

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

const badge = (text, status = "") =>
  `<span class="badge ${status}">${escapeHtml(text)}</span>`;

test("index.html renders global search in topbar and search fields on all resource pages", () => {
  assert.match(indexHtml, /id="global-search-input"/);
  assert.match(indexHtml, /id="global-search-menu"/);
  assert.match(indexHtml, /id="global-search-results"/);
  assert.match(indexHtml, /id="agents-search-input"/);
  assert.match(indexHtml, /data-search-target="agents-content"/);
  assert.match(indexHtml, /id="workflows-search-input"/);
  assert.match(indexHtml, /data-search-target="workflows-content"/);
  assert.match(indexHtml, /id="tools-search-input"/);
  assert.match(indexHtml, /data-search-target="tools-content"/);
  assert.match(indexHtml, /id="library-search-input"/);
  assert.match(indexHtml, /data-search-target="library-content"/);
  assert.match(indexHtml, /id="activity-search-input"/);
  assert.match(indexHtml, /data-search-target="activity-content"/);
  assert.match(indexHtml, /id="projects-search-input"/);
  assert.match(indexHtml, /data-search-target="projects-content"/);
});

test("studio.css defines styles for global search and page-level search bars", () => {
  assert.match(studioCss, /\.global-search-container/);
  assert.match(studioCss, /\.global-search-menu/);
  assert.match(studioCss, /\.page-search-bar/);
  assert.match(studioCss, /\.page-search-input/);
  assert.match(studioCss, /\.page-search-empty/);
});

test("searchStudio searches across pages, agents, workflows, tools, skills, and sessions", () => {
  const context = vm.createContext({
    state: {
      agents: [
        {
          id: "researcher",
          document: {
            metadata: { name: "Web Researcher" },
            spec: { provider: "codex", purpose: "Perform in-depth web research", skill_ids: ["search-skill"] },
          },
        },
        {
          id: "copywriter",
          document: {
            metadata: { name: "Copy Specialist" },
            spec: { provider: "claude-code", purpose: "Write engaging articles" },
          },
        },
      ],
      workflows: [
        {
          id: "research-pipeline",
          document: {
            metadata: { name: "Research Pipeline", description: "Automates multi-agent research" },
            steps: [{ agent_type: "researcher" }, { agent_type: "copywriter" }],
          },
        },
      ],
      tools: [
        { id: "studio:123:web_fetch", description: "Fetch web pages", category: "network" },
        { id: "studio:456:file_read", description: "Read workspace file", category: "filesystem" },
      ],
      skills: [
        { id: "search-skill", name: "Search Skill", description: "Performs web queries" },
      ],
      projects: [
        { id: "proj-1", name: "Alpha Project", instructions: "Knowledge base for Alpha" },
      ],
      sessions: [
        { id: "sess-1", title: "Debugging Web Fetcher" },
      ],
    },
  });

  const searchFnSource = studioSource.slice(
    studioSource.indexOf("function searchStudio("),
    studioSource.indexOf("function renderGlobalSearchResults(")
  );
  vm.runInContext(searchFnSource, context);

  // Empty query returns top page links
  const emptyRes = context.searchStudio("");
  assert.ok(emptyRes.pages.length > 0);
  assert.equal(emptyRes.agents.length, 0);

  // Search 'research': matches agent, workflow, and skill
  const res1 = context.searchStudio("research");
  assert.ok(res1.agents.some((a) => a.id === "researcher"));
  assert.ok(res1.workflows.some((w) => w.id === "research-pipeline"));

  // Search 'web_fetch': matches tool
  const res2 = context.searchStudio("web_fetch");
  assert.ok(res2.tools.some((t) => t.id === "studio:123:web_fetch"));

  // Search 'Debugging': matches conversation session
  const res3 = context.searchStudio("debugging");
  assert.ok(res3.sessions.some((s) => s.id === "sess-1"));

  // Search page 'Workflow': matches page
  const res4 = context.searchStudio("workflow");
  assert.ok(res4.pages.some((p) => p.id === "workflows"));
});

test("filterResourceCards filters matching resource cards and displays empty note when none match", () => {
  const filterFnSource = studioSource.slice(
    studioSource.indexOf("function filterResourceCards("),
    studioSource.indexOf("let activeSearchIndex")
  );

  function createMockCard(text) {
    return {
      hidden: false,
      textContent: text,
    };
  }

  const card1 = createMockCard("Web Researcher codex Perform in-depth web research");
  const card2 = createMockCard("Copy Specialist claude-code Write engaging articles");
  const cards = [card1, card2];
  let emptyNote = null;

  const mockContainer = {
    querySelectorAll(sel) {
      if (sel.includes("resource-card")) return cards;
      if (sel.includes("section-subheading")) return [];
      return [];
    },
    querySelector(sel) {
      if (sel === ".page-search-empty") return emptyNote;
      return null;
    },
    appendChild(node) {
      emptyNote = node;
    },
  };

  const mockInput = { value: "research" };

  const context = vm.createContext({
    String,
    $: (id) => (id === "agents-content" ? mockContainer : id === "agents-search-input" ? mockInput : null),
    document: {
      createElement: (tag) => ({
        tagName: tag.toUpperCase(),
        className: "",
        textContent: "",
        hidden: false,
      }),
    },
  });

  vm.runInContext(filterFnSource, context);

  // Filter for 'research'
  context.filterResourceCards("agents-content", "agents-search-input", "agents");
  assert.equal(card1.hidden, false);
  assert.equal(card2.hidden, true);

  // Filter for nonexistent query: empty note shown
  mockInput.value = "nonexistent";
  context.filterResourceCards("agents-content", "agents-search-input", "agents");
  assert.equal(card1.hidden, true);
  assert.equal(card2.hidden, true);
  assert.ok(emptyNote);
  assert.equal(emptyNote.hidden, false);
  assert.match(emptyNote.textContent, /No agents match "nonexistent"/);

  // Clear query: all restored, empty note hidden
  mockInput.value = "";
  context.filterResourceCards("agents-content", "agents-search-input", "agents");
  assert.equal(card1.hidden, false);
  assert.equal(card2.hidden, false);
  assert.equal(emptyNote.hidden, true);
});

test("renderGlobalSearchResults builds grouped results and empty state", () => {
  const renderFnSource = studioSource.slice(
    studioSource.indexOf("function renderGlobalSearchResults("),
    studioSource.indexOf("function openGlobalSearch(")
  );

  let innerHTML = "";
  const mockResultsBox = {
    set innerHTML(val) {
      innerHTML = val;
    },
    get innerHTML() {
      return innerHTML;
    },
  };

  const context = vm.createContext({
    escapeHtml,
    badge,
    $: (id) => (id === "global-search-results" ? mockResultsBox : null),
    activeSearchIndex: -1,
  });

  vm.runInContext(renderFnSource, context);

  // When results exist
  context.renderGlobalSearchResults(
    {
      pages: [{ id: "agents", title: "Agents", desc: "Specialist team members" }],
      agents: [{ id: "researcher", name: "Web Researcher", desc: "Searches web" }],
      workflows: [],
      tools: [],
      skills: [],
      projects: [],
      sessions: [],
    },
    "agent"
  );

  assert.match(innerHTML, /class="global-search-group-title">Pages/);
  assert.match(innerHTML, /data-search-id="agents"/);
  assert.match(innerHTML, /class="global-search-group-title">Agents/);
  assert.match(innerHTML, /data-search-id="researcher"/);

  // When 0 results exist for query
  context.renderGlobalSearchResults(
    {
      pages: [],
      agents: [],
      workflows: [],
      tools: [],
      skills: [],
      projects: [],
      sessions: [],
    },
    "unknownquery"
  );

  assert.match(innerHTML, /class="global-search-empty"/);
  assert.match(innerHTML, /No results found for "unknownquery"/);
});

test("handleGlobalSearchSelect navigates and populates page search field", () => {
  const handlerFnSource = studioSource.slice(
    studioSource.indexOf("function handleGlobalSearchSelect("),
    studioSource.indexOf("function agentToolOptions(")
  );

  let closed = false;
  const mockAgentsInput = { value: "" };
  let filteredTarget = null;

  const context = vm.createContext({
    location: { hash: "" },
    closeGlobalSearch: () => {
      closed = true;
    },
    $: (id) => (id === "agents-search-input" ? mockAgentsInput : null),
    filterResourceCards: (targetId, inputId, name) => {
      filteredTarget = targetId;
    },
  });

  vm.runInContext(handlerFnSource, context);

  // Selecting an agent
  context.handleGlobalSearchSelect({
    dataset: { searchKind: "agent", searchId: "researcher" },
  });

  assert.equal(closed, true);
  assert.equal(context.location.hash, "agents");
  assert.equal(mockAgentsInput.value, "researcher");
  assert.equal(filteredTarget, "agents-content");
});

test("index.html detail dialog includes full view expand controls", () => {
  assert.match(indexHtml, /id="detail-toggle-expand"/);
  assert.match(indexHtml, /id="detail-expand-btn"/);
});

test("studio.css defines full view modal and table readability styles", () => {
  assert.match(studioCss, /#detail\.maximized/);
  assert.match(studioCss, /\.table-container/);
  assert.match(studioCss, /overflow-wrap:\s*normal\s*!important/);
  assert.match(studioCss, /word-break:\s*normal\s*!important/);
});

test("toggleDetailMaximized toggles .maximized class and updates expand buttons", () => {
  const toggleFnSource = studioSource.slice(
    studioSource.indexOf("function toggleDetailMaximized("),
    studioSource.indexOf('$("detail")?.addEventListener("cancel"')
  );

  const mockClasses = new Set();
  const mockDialog = {
    classList: {
      contains: (cls) => mockClasses.has(cls),
      toggle: (cls, force) => {
        if (force) mockClasses.add(cls);
        else mockClasses.delete(cls);
      },
    },
  };
  const mockExpandBtn = {
    attrs: {},
    innerHTML: "",
    setAttribute(k, v) { this.attrs[k] = v; },
  };
  const mockFooterBtn = { textContent: "" };

  const context = vm.createContext({
    $: (id) => {
      if (id === "detail") return mockDialog;
      if (id === "detail-toggle-expand") return mockExpandBtn;
      if (id === "detail-expand-btn") return mockFooterBtn;
      return null;
    },
  });

  vm.runInContext(toggleFnSource, context);

  // Toggle to maximized
  context.toggleDetailMaximized();
  assert.equal(mockClasses.has("maximized"), true);
  assert.equal(mockExpandBtn.attrs["aria-label"], "Exit full view");
  assert.equal(mockFooterBtn.textContent, "Exit full view");

  // Toggle back to normal
  context.toggleDetailMaximized();
  assert.equal(mockClasses.has("maximized"), false);
  assert.equal(mockExpandBtn.attrs["aria-label"], "Full view");
  assert.equal(mockFooterBtn.textContent, "Full view");

  // Force true
  context.toggleDetailMaximized(true);
  assert.equal(mockClasses.has("maximized"), true);

  // Force false
  context.toggleDetailMaximized(false);
  assert.equal(mockClasses.has("maximized"), false);
});

test("content.js renderContent wraps markdown tables in .table-container", () => {
  const contentSource = fs.readFileSync("nexus/studio_static/content.js", "utf8");
  assert.match(contentSource, /table\.parentElement\.classList\.contains\("table-container"\)/);
  assert.match(contentSource, /wrap\.className\s*=\s*"table-container"/);
});

test("studio.css pins header and close button so they stay visible when scrolled down", () => {
  const css = fs.readFileSync("nexus/studio_static/studio.css", "utf8");
  assert.match(css, /#detail(\[open\])?\s*\{[^}]*overflow:\s*hidden/);
  assert.match(css, /#detail(\[open\])?\s*\{[^}]*padding:\s*0/);
  assert.match(css, /#detail\s+\.dialog-heading\s*\{[^}]*position:\s*sticky/);
  assert.match(css, /#detail\s+\.dialog-heading\s*\{[^}]*top:\s*0/);
  assert.match(css, /#detail-content\s*\{[^}]*overflow-y:\s*auto/);
  assert.match(css, /#detail\s+footer\s*\{[^}]*position:\s*sticky/);
  assert.match(css, /#detail\s+footer\s*\{[^}]*bottom:\s*0/);
  assert.match(css, /\.dialog-heading\s*\{[^}]*position:\s*sticky/);
});

test("studio.css keeps detail dialog strictly hidden when not open", () => {
  const css = fs.readFileSync("nexus/studio_static/studio.css", "utf8");
  assert.match(css, /#detail:not\(\[open\]\)\s*\{\s*display:\s*none\s*!important\s*\}/);
  assert.match(css, /#detail\[open\]\s*\{[^}]*display:\s*flex/);
  assert.match(css, /dialog:not\(\[open\]\)\s*\{\s*display:\s*none\s*!important\s*\}/);
});

test("studio.js outside tap/click dismisses dialog on backdrop and preserves internal drag", () => {
  const latestStudioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");
  const startIndex = latestStudioSource.indexOf("let dialogClickStartedOutside=false;");
  const endIndex = latestStudioSource.indexOf("const button=event.target.closest(\"button\");");
  const dismissSlice = latestStudioSource.slice(startIndex, endIndex) + "});";

  let closed = false;
  let cancelFired = false;
  const mockDialog = {
    tagName: "DIALOG",
    open: true,
    getBoundingClientRect: () => ({ left: 100, top: 100, right: 600, bottom: 500 }),
    dispatchEvent: (e) => {
      cancelFired = true;
      return true; // not prevented
    },
    close: () => {
      closed = true;
      mockDialog.open = false;
    },
  };

  let registeredPointerDown = null;
  let registeredClick = null;

  const context = vm.createContext({
    Event: function (type, init) {
      this.type = type;
      this.cancelable = init?.cancelable;
    },
    document: {
      addEventListener: (type, fn) => {
        if (type === "pointerdown") registeredPointerDown = fn;
        if (type === "click") registeredClick = fn;
      },
    },
  });

  vm.runInContext(dismissSlice, context);
  assert.ok(registeredPointerDown);
  assert.ok(registeredClick);

  // 1. Outside click: tap down outside (50, 50) and click outside (50, 50)
  registeredPointerDown({ target: mockDialog, clientX: 50, clientY: 50 });
  registeredClick({ target: mockDialog, clientX: 50, clientY: 50 });
  assert.equal(cancelFired, true);
  assert.equal(closed, true);
  assert.equal(mockDialog.open, false);

  // 2. Inside interaction: tap down inside (250, 250) and release outside (50, 50) - e.g. text selection drag
  mockDialog.open = true;
  closed = false;
  cancelFired = false;
  const innerElement = { tagName: "DIV", open: false };
  registeredPointerDown({ target: innerElement, clientX: 250, clientY: 250 });
  registeredClick({ target: mockDialog, clientX: 50, clientY: 50 });
  assert.equal(closed, false);
  assert.equal(mockDialog.open, true);

  // 3. Inside click: tap down inside (250, 250) and click inside (250, 250)
  registeredPointerDown({ target: innerElement, clientX: 250, clientY: 250 });
  registeredClick({ target: innerElement, clientX: 250, clientY: 250 });
  assert.equal(closed, false);
  assert.equal(mockDialog.open, true);
});

test("index.html renders activity kind filter pills for workflows, quick builds, conversations, tools", () => {
  const html = fs.readFileSync("nexus/studio_static/index.html", "utf8");
  assert.match(html, /class="activity-filters"/);
  assert.match(html, /data-activity-filter="all"/);
  assert.match(html, /data-activity-filter="workflow"/);
  assert.match(html, /data-activity-filter="build"/);
  assert.match(html, /data-activity-filter="chat"/);
  assert.match(html, /data-activity-filter="tool"/);
});

test("studio.css defines styles for activity filter pills and workflow history dialog", () => {
  const css = fs.readFileSync("nexus/studio_static/studio.css", "utf8");
  assert.match(css, /\.activity-filters/);
  assert.match(css, /\.activity-filter-pill/);
  assert.match(css, /\.activity-filter-pill\.active/);
  assert.match(css, /\.workflow-history-list/);
  assert.match(css, /\.workflow-history-row/);
});

test("renderRuns filters by state.activityFilter for workflows, quick builds, and tools", () => {
  const latestStudioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");
  const renderRunsSlice = latestStudioSource.slice(
    latestStudioSource.indexOf("function renderRuns(){"),
    latestStudioSource.indexOf("async function refreshDefinitions(){")
  );

  let innerHTML = "";
  const mockActivityContent = {
    set innerHTML(val) { innerHTML = val; },
    get innerHTML() { return innerHTML; },
  };

  const sampleRuns = [
    { id: "run-1", kind: "workflow", task: "Deploy website", provider: "codex", status: "completed", created_at: "2026-10-28T00:00:00Z" },
    { id: "run-2", kind: "build", task: "Generate research team", provider: "claude-code", status: "completed", created_at: "2026-10-28T00:01:00Z" },
    { id: "run-3", kind: "chat", task: "Explain architecture", provider: "codex", status: "completed", created_at: "2026-10-28T00:02:00Z" },
    { id: "run-4", kind: "tool", task: "Read file", provider: "local", status: "completed", created_at: "2026-10-28T00:03:00Z" },
  ];

  const context = vm.createContext({
    escapeHtml: (s) => s || "",
    providerName: (p) => p || "",
    badge: (t, s) => `<span class="badge ${s}">${t}</span>`,
    empty: (t, d) => `<div class="empty">${t} - ${d}</div>`,
    filterResourceCards: () => {},
    $: (id) => (id === "activity-content" ? mockActivityContent : null),
    state: {
      activityFilter: "all",
      runs: sampleRuns,
    },
  });

  vm.runInContext(renderRunsSlice, context);

  // 1. All
  context.renderRuns();
  assert.match(innerHTML, /Deploy website/);
  assert.match(innerHTML, /Generate research team/);
  assert.match(innerHTML, /Explain architecture/);
  assert.match(innerHTML, /Read file/);

  // 2. Workflow only
  context.state.activityFilter = "workflow";
  context.renderRuns();
  assert.match(innerHTML, /Deploy website/);
  assert.doesNotMatch(innerHTML, /Generate research team/);
  assert.doesNotMatch(innerHTML, /Explain architecture/);
  assert.doesNotMatch(innerHTML, /Read file/);

  // 3. Quick build only
  context.state.activityFilter = "build";
  context.renderRuns();
  assert.doesNotMatch(innerHTML, /Deploy website/);
  assert.match(innerHTML, /Generate research team/);
  assert.doesNotMatch(innerHTML, /Explain architecture/);
  assert.doesNotMatch(innerHTML, /Read file/);

  // 4. Chat only
  context.state.activityFilter = "chat";
  context.renderRuns();
  assert.doesNotMatch(innerHTML, /Deploy website/);
  assert.doesNotMatch(innerHTML, /Generate research team/);
  assert.match(innerHTML, /Explain architecture/);
  assert.doesNotMatch(innerHTML, /Read file/);

  // 5. Tool only
  context.state.activityFilter = "tool";
  context.renderRuns();
  assert.doesNotMatch(innerHTML, /Deploy website/);
  assert.doesNotMatch(innerHTML, /Generate research team/);
  assert.doesNotMatch(innerHTML, /Explain architecture/);
  assert.match(innerHTML, /Read file/);
});

test("renderWorkflows includes History action on each workflow card", () => {
  const latestStudioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");
  const renderWorkflowsSlice = latestStudioSource.slice(
    latestStudioSource.indexOf("function renderWorkflows(){"),
    latestStudioSource.indexOf("const MODEL_INPUTS=")
  );

  let innerHTML = "";
  const mockWorkflowsContent = {
    set innerHTML(val) { innerHTML = val; },
    get innerHTML() { return innerHTML; },
  };

  const context = vm.createContext({
    escapeHtml: (s) => s || "",
    badge: (t, s) => `<span class="badge ${s}">${t}</span>`,
    empty: () => "",
    filterResourceCards: () => {},
    $: (id) => (id === "workflows-content" ? mockWorkflowsContent : id === "workflow-count" ? { textContent: "" } : null),
    state: {
      workflows: [
        {
          id: "wf-1",
          document: {
            metadata: { name: "Analysis Pipeline" },
            steps: [{ agent_type: "analyst" }],
          },
        },
      ],
    },
  });

  vm.runInContext(renderWorkflowsSlice, context);
  context.renderWorkflows();
  assert.match(innerHTML, /data-workflow-history="wf-1"/);
  assert.match(innerHTML, />History<\/button>/);
});

test("updateRunDetail renders the Fix in conversation action", () => {
  const latestStudioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");
  assert.match(latestStudioSource, /data-fix-run-chat/);
  assert.match(latestStudioSource, /Fix in conversation/);
  assert.doesNotMatch(latestStudioSource, /data-rerun-workflow/);
});

test("activity search matches a workflow id kept off-screen and hides rows", () => {
  const filterFnSource = studioSource.slice(
    studioSource.indexOf("function filterResourceCards("),
    studioSource.indexOf("let activeSearchIndex")
  );
  const mine = { hidden: false, textContent: "Evaluate the project workflow", dataset: { search: "qb-d2a60662-founder-playbook" } };
  const other = { hidden: false, textContent: "Propose useful skills workflow", dataset: { search: "qb-785cd38f-project_skill_discovery" } };
  const container = { querySelectorAll: (sel) => (sel.includes("activity-row") ? [mine, other] : []), querySelector: () => null, appendChild() {} };
  const context = vm.createContext({ String, $: (id) => (id === "activity-content" ? container : { value: "qb-d2a60662-founder-playbook" }), document: { createElement: () => ({}) } });
  vm.runInContext(filterFnSource, context);
  context.filterResourceCards("activity-content", "activity-search-input", "activity");
  assert.equal(mine.hidden, false);
  assert.equal(other.hidden, true);
  assert.match(studioSource, /data-search="\$\{escapeHtml\(r\.workflow_definition_id/);
  assert.match(fs.readFileSync("nexus/studio_static/studio.css", "utf8"), /\[hidden\]\{display:none!important\}/);
});

test("sidebar keeps recent conversations visible when the nav overflows", () => {
  const css = fs.readFileSync("nexus/studio_static/studio.css", "utf8");
  assert.match(css, /\.sidebar \.recents\{min-height:min\(180px,25dvh\)/);
});

test("Task details offers step re-run with extra input and two modes", () => {
  const features = fs.readFileSync("nexus/studio_static/features.js", "utf8");
  assert.match(features, /async function renderStepRerunActions\(/);
  assert.match(features, /rerun-step`,\{step_id:/);
  assert.match(features, /name="mode" value="continue"/);
  assert.match(features, /name="mode" value="only"/);
  assert.match(features, /renderStepRerunActions\(root,run\);/);
});

test("assistant chat shows workspace proposals with Apply and Reject", () => {
  const access = fs.readFileSync("nexus/studio_static/workspace-access.js", "utf8");
  assert.match(access, /async function renderWorkspaceProposals\(node,runId\)/);
  assert.match(access, /\["apply","Apply"\],\["reject","Reject"\]/);
  assert.match(access, /Nothing is written until you apply it/);
  assert.match(studioSource, /isStudioAssistant&&typeof renderWorkspaceProposals==="function"\)renderWorkspaceProposals\(reply,finished\.id\)/);
  assert.match(studioSource, /session\.studio_assistant&&m\.role==="assistant"&&m\.run_id/);
});
