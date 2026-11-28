// Studio run-completion and approval notifications contracts (IDEA-02)
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class MockElement {
  constructor(id = "") {
    this.id = id;
    this.checked = false;
    this.hidden = false;
    this.textContent = "";
    this.innerHTML = "";
    this.dataset = {};
  }
  addEventListener() {}
  focus() {}
  scrollIntoView() {}
  showModal() {}
  close() {}
  querySelector() { return new MockElement(); }
  querySelectorAll() { return []; }
  after() {}
  replaceChildren() {}
}

class MockNotification {
  static permission = "default";
  static permissionToGrant = "granted";
  static instances = [];
  static requestPermissionCalls = 0;

  static async requestPermission() {
    MockNotification.requestPermissionCalls++;
    MockNotification.permission = MockNotification.permissionToGrant;
    return MockNotification.permission;
  }

  constructor(title, options = {}) {
    this.title = title;
    this.options = options;
    this.onclick = null;
    MockNotification.instances.push(this);
  }

  close() {
    this.closed = true;
  }
}

function fixture(storageInitial = {}) {
  MockNotification.instances = [];
  MockNotification.permission = "default";
  MockNotification.permissionToGrant = "granted";
  MockNotification.requestPermissionCalls = 0;

  const storage = new Map(Object.entries(storageInitial));
  const localStorage = {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, val) => storage.set(key, String(val)),
    removeItem: key => storage.delete(key),
    clear: () => storage.clear()
  };

  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) elements.set(id, new MockElement(id));
    return elements.get(id);
  };

  const context = vm.createContext({
    localStorage,
    sessionStorage: localStorage,
    Notification: MockNotification,
    MutationObserver: class { observe() {} disconnect() {} },
    requestAnimationFrame: fn => setTimeout(fn, 0),
    setTimeout,
    clearTimeout,
    innerHeight: 800,
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    document: {
      hidden: false,
      body: { dataset: {}, classList: { add() {}, remove() {}, toggle() {} } },
      documentElement: { style: { setProperty() {} } },
      getElementById: id => $(id),
      createElement: () => new MockElement(),
      querySelector: () => new MockElement(),
      querySelectorAll: () => [],
      addEventListener() {}
    },
    window: {
      Notification: MockNotification,
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {},
      focus() {}
    },
    location: { hash: "", href: "http://localhost/", search: "" },
    navigator: { onLine: true },
    $,
    state: { runs: [], mode: "local", user: null, ready: true },
    api: async () => ({ items: [] }),
    notify: () => {},
    providerName: p => p || "default",
    escapeHtml: str => String(str || "").replace(/[<&"]/g, c => ({ "<": "&lt;", "&": "&amp;", "\"": "&quot;" }[c])),
    badge: (text, cls) => `<span class="badge ${cls}">${text}</span>`,
    empty: () => "<div></div>",
    filterResourceCards: () => {},
    renderRuns: () => {},
    renderAgents: () => {},
    renderWorkflows: () => {},
    renderSessions: () => {},
    renderContent: () => {},
    showRun: () => {},
    view: () => "activity"
  });

  const source = fs.readFileSync("nexus/studio_static/studio.js", "utf8");
  vm.runInContext(source, context);

  return {
    context,
    $,
    localStorage,
    Notification: MockNotification,
    run: code => vm.runInContext(code, context)
  };
}

test("notifications are strictly opt-in and default to disabled", () => {
  const f = fixture();
  assert.equal(f.run("areNotificationsOptedIn()"), false);
  assert.equal(f.localStorage.getItem("nexus-notifications-optin"), null);
});

test("enabling notifications requests permission and records opt-in when granted", async () => {
  const f = fixture();
  f.Notification.permission = "default";
  f.Notification.permissionToGrant = "granted";

  const result = await f.run("setNotificationsOptedIn(true)");
  assert.equal(result, true);
  assert.equal(f.run("areNotificationsOptedIn()"), true);
  assert.equal(f.localStorage.getItem("nexus-notifications-optin"), "true");
  assert.equal(f.Notification.requestPermissionCalls, 1);
});

