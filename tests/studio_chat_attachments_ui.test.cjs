const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const workspaceSource = fs.readFileSync("nexus/studio_static/workspace.js", "utf8");
const studioSource = fs.readFileSync("nexus/studio_static/studio.js", "utf8");

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

function createMockElement(tag = "div") {
  const listeners = new Map();
  const classes = new Set();
  const el = {
    tag,
    hidden: false,
    disabled: false,
    value: "",
    textContent: "",
    innerHTML: "",
    dataset: {},
    options: [],
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
    },
    addEventListener: (event, handler) => {
      if (!listeners.has(event)) listeners.set(event, []);
      listeners.get(event).push(handler);
    },
    dispatchEvent: (event) => {
      const list = listeners.get(event.type) || [];
      for (const h of list) h(event);
    },
    closest: (selector) => {
      if (selector === "[data-remove-artifact]" && el.dataset.removeArtifact) return el;
      if (selector === "[data-toggle-artifact]" && el.dataset.toggleArtifact) return el;
      return null;
    },
    get selectedOptions() {
      return el.options.filter((o) => o.selected);
    },
  };
  return el;
}

test("formatBytes formats bytes into human-readable strings", () => {
  const context = vm.createContext({});
  const fnSource = workspaceSource.slice(
    workspaceSource.indexOf("function formatBytes("),
    workspaceSource.indexOf("function getFileCategory(")
  );
  vm.runInContext(fnSource, context);

  assert.equal(context.formatBytes(0), "0 B");
  assert.equal(context.formatBytes(512), "512 B");
  assert.equal(context.formatBytes(2048), "2.0 KB");
  assert.equal(context.formatBytes(15 * 1024 * 1024), "15.0 MB");
});

test("getFileCategory categorizes file types by extension", () => {
  const context = vm.createContext({});
  const fnSource = workspaceSource.slice(
    workspaceSource.indexOf("function getFileCategory("),
    workspaceSource.indexOf("function fileIconSvg(")
  );
  vm.runInContext(fnSource, context);

  assert.equal(context.getFileCategory("document.txt"), "text");
  assert.equal(context.getFileCategory("image.PNG"), "image");
  assert.equal(context.getFileCategory("paper.pdf"), "pdf");
  assert.equal(context.getFileCategory("script.py"), "code");
  assert.equal(context.getFileCategory("data.json"), "code");
});

test("renderChatArtifacts populates chips and gallery cards", () => {
  const elements = new Map();
  const getEl = (id) => {
    if (!elements.has(id)) elements.set(id, createMockElement());
    return elements.get(id);
  };

  const select = getEl("chat-artifacts");
  select.options = [
    { value: "art-1", textContent: "My Research · research.txt", selected: true, dataset: { size: "1024" } },
    { value: "art-2", textContent: "System Architecture · arch.pdf", selected: false, dataset: { size: "20480" } },
  ];

  const context = vm.createContext({
    $: getEl,
    escapeHtml,
    window: {},
  });

  const fns = workspaceSource.slice(
    workspaceSource.indexOf("function formatBytes("),
    workspaceSource.indexOf("let chatAttachmentEventsBound")
  );
  vm.runInContext(fns, context);

  context.renderChatArtifacts();

  // Active attached chips
  const chipsHtml = getEl("chat-attached-chips").innerHTML;
  assert.match(chipsHtml, /research\.txt/);
  assert.match(chipsHtml, /1\.0 KB/);
  assert.match(chipsHtml, /data-remove-artifact="art-1"/);

  // Counter & pill
  assert.equal(getEl("chat-attached-count").textContent, "1 / 5");
  assert.equal(getEl("chat-attachment-pill").textContent, "1 attached");
  assert.equal(getEl("chat-attachment-pill").hidden, false);
  assert.equal(getEl("chat-artifacts-total").textContent, "2 available");

  // Gallery cards
  const galleryHtml = getEl("chat-artifacts-gallery").innerHTML;
  assert.match(galleryHtml, /My Research/);
  assert.match(galleryHtml, /is-selected/);
  assert.match(galleryHtml, /System Architecture/);
  assert.match(galleryHtml, /arch\.pdf/);
});

test("setupChatAttachmentEvents wires toggle and remove actions", () => {
  const elements = new Map();
  const getEl = (id) => {
    if (!elements.has(id)) elements.set(id, createMockElement());
    return elements.get(id);
  };

  const select = getEl("chat-artifacts");
  select.options = [
    { value: "art-1", textContent: "File One · one.txt", selected: false, dataset: { size: "100" } },
  ];

  let notified = "";
  const context = vm.createContext({
    $: getEl,
    escapeHtml,
    notify: (msg) => { notified = msg; },
    attachUploadedFiles: async () => {},
    refreshModelChoices: async () => {},
    window: {},
  });

  const fns = workspaceSource.slice(
    workspaceSource.indexOf("function formatBytes("),
    workspaceSource.indexOf("if(typeof window!==\"undefined\")")
  );
  vm.runInContext(fns, context);

  context.setupChatAttachmentEvents();

  // Test toggling card on
  const details = getEl("chat-attachments-details");
  const card = createMockElement("button");
  card.dataset.toggleArtifact = "art-1";
  details.dispatchEvent({ type: "click", target: card, preventDefault() {} });

  assert.equal(select.options[0].selected, true);
  assert.equal(getEl("chat-attached-count").textContent, "1 / 5");

  // Test removing via chip
  const removeBtn = createMockElement("button");
  removeBtn.dataset.removeArtifact = "art-1";
  details.dispatchEvent({ type: "click", target: removeBtn, preventDefault() {}, stopPropagation() {} });

  assert.equal(select.options[0].selected, false);
  assert.equal(getEl("chat-attached-count").textContent, "0 / 5");
  assert.equal(getEl("chat-attachment-pill").hidden, true);
});

test("dropzone handles dragover, dragleave and drop", async () => {
  const elements = new Map();
  const getEl = (id) => {
    if (!elements.has(id)) elements.set(id, createMockElement());
    return elements.get(id);
  };

  let uploadedArgs = null;
  const context = vm.createContext({
    $: getEl,
    escapeHtml,
    notify: () => {},
    attachUploadedFiles: async (input, selId, statId, refresh) => {
      uploadedArgs = { input, selId, statId };
    },
    refreshModelChoices: async () => {},
    window: {},
  });

  const fns = workspaceSource.slice(
    workspaceSource.indexOf("function formatBytes("),
    workspaceSource.indexOf("if(typeof window!==\"undefined\")")
  );
  vm.runInContext(fns, context);

  context.setupChatAttachmentEvents();

  const dropzone = getEl("chat-dropzone");
  dropzone.dispatchEvent({ type: "dragover", preventDefault() {} });
  assert.equal(dropzone.classList.contains("is-dragover"), true);

  dropzone.dispatchEvent({ type: "dragleave", preventDefault() {} });
  assert.equal(dropzone.classList.contains("is-dragover"), false);

  const mockFile = { name: "drop.txt", size: 120 };
  await dropzone.dispatchEvent({
    type: "drop",
    preventDefault() {},
    dataTransfer: { files: [mockFile] },
  });

  assert.equal(uploadedArgs.selId, "chat-artifacts");
  assert.equal(uploadedArgs.input.files[0].name, "drop.txt");
});
