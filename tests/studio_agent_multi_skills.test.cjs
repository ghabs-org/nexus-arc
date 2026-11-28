const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const studioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");
const librarySource = fs.readFileSync("nexus/studio_static/library.js", "utf8");
const quickBuildSource = fs.readFileSync("nexus/studio_static/quick-build.js", "utf8");

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

test("agentSkillOptions renders checkboxes for all skills with multi-selection checked", () => {
  const context = vm.createContext({
    escapeHtml,
    state: {
      skills: [
        { id: "brag", name: "Brag", description: "Make a video" },
        { id: "design-taste-frontend", name: "Design Taste", description: "Frontend taste" },
        { id: "code-review", name: "Code Review", description: "Review code" },
      ],
    },
  });

  const fnSource = studioSource.slice(
    studioSource.indexOf("function agentSkillOptions("),
    studioSource.indexOf("function createSkillDialog(")
  );
  vm.runInContext(fnSource, context);

  const html = context.agentSkillOptions(["brag", "code-review"]);
  assert.match(html, /value="brag" checked/);
  assert.match(html, /value="design-taste-frontend" /);
  assert.doesNotMatch(html, /value="design-taste-frontend" checked/);
  assert.match(html, /value="code-review" checked/);
  assert.match(html, /Brag \(brag\)/);
  assert.match(html, /Code Review \(code-review\)/);
});

test("agentSkillOptions handles unregistered skills gracefully", () => {
  const context = vm.createContext({
    escapeHtml,
    state: { skills: [{ id: "brag", name: "Brag" }] },
  });

  const fnSource = studioSource.slice(
    studioSource.indexOf("function agentSkillOptions("),
    studioSource.indexOf("function createSkillDialog(")
  );
  vm.runInContext(fnSource, context);

  const html = context.agentSkillOptions(["unregistered-skill"]);
  assert.match(html, /value="unregistered-skill" checked/);
  assert.match(html, /\(not in catalog\)/);
});

test("saveEditor collects multiple checked skill_ids into array on agent save", async () => {
  const submitted = [];
  const notifications = [];
  let modalClosed = false;

  const entriesList = [
    ["name", "Multi Specialist"],
    ["description", "Has multiple skills"],
    ["provider", "claude-code"],
    ["model", ""],
    ["instructions", "Work well."],
    ["tool_ids", ""],
    ["timeout_seconds", "600"],
    ["guardrails_json", "{}"],
  ];

  const mockFormData = {
    getAll: (k) => (k === "skill_ids" ? ["brag", "design-taste-frontend"] : []),
    [Symbol.iterator]: function* () {
      yield* entriesList;
    },
  };

  const context = vm.createContext({
    state: {
      editor: { kind: "agent", identity: "multi-agent" },
    },
    FormData: function () {
      return mockFormData;
    },
    Object,
    api: async (url, data) => {
      submitted.push({ url, data });
      return { id: "multi-agent" };
    },
    refreshDefinitions: async () => {},
    notify: (msg) => notifications.push(msg),
    location: { hash: "" },
    $: (id) => ({
      disabled: false,
      textContent: "",
      close: () => {
        modalClosed = true;
      },
    }),
  });

  const saveFn = studioSource.slice(
    studioSource.indexOf("async function saveEditor("),
    studioSource.indexOf("function showDetail(")
  );
  vm.runInContext(saveFn, context);

  await context.saveEditor({ preventDefault: () => {}, target: {} });

  assert.equal(submitted.length, 1);
  assert.equal(submitted[0].url, "/api/agents");
  assert.deepEqual(submitted[0].data.skill_ids, ["brag", "design-taste-frontend"]);
  assert.equal(modalClosed, true);
  assert.equal(notifications[0], "Agent created.");
});