test("denied permission leaves notifications disabled", async () => {
  const f = fixture();
  f.Notification.permission = "denied";

  const result = await f.run("setNotificationsOptedIn(true)");
  assert.equal(result, false);
  assert.equal(f.run("areNotificationsOptedIn()"), false);
  assert.notEqual(f.localStorage.getItem("nexus-notifications-optin"), "true");
});

test("disabling notifications updates localStorage and UI", async () => {
  const f = fixture({ "nexus-notifications-optin": "true" });
  assert.equal(f.run("areNotificationsOptedIn()"), true);

  const result = await f.run("setNotificationsOptedIn(false)");
  assert.equal(result, false);
  assert.equal(f.run("areNotificationsOptedIn()"), false);
  assert.equal(f.localStorage.getItem("nexus-notifications-optin"), "false");
});

test("dispatchRunNotification respects opt-in requirement", () => {
  const f = fixture(); // not opted in
  f.Notification.permission = "granted";

  const testRun = {
    id: "run-secret-123456",
    kind: "workflow",
    status: "completed",
    task: "SECRET PROMPT: Process financial credentials"
  };

  f.run(`dispatchRunNotification(${JSON.stringify(testRun)}, "completed")`);
  assert.equal(f.Notification.instances.length, 0);
});

test("dispatchRunNotification enforces minimal private-content limits", () => {
  const f = fixture({ "nexus-notifications-optin": "true" });
  f.Notification.permission = "granted";

  const testRun = {
    id: "run-abc123456789",
    kind: "workflow",
    status: "completed",
    task: "SECRET_PASSWORD_123: Process database dump containing private api_key",
    error: "Detailed internal traceback with secret credentials"
  };

  f.run(`dispatchRunNotification(${JSON.stringify(testRun)}, "completed")`);
  assert.equal(f.Notification.instances.length, 1);

  const notification = f.Notification.instances[0];
  assert.equal(notification.title, "Task completed");
  assert.ok(notification.options.body.includes("Run #run-abc1 (workflow) completed successfully."));

  // Verify ZERO leak of sensitive content
  assert.ok(!notification.options.body.includes("SECRET_PASSWORD"));
  assert.ok(!notification.options.body.includes("api_key"));
  assert.ok(!notification.options.body.includes("database dump"));
  assert.ok(!notification.options.body.includes("Detailed internal traceback"));
});

test("dispatchRunNotification deduplicates repeated events for the same run", () => {
  const f = fixture({ "nexus-notifications-optin": "true" });
  f.Notification.permission = "granted";

  const testRun = { id: "run-dedupe-9999", kind: "workflow", status: "completed" };

  f.run(`dispatchRunNotification(${JSON.stringify(testRun)}, "completed")`);
  f.run(`dispatchRunNotification(${JSON.stringify(testRun)}, "completed")`);
  f.run(`dispatchRunNotification(${JSON.stringify(testRun)}, "completed")`);

  assert.equal(f.Notification.instances.length, 1);
  assert.equal(notificationTag(f.Notification.instances[0]), "nexus-run-run-dedupe-9999");
});

function notificationTag(instance) {
  return instance.options.tag;
}

test("notification click routes to inbox for approval and activity for completion", () => {
  const f = fixture({ "nexus-notifications-optin": "true" });
  f.Notification.permission = "granted";

  const approvalRun = { id: "run-gate-1111", kind: "workflow", status: "waiting", awaiting_approval: [{ name: "Gate 1" }] };
  f.run(`dispatchRunNotification(${JSON.stringify(approvalRun)}, "awaiting_approval")`);
  assert.equal(f.Notification.instances.length, 1);

  f.Notification.instances[0].onclick();
  assert.equal(f.run("location.hash"), "inbox");

  const completedRun = { id: "run-done-2222", kind: "workflow", status: "completed" };
  f.run(`dispatchRunNotification(${JSON.stringify(completedRun)}, "completed")`);
  assert.equal(f.Notification.instances.length, 2);

  f.Notification.instances[1].onclick();
  assert.equal(f.run("location.hash"), "activity");
});
