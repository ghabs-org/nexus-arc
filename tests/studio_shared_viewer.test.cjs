const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const source = fs.readFileSync('nexus/studio_static/shared.js', 'utf8');
const share = (title = 'Private evidence', expires = 2000000) => ({
  title, expires_at: new Date(expires).toISOString(),
  artifacts: [{id: 'selected', filename: 'selected.txt'}],
  messages: [{role: 'assistant', content: '**Approved**'}]
});
const response = (body, status = 200) => ({status, ok: status === 200, json: async () => body});

function setup() {
  const elements = new Map(), listeners = new Map(), requests = [], timers = new Map(), downloads = [];
  let now = 1000000, timer = 0;
  function node(tag = 'div') {
    return {tag, children: [], textContent: '', className: '', disabled: false,
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
      click() { downloads.push({href: this.href, filename: this.download}); }
    };
  }
  for (const id of ['shared-title','shared-status','shared-messages']) elements.set(id, node());
  const document = {
    hidden: false, getElementById: id => elements.get(id), createElement: node,
    addEventListener: (event, callback) => listeners.set(event, callback)
  };
  const context = vm.createContext({
    document, window: {addEventListener: (event, callback) => listeners.set(event, callback)},
    location: {hash: '#private/first'}, AbortController, Blob, Uint8Array,
    Date: class extends Date {static now() {return now;}},
    URL: {createObjectURL: () => 'blob:synthetic', revokeObjectURL() {}},
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    renderContent: (element, text) => {element.textContent = text;},
    setTimeout: (callback, delay) => {timers.set(++timer, {callback, delay});return timer;},
    clearTimeout: id => timers.delete(id),
    fetch: (path, options) => new Promise((resolve, reject) => requests.push({path, options, resolve, reject}))
  });
  vm.runInContext(source.replace(/\nloadSharedSnapshot\(\);\s*$/, ''), context);
  return {context, elements, listeners, requests, timers, downloads, clock: value => {now = value;}};
}

test('private viewer uses authorization, shared formatting, and no mutation requests', async () => {
  const view = setup(), pending = view.context.loadSharedSnapshot();
  const request = view.requests[0];
  assert.equal(request.path, '/api/studio/shares/first');
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(request.options.cache, 'no-store');
  assert.equal(request.options.method, undefined);
  request.resolve(response(share()));await pending;
  assert.equal(view.elements.get('shared-title').textContent, 'Private evidence');
  assert.equal(view.elements.get('shared-messages').children[1].children[1].className, 'message-body');
  assert.equal(view.elements.get('shared-messages').children[1].children[1].textContent, '**Approved**');
});

test('changing a copied link clears content immediately and ignores late JSON', async () => {
  const view = setup(), old = view.context.loadSharedSnapshot();
  let finishJson, jsonReady;
  const started = new Promise(resolve => {jsonReady = resolve;});
  view.requests[0].resolve({status: 200, ok: true, json: () => new Promise(resolve => {finishJson = resolve;jsonReady();})});
  await started;
  view.context.location.hash = '#private/expired';
  const current = view.listeners.get('hashchange')();
  assert.equal(view.requests[0].options.signal.aborted, true);
  assert.equal(view.elements.get('shared-messages').children.length, 0);
  view.requests[1].resolve(response({}, 404));await current;
  finishJson(share('Stale response'));await old;
  assert.equal(view.elements.get('shared-title').textContent, 'Shared conversation');
  assert.match(view.elements.get('shared-status').textContent, /unavailable, expired or revoked/);
  assert.equal(view.elements.get('shared-messages').children.length, 0);
});

test('unauthenticated, forbidden, revoked and expired responses never retain prior content', async () => {
  for (const status of [401,403,404]) {
    const view = setup(), first = view.context.loadSharedSnapshot();
    view.requests[0].resolve(response(share()));await first;
    const second = view.context.loadSharedSnapshot();
    assert.equal(view.elements.get('shared-messages').children.length, 0);
    view.requests[1].resolve(response({}, status));await second;
    assert.equal(view.elements.get('shared-messages').children.length, 0);
    assert.match(view.elements.get('shared-status').textContent, status === 401 ? /Sign in/ : /unavailable/);
  }
});

test('known expiry clears loaded content and invalidates a file response in flight', async () => {
  const view = setup(), pending = view.context.loadSharedSnapshot();
  view.requests[0].resolve(response(share()));await pending;
  const download = view.elements.get('shared-messages').children[0].onclick();
  view.clock(2000001);
  view.timers.values().next().value.callback();
  assert.equal(view.elements.get('shared-messages').children.length, 0);
  assert.equal(view.requests[1].options.signal.aborted, true);
  view.requests[1].resolve(response({filename: 'selected.txt', content_base64: 'U2VjcmV0'}));
  await download;
  assert.equal(view.downloads.length, 0);
});

test('public requests omit cookies, including downloads, and long expiry does not overflow timers', async () => {
  const view = setup();view.context.location.hash = '#public/token';
  const pending = view.context.loadSharedSnapshot();
  view.requests[0].resolve(response(share('Public evidence', 4000000000)));await pending;
  assert.equal(view.requests[0].path, '/api/public-shares/token');
  assert.equal(view.requests[0].options.credentials, 'omit');
  assert.equal(view.timers.values().next().value.delay, 2147483647);
  const download = view.elements.get('shared-messages').children[0].onclick();
  assert.equal(view.requests[1].path, '/api/public-shares/token/artifacts/selected');
  assert.equal(view.requests[1].options.credentials, 'omit');
  view.requests[1].resolve(response({filename: 'selected.txt', content_base64: 'U2VjcmV0'}));
  await download;
  assert.deepEqual(view.downloads, [{href: 'blob:synthetic', filename: 'selected.txt'}]);
});

test('returning from background or browser cache rechecks access using GET', async () => {
  const view = setup(), pending = view.context.loadSharedSnapshot();
  view.requests[0].resolve(response(share()));await pending;
  view.context.document.hidden = true;
  view.listeners.get('visibilitychange')();
  assert.equal(view.requests.length, 1);
  view.context.document.hidden = false;
  view.listeners.get('visibilitychange')();
  assert.equal(view.requests.length, 2);
  assert.equal(view.elements.get('shared-messages').children.length, 0);
  view.requests[1].resolve(response({}, 403));
  await Promise.resolve();
  view.listeners.get('pageshow')({persisted: false});
  assert.equal(view.requests.length, 2);
  view.listeners.get('pageshow')({persisted: true});
  assert.equal(view.requests.length, 3);
  view.requests[2].resolve(response({}, 404));
});