test("importSkill in library dispatches studio-resource-saved event and triggers loadSkills", async () => {
  const dispatched = [];
  let skillsLoaded = false;
  const notifications = [];

  const context = vm.createContext({
    escapeHtml,
    $: () => ({ disabled: false }),
    notify: (msg) => notifications.push(msg),
    refreshLibrary: async () => {},
    loadSkills: async () => {
      skillsLoaded = true;
    },
    window: {
      dispatchEvent: (evt) => dispatched.push(evt),
    },
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init?.detail;
      }
    },
    setTimeout: (fn) => fn(),
    document: {
      getElementById: () => null,
      querySelectorAll: () => [],
    },
    fieldsDialog: async (title) => {
      if (title === "Import a skill") {
        return { get: () => "https://github.com/owner/single-skill" };
      }
      return null;
    },
    api: async (url, payload) => {
      return {
        skill: { name: "single-skill", id: "single-skill" },
        imported_files: ["SKILL.md"],
        skipped_files: [],
      };
    },
  });

  const importFn = librarySource.slice(
    librarySource.indexOf("async function importSkill("),
    librarySource.indexOf('$("library-import").addEventListener')
  );
  vm.runInContext(importFn, context);
  const result = await context.importSkill();

  assert.equal(skillsLoaded, true);
  assert.equal(dispatched.length, 1);
  assert.equal(dispatched[0].type, "studio-resource-saved");
  assert.equal(dispatched[0].detail.kind, "skills");
  assert.equal(result.skill.name, "single-skill");
});

test("createSkillDialog markup provides option to import skill directly", () => {
  const cssSource = fs.readFileSync("nexus/studio_static/studio.css", "utf8");
  assert.match(studioSource, /class="secondary import-skill-trigger"/);
  assert.match(studioSource, /Have an existing skill repository or SKILL\.md\?/);
  assert.match(cssSource, /\.skills-select-header button/);
  assert.match(cssSource, /button\.new-skill-btn/);
  assert.match(cssSource, /font-size:11px!important/);
});

test("promptNewSkill bridges to importSkill when importRequested is true", async () => {
  let importSkillCalled = false;
  let skillsLoaded = false;
  const agentListMock = {
    querySelectorAll: () => [{ value: "existing-skill" }],
    innerHTML: "",
  };

  const context = vm.createContext({
    escapeHtml,
    createSkillDialog: async () => ({ importRequested: true }),
    importSkill: async () => {
      importSkillCalled = true;
      return { skill: { id: "imported-skill", name: "Imported Skill" } };
    },
    loadSkills: async () => {
      skillsLoaded = true;
    },
    $: (id) => (id === "agent-skills-list" ? agentListMock : null),
    agentSkillOptions: (checked) => `checked:${checked.join(",")}`,
    notify: () => {},
  });

  const promptFn = studioSource.slice(
    studioSource.indexOf("async function promptNewSkill("),
    studioSource.indexOf("// Model fields are rendered by many views")
  );
  vm.runInContext(promptFn, context);

  const res = await context.promptNewSkill();
  assert.equal(importSkillCalled, true);
  assert.equal(skillsLoaded, true);
  assert.equal(res.skill.id, "imported-skill");
  assert.equal(agentListMock.innerHTML, "checked:existing-skill,imported-skill");
});

test("agent editor renders compact new skill and import skill buttons in skills header", () => {
  assert.match(
    studioSource,
    /class="skills-select-actions"><button type="button" class="secondary new-skill-btn">\+ New skill…<\/button><button type="button" class="secondary import-skill-btn">Import skill…<\/button>/
  );
  assert.doesNotMatch(studioSource, /class="quiet new-skill-btn"/);
});

