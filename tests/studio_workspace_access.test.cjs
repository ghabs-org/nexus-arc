const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("nexus/studio_static/workspace-access.js", "utf8");

function setupContext() {
  const elements = new Map();
  const context = vm.createContext({
    escapeHtml: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"),
    $: (id) => {
      if (!elements.has(id)) {
        elements.set(id, {
          hidden: false,
          disabled: false,
          textContent: "",
          innerHTML: "",
          value: "",
          querySelector(sel) {
            return { textContent: "", set innerHTML(val) {} };
          },
          querySelectorAll() {
            return [];
          },
          addEventListener() {},
          replaceChildren() {},
        });
      }
      return elements.get(id);
    },
    btoa: (s) => Buffer.from(s, "binary").toString("base64"),
    Uint8Array,
    String,
    Number,
    encodeURIComponent,
    window: { addEventListener() {} },
    location: { hash: "files" },
    state: { ready: false, busy: false, providers: [] },
  });
  return { context, elements };
}

test("fileQuery encodes root and path parameters correctly", () => {
  const { context } = setupContext();
  vm.runInContext(source.slice(0, source.indexOf("async function refreshServerFiles")), context);
  const query = vm.runInContext('fileQuery("my root", "path/with spaces & symbols")', context);
  assert.equal(query, "root=my%20root&path=path%2Fwith%20spaces%20%26%20symbols");
});

test("fileParent returns parent directory path or empty string", () => {
  const { context } = setupContext();
  vm.runInContext(source.slice(0, source.indexOf("async function refreshServerFiles")), context);
  assert.equal(vm.runInContext('fileParent("a/b/c")', context), "a/b");
  assert.equal(vm.runInContext('fileParent("a")', context), "");
  assert.equal(vm.runInContext('fileParent("")', context), "");
});

test("workspaceFileRows escapes HTML and distinguishes directory from file size", () => {
  const { context } = setupContext();
  vm.runInContext(source.slice(0, source.indexOf("async function refreshServerFiles")), context);
  const rows = vm.runInContext(
    'workspaceFileRows([{name: "<script>alert(1)</script>", kind: "directory", size: 0}, {name: "test.txt", kind: "file", size: 1024}])',
    context
  );
  assert.ok(rows.includes("&lt;script&gt;alert(1)&lt;/script&gt;"));
  assert.ok(rows.includes("Folder"));
  assert.ok(rows.includes("1024 bytes"));
  assert.ok(rows.includes("Open folder"));
  assert.ok(rows.includes("Preview"));

  const emptyRows = vm.runInContext("workspaceFileRows([])", context);
  assert.equal(emptyRows, "<p>No exposed files in this folder.</p>");
});

test("fileBase64 encodes buffer correctly and rejects files larger than 16 MiB", async () => {
  const { context } = setupContext();
  vm.runInContext(
    source.slice(source.indexOf("async function fileBase64"), source.indexOf('$("files-root").addEventListener')),
    context
  );

  const smallFile = {
    size: 5,
    async arrayBuffer() {
      return Buffer.from("hello");
    },
  };
  context.smallFile = smallFile;
  const base64 = await vm.runInContext("fileBase64(smallFile)", context);
  assert.equal(base64, Buffer.from("hello").toString("base64"));

  const oversizedFile = {
    size: 16 * 1024 * 1024 + 1,
    async arrayBuffer() {
      return Buffer.alloc(0);
    },
  };
  context.oversizedFile = oversizedFile;
  await assert.rejects(
    vm.runInContext("fileBase64(oversizedFile)", context),
    /Upload at most 16 MiB/
  );
});

test("renderStudioHelp populates answer, notice, workspace path and resource links", () => {
  const { context, elements } = setupContext();
  vm.runInContext(
    source.slice(source.indexOf("function renderStudioHelp"), source.indexOf('$("studio-help-form").addEventListener')),
    context
  );

  const data = {
    notice: "Built-in help only.",
    answer: "Agent definitions are YAML files.",
    workspace: { path: "/vm/workspace" },
    sources: [{ id: "storage", title: "Where agents are saved", url: "#files" }],
    resources: {
      agents: [{ id: "coder", name: "Coder Agent", url: "#agents" }],
      workflows: [],
    },
  };
  context.helpData = data;
  vm.runInContext("renderStudioHelp(helpData)", context);
  const answerEl = elements.get("studio-help-answer");
  assert.ok(answerEl.innerHTML.includes("Built-in help only."));
  assert.ok(answerEl.innerHTML.includes("/vm/workspace"));
  assert.ok(answerEl.innerHTML.includes("Where agents are saved"));
  assert.ok(answerEl.innerHTML.includes("Coder Agent"));
  assert.ok(answerEl.innerHTML.includes("None saved yet."));
});

