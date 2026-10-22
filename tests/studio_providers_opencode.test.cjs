const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const studioSource = fs.readFileSync('nexus/studio_static/studio.js', 'utf8');

test('renderProviders displays Configuration and ready badge when OpenCode is logged in', () => {
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const badge = (text, kind='') => `<span class="badge ${escapeHtml(kind)}">${escapeHtml(text)}</span>`;

  let providersHtml = '';
  const dom = {
    'providers-content': {
      set innerHTML(val) { providersHtml = val; },
      get innerHTML() { return providersHtml; }
    }
  };

  const state = {
    mode: 'local',
    providers: [
      {
        id: 'opencode',
        name: 'OpenCode',
        kind: 'cli',
        configured: true,
        status: 'Logged in',
        setup: 'Logged in (~/.local/share/opencode/auth.json)\nCLI: npm install -g opencode-ai',
        models: [{ id: 'opencode/gpt-6.1-sol', name: 'GPT-6.1-Sol' }]
      },
      {
        id: 'copilot',
        name: 'GitHub Copilot',
        kind: 'cli',
        configured: false,
        status: 'Setup needed',
        setup: 'copilot login',
        models: [{ id: 'auto', name: 'Auto' }]
      }
    ]
  };

  const renderProvidersSlice = studioSource.slice(
    studioSource.indexOf('function renderProviders()'),
    studioSource.indexOf('function renderTools()')
  );

  const context = vm.createContext({
    $: id => dom[id],
    escapeHtml,
    badge,
    state,
    renderModelLists: () => {}
  });

  vm.runInContext(renderProvidersSlice, context);
  context.renderProviders();

  // OpenCode: configured=true -> button text is "Configuration", badge is "ready" with text "Logged in"
  assert.match(providersHtml, /data-provider="opencode">Configuration<\/button>/);
  assert.match(providersHtml, /<span class="badge ready">Logged in<\/span>/);

  // Copilot: configured=false -> button text is "Setup instructions", badge is "" with text "Setup needed"
  assert.match(providersHtml, /data-provider="copilot">Setup instructions<\/button>/);
  assert.match(providersHtml, /<span class="badge ">Setup needed<\/span>/);
});