test("agent editor renders skill search filter input above skills list", () => {
  assert.match(
    studioSource,
    /<input type="search" class="skills-filter-input" placeholder="Filter skills…" aria-label="Filter skills">/
  );
  const cssSource = fs.readFileSync("nexus/studio_static/studio.css", "utf8");
  assert.match(cssSource, /\.skills-filter-input\{/);
});

test("quick build renders multi-skill checklist and filter search input", () => {
  assert.match(quickBuildSource, /class="skills-filter-input"/);
  assert.match(quickBuildSource, /data-build-agent-skills/);
  assert.match(quickBuildSource, /agentSkillOptions\(a\.skill_ids/);
  assert.doesNotMatch(
    quickBuildSource,
    /<select data-build-field="agents\.\$\{i\}\.skill_ids" class="agent-skill-select"/
  );
});

test("filterSkillsList filters skills by name, description and id while preserving checkboxes", () => {
  const filterFn = studioSource.slice(
    studioSource.indexOf("function filterSkillsList("),
    studioSource.indexOf("function handleSkillsFilter(")
  );
  const context = vm.createContext({
    String,
    document: {
      createElement: (tag) => ({
        tagName: tag.toUpperCase(),
        className: "",
        style: {},
        textContent: "",
        hidden: false,
      }),
    },
  });
  vm.runInContext(filterFn, context);

  function createMockItem(id, name, desc, checked = false) {
    const checkbox = { value: id, checked };
    const nameEl = { textContent: `${name} (${id})` };
    const descEl = { textContent: desc };
    return {
      hidden: false,
      querySelector(sel) {
        if (sel === ".skills-select-name") return nameEl;
        if (sel === ".skills-select-desc") return descEl;
        if (sel === 'input[type="checkbox"]') return checkbox;
        return null;
      },
    };
  }

  const item1 = createMockItem("brag", "Brag", "Turn site into video", true);
  const item2 = createMockItem("design-taste", "Design Taste", "Frontend polish", false);
  const item3 = createMockItem("code-review", "Code Review", "Review PR diffs", true);
  const items = [item1, item2, item3];
  let emptyNote = null;

  const mockList = {
    querySelectorAll(sel) {
      if (sel === ".skills-select-item") return items;
      return [];
    },
    querySelector(sel) {
      if (sel === ".skills-filter-empty") return emptyNote;
      return null;
    },
    appendChild(node) {
      emptyNote = node;
    },
  };

  // Filter for 'taste' (matches name in item2)
  context.filterSkillsList(mockList, "taste");
  assert.equal(item1.hidden, true);
  assert.equal(item2.hidden, false);
  assert.equal(item3.hidden, true);
  assert.equal(item1.querySelector('input[type="checkbox"]').checked, true); // checked state intact

  // Filter for 'video' (matches description in item1)
  context.filterSkillsList(mockList, "video");
  assert.equal(item1.hidden, false);
  assert.equal(item2.hidden, true);
  assert.equal(item3.hidden, true);

  // Filter for ID 'code-review'
  context.filterSkillsList(mockList, "code-review");
  assert.equal(item1.hidden, true);
  assert.equal(item2.hidden, true);
  assert.equal(item3.hidden, false);

  // Filter for nonexistent query: empty note shown
  context.filterSkillsList(mockList, "nonexistent");
  assert.equal(item1.hidden, true);
  assert.equal(item2.hidden, true);
  assert.equal(item3.hidden, true);
  assert.ok(emptyNote);
  assert.equal(emptyNote.hidden, false);
  assert.match(emptyNote.textContent, /No skills match "nonexistent"/);

  // Clear query: all unhidden, empty note hidden
  context.filterSkillsList(mockList, "");
  assert.equal(item1.hidden, false);
  assert.equal(item2.hidden, false);
  assert.equal(item3.hidden, false);
  assert.equal(emptyNote.hidden, true);
});

test("agentToolOptions renders checkboxes with action name and descriptions from state.tools", () => {
  const context = vm.createContext({
    escapeHtml,
    state: {
      tools: [
        { id: "studio:abc1234:web_fetch", description: "Fetch web pages" },
        { id: "studio:def5678:web_search", description: "Search the web" },
        { id: "read_file", description: "Read a local file" },
      ],
    },
  });

  const fnSource = studioSource.slice(
    studioSource.indexOf("function agentToolOptions("),
    studioSource.indexOf("function createSkillDialog(")
  );
  vm.runInContext(fnSource, context);

  const html = context.agentToolOptions(["studio:abc1234:web_fetch"]);
  assert.match(html, /name="tool_ids"/);
  assert.match(html, /value="studio:abc1234:web_fetch" checked/);
  assert.match(html, /web_fetch \(studio:abc1234:web_fetch\)/);
  assert.match(html, /Fetch web pages/);
  assert.match(html, /value="studio:def5678:web_search" /);
  assert.doesNotMatch(html, /value="studio:def5678:web_search" checked/);
  assert.match(html, /web_search \(studio:def5678:web_search\)/);
  assert.match(html, />read_file</);
});

test("agentToolOptions handles custom or unregistered tools gracefully", () => {
  const context = vm.createContext({
    escapeHtml,
    state: {
      tools: [{ id: "studio:abc:web_fetch", description: "Fetch web" }],
    },
  });

  const fnSource = studioSource.slice(
    studioSource.indexOf("function agentToolOptions("),
    studioSource.indexOf("function createSkillDialog(")
  );
  vm.runInContext(fnSource, context);

  const html = context.agentToolOptions(["custom_tool"]);
  assert.match(html, /value="custom_tool" checked/);
  assert.match(html, /\(custom tool\)/);
});

test("filterSkillsList on agent-tools-list displays 'No tools match' message", () => {
  const filterFn = studioSource.slice(
    studioSource.indexOf("function filterSkillsList("),
    studioSource.indexOf("function handleSkillsFilter(")
  );
  const context = vm.createContext({
    String,
    document: {
      createElement: (tag) => ({
        tagName: tag.toUpperCase(),
        className: "",
        style: {},
        textContent: "",
        hidden: false,
      }),
    },
  });
  vm.runInContext(filterFn, context);

  let emptyNote = null;
  const mockToolItem = {
    hidden: false,
    querySelector(sel) {
      if (sel === ".skills-select-name") return { textContent: "web_fetch" };
      if (sel === ".skills-select-desc") return { textContent: "Fetch web pages" };
      if (sel === 'input[type="checkbox"]') return { value: "studio:123:web_fetch", checked: false };
      return null;
    },
  };

  const mockToolList = {
    classList: {
      contains: (cls) => cls === "agent-tools-list",
    },
    querySelectorAll(sel) {
      return sel === ".skills-select-item" ? [mockToolItem] : [];
    },
    querySelector(sel) {
      return sel === ".skills-filter-empty" ? emptyNote : null;
    },
    appendChild(node) {
      emptyNote = node;
    },
  };

  context.filterSkillsList(mockToolList, "nomatch");
  assert.ok(emptyNote);
  assert.equal(emptyNote.hidden, false);
  assert.match(emptyNote.textContent, /No tools match "nomatch"/);
});

test("saveEditor collects multiple checked tool_ids into array on agent save", async () => {
  const submitted = [];
  const notifications = [];

  const entriesList = [
    ["name", "Web Agent"],
    ["description", "Has web tools"],
    ["provider", "codex"],
    ["model", ""],
    ["instructions", "Search and fetch."],
    ["timeout_seconds", "600"],
    ["guardrails_json", "{}"],
  ];

  const mockFormData = {
    getAll: (k) => {
      if (k === "skill_ids") return ["research-skill"];
      if (k === "tool_ids") return ["studio:123:web_fetch", "studio:456:web_search"];
      return [];
    },
    [Symbol.iterator]: function* () {
      yield* entriesList;
    },
  };

  const context = vm.createContext({
    state: {
      editor: { kind: "agent", identity: "web-agent" },
    },
    FormData: function () {
      return mockFormData;
    },
    Object,
    api: async (url, data) => {
      submitted.push({ url, data });
      return { id: "web-agent" };
    },
    refreshDefinitions: async () => {},
    notify: (msg) => notifications.push(msg),
    location: { hash: "" },
    $: (id) => ({
      disabled: false,
      textContent: "",
      close: () => {},
    }),
  });

  const saveFn = studioSource.slice(
    studioSource.indexOf("async function saveEditor("),
    studioSource.indexOf("function showDetail(")
  );
  vm.runInContext(saveFn, context);

  await context.saveEditor({
    preventDefault: () => {},
    target: {
      querySelector: (sel) => (sel.includes('input[name="tool_ids"]') ? {} : null),
    },
  });

  assert.equal(submitted.length, 1);
  assert.equal(submitted[0].url, "/api/agents");
  assert.deepEqual(submitted[0].data.tool_ids, [
    "studio:123:web_fetch",
    "studio:456:web_search",
  ]);
  assert.deepEqual(submitted[0].data.skill_ids, ["research-skill"]);
});

test("agent editor renders tool search filter input and tools checklist", () => {
  const freshStudioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");
  assert.match(
    freshStudioSource,
    /<input type="search" class="skills-filter-input" placeholder="Filter tools…" aria-label="Filter tools">/
  );
  assert.match(
    freshStudioSource,
    /<div class="skills-select-list agent-tools-list" id="agent-tools-list">\$\{agentToolOptions\(\)\}<\/div>/
  );
});

