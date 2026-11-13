const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const source = fs.readFileSync('nexus/studio_static/features.js', 'utf8');
const share = source.slice(source.indexOf('async function shareConversation('), source.indexOf('document.addEventListener("click",async event=>{const b='));
const escapeHtml = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

function setup({selected = [], cancel = false, failure = false} = {}) {
  const calls = [], dialogs = [], details = [];
  const context = vm.createContext({
    state: {sessionId: 'original'}, escapeHtml, URL, location: {href: 'http://localhost/'},
    api: async path => {
      assert.equal(path, '/api/catalog/artifacts');
      if (failure) throw new Error('Files unavailable');
      return {items: [
        {id: 'selected', name: 'Policy', data: {filename: '<policy>.md'}},
        {id: 'omitted', name: 'Private notes', data: {filename: 'private.md'}},
        {id: 'archived', archived: true, data: {filename: 'archived.md'}}
      ]};
    },
    fieldsDialog: async (title, html, label, validate) => {
      dialogs.push({title, html, label, validate});
      context.state.sessionId = 'different-conversation';
      if (cancel) return null;
      const values = new Map([['expires', '168'], ['recipients', 'alice, bob']]);
      values.getAll = () => selected;
      assert.equal(validate(values), '');
      return values;
    },
    featureApi: async (path, payload) => {
      calls.push({path, payload});
      return {id: 'snapshot', artifacts: selected.map(id => ({id, filename: '<policy>.md', revision: 2}))};
    },
    showDetail: (title, html) => details.push({title, html})
  });
  vm.runInContext(share, context);
  return {context, calls, dialogs, details};
}

test('shares only explicit files and captures conversation before dialog awaits', async () => {
  const {context, calls, dialogs, details} = setup({selected: ['selected']});
  await context.shareConversation();
  const payload = calls[0].payload;
  assert.equal(payload.session_id, 'original');
  assert.deepEqual(Array.from(payload.artifact_ids), ['selected']);
  assert.equal(payload.public, false);
  assert.deepEqual(Array.from(payload.recipients), ['alice', 'bob']);
  assert.match(dialogs[0].html, /&lt;policy&gt;\.md/);
  assert.doesNotMatch(dialogs[0].html, /archived\.md| checked|<policy>/);
  assert.match(details[0].html, /1 selected file/);
  assert.match(details[0].html, /&lt;policy&gt;\.md · revision 2/);
  assert.match(dialogs[0].validate({getAll: () => Array(11).fill('id')}), /up to ten/);
});

test('messages-only sharing sends an empty file selection', async () => {
  const {context, calls, details} = setup();
  await context.shareConversation();
  assert.equal(calls[0].payload.artifact_ids.length, 0);
  assert.match(details[0].html, /0 selected files/);
});

test('cancel and failed file listing never create snapshots', async () => {
  const cancelled = setup({cancel: true});
  await cancelled.context.shareConversation();
  assert.equal(cancelled.calls.length, 0);
  const failed = setup({failure: true});
  await assert.rejects(failed.context.shareConversation(), /Files unavailable/);
  assert.equal(failed.calls.length, 0);
  assert.equal(failed.dialogs.length, 0);
});

test('requires an existing conversation', async () => {
  const {context, calls} = setup();
  context.state.sessionId = null;
  await assert.rejects(context.shareConversation(), /Start a conversation first/);
  assert.equal(calls.length, 0);
});
