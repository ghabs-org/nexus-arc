const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const studioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");
const librarySource = fs.readFileSync("nexus/studio_static/library.js", "utf8");

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
