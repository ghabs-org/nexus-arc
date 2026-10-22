"""HTML for the Nexus sign-in and account setup pages served by the webhook server.

Framed-sheet app style (tinted frame, white sheet, IBM Plex Sans; IBM Plex Mono only
for codes, keys and session references). CSS and JS are plain strings; page data is
passed to the script as JSON, never interpolated into JavaScript.
"""

from __future__ import annotations

import json
from html import escape

FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600'
    '&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">'
)

# Nexus mark: two nodes joined by a link, drawn in the accent.
MARK_SVG = (
    '<svg class="mark" viewBox="0 0 20 20" width="20" height="20" aria-hidden="true">'
    '<rect width="20" height="20" rx="5" fill="var(--accent)"/>'
    '<circle cx="6.5" cy="13.5" r="2.4" fill="#fff"/><circle cx="13.5" cy="6.5" r="2.4" fill="#fff"/>'
    '<path d="M8.2 11.8 11.8 8.2" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/></svg>'
)

CSS = """
:root {
  --ground: #f9fbfc; --surface: #ffffff; --raised: #f4f6f8; --active: #ebedee;
  --border: #e8eaec; --rule: #f0f1f2; --control: #cdced1;
  --text: #131416; --muted: #5d5f60; --faint: #6b6b6b;
  --primary: #1d1f20; --accent: #1b6c80;
  --positive: #1e7b4f; --negative: #c4242a;
  --pill-positive-bg: #dcfae5; --pill-positive-ink: #04250e;
  --pill-negative-bg: #ffedea; --pill-negative-ink: #831c1e;
  --pill-warning-bg: #fffbeb; --pill-warning-ink: #92400e;
  --pill-neutral-bg: #ebedee; --pill-neutral-ink: #353736;
  --radius-card: 8px; --radius-control: 6px; --radius-sheet: 16px; --sheet-inset: 8px;
  --gap: 24px; --pad: 24px; --control-height: 32px; --field-height: 38px;
  --font: "IBM Plex Sans", Inter, system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body {
  background: var(--ground); color: var(--text); font: 400 14px/20px var(--font);
  -webkit-font-smoothing: antialiased; min-height: 100vh; display: flex; flex-direction: column;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* Frame: the 48px top bar sits on the tinted ground; the sheet is inset 8px. */
.topbar { height: 48px; display: flex; align-items: center; gap: 10px; padding: 0 20px; flex: none; }
.brand { display: flex; align-items: center; gap: 8px; color: var(--text); font-weight: 600; }
.brand:hover { text-decoration: none; }
.crumb { color: var(--muted); }
.crumb::before { content: "/"; margin: 0 8px 0 2px; color: var(--control); }
.topbar .who { margin-left: auto; color: var(--muted); }
.sheet {
  flex: 1; margin: 0 var(--sheet-inset) var(--sheet-inset); background: var(--surface);
  border: 1px solid var(--border); border-radius: var(--radius-sheet); padding: 32px var(--pad) 40px;
}
.canvas { max-width: 1152px; margin: 0 auto; }

h1 { font-size: 22px; line-height: 28px; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
.subline { color: var(--muted); margin: 6px 0 0; font-size: 14px; }
.title-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 32px; }
h2 { font-size: 16px; line-height: 24px; font-weight: 500; margin: 0; }
p { margin: 0 0 12px; }
small, .note { color: var(--muted); font-size: 12px; line-height: 18px; }
code, .mono { font-family: var(--mono); font-size: 13px; }
code { background: var(--raised); padding: 1px 5px; border-radius: 4px; }

/* Settings rows: a label column beside its panel, split by full-width rules. */
.setting { display: grid; grid-template-columns: minmax(220px, 340px) minmax(0, 1fr); gap: var(--gap); padding: 32px 0; border-top: 1px solid var(--border); }
.setting:first-of-type { border-top: 0; padding-top: 0; }
.setting > .label h2 { margin-top: 6px; }
.setting > .label p { color: var(--muted); margin-top: 6px; }
.panel { background: var(--raised); border-radius: var(--radius-card); padding: 16px 20px; }
.panel.plain { background: transparent; padding: 0; }

.kv { display: grid; grid-template-columns: 160px minmax(0, 1fr); row-gap: 0; }
.kv > div { padding: 10px 0; border-top: 1px solid var(--rule); min-height: 40px; display: flex; align-items: center; gap: 8px; min-width: 0; }
.kv > div:nth-child(-n+2) { border-top: 0; }
.kv .k { color: var(--muted); }

/* Account rows */
.accounts { list-style: none; margin: 0; padding: 0; }
.account { padding: 14px 0; border-top: 1px solid var(--border); }
.account:first-child { border-top: 0; padding-top: 2px; }
.account:last-child { padding-bottom: 2px; }
.account-head { display: grid; grid-template-columns: minmax(0, 1fr) auto auto auto; align-items: center; gap: 16px; }
.account-name { font-weight: 500; }
.account-sub { color: var(--muted); font-size: 12px; line-height: 18px; }
.account-steps { margin-top: 14px; padding: 16px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-card); display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; }
.account-steps > div, .panel, .accounts, .account, .setting > * { min-width: 0; }
.account-steps[hidden] { display: none; }
.step-label { font-weight: 500; margin-bottom: 6px; }
.status-line { color: var(--muted); font-size: 12px; line-height: 18px; min-height: 18px; }
.status-line.bad { color: var(--negative); }

.pill { display: inline-flex; align-items: center; height: 22px; padding: 0 8px; border-radius: 999px; font-size: 12px; font-weight: 500; white-space: nowrap; flex: none; }
.pill.ok { background: var(--pill-positive-bg); color: var(--pill-positive-ink); }
.pill.wait { background: var(--pill-warning-bg); color: var(--pill-warning-ink); }
.pill.bad { background: var(--pill-negative-bg); color: var(--pill-negative-ink); }
.pill.off { background: var(--pill-neutral-bg); color: var(--pill-neutral-ink); }

/* Controls: one height per context; joined input + action share an edge. */
label.field { display: block; font-weight: 500; margin: 0 0 8px; }
input[type=text], input[type=password] {
  width: 100%; height: var(--field-height); padding: 0 12px; font: 400 14px var(--font); color: var(--text);
  background: var(--surface); border: 1px solid var(--control); border-radius: var(--radius-control);
}
input.mono { font-family: var(--mono); font-size: 13px; }
input::placeholder { color: var(--faint); }
select.select { width: 100%; height: var(--field-height); padding: 0 10px; font: 400 14px var(--font); color: var(--text); background: var(--surface); border: 1px solid var(--control); border-radius: var(--radius-control); }
input:disabled { background: var(--raised); color: var(--muted); }
.joined { display: flex; min-width: 0; width: 100%; }
.joined > input, .joined > .value { border-top-right-radius: 0; border-bottom-right-radius: 0; flex: 1; min-width: 0; }
.joined > .btn { border-radius: 0; margin-left: -1px; height: var(--field-height); }
.joined > .btn:last-child { border-top-right-radius: var(--radius-control); border-bottom-right-radius: var(--radius-control); }
.value {
  height: var(--field-height); display: flex; align-items: center; padding: 0 12px; background: var(--surface);
  border: 1px solid var(--control); border-radius: var(--radius-control); font-family: var(--mono); font-size: 13px;
  overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
}
.value.code { font-size: 18px; letter-spacing: 0.06em; }
.value a { display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: var(--control-height);
  padding: 0 12px; font: 500 14px var(--font); color: var(--text); background: var(--surface);
  border: 1px solid var(--control); border-radius: var(--radius-control); cursor: pointer; white-space: nowrap;
  text-decoration: none; transition: background-color .12s ease, transform .06s ease;
}
.btn:hover { background: var(--raised); text-decoration: none; }
.btn:active { transform: translateY(1px); }
.btn:disabled { color: var(--muted); cursor: default; }
.btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.btn.primary:hover { background: #33363a; }
.btn.done { color: var(--positive); }
.row-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.switch { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); cursor: pointer; white-space: nowrap; }
.switch input { appearance: none; width: 28px; height: 16px; border-radius: 999px; background: var(--control); position: relative; margin: 0; cursor: pointer; transition: background-color .12s ease; }
.switch input::after { content: ""; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px; border-radius: 50%; background: #fff; transition: transform .12s ease; }
.switch input:checked { background: var(--primary); }
.switch input:checked::after { transform: translateX(12px); }

.fields { display: grid; gap: 16px; }
.form-foot { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-top: 32px; border-top: 1px solid var(--border); }
.form-foot .note { max-width: 640px; }
.message-body > *:first-child { margin-top: 0; }
.message-body > * + * { margin-top: 12px; }
.message-body > p:last-child { margin-bottom: 0; }
.plain-list { margin: 0; padding-left: 18px; }
.plain-list li + li { margin-top: 4px; }
.message-body form { display: grid; gap: 12px; max-width: 560px; }
.message-body button, .message-body .form-submit { justify-self: start; }
.message-body button:not(.btn) {
  height: var(--control-height); padding: 0 14px; font: 500 14px var(--font); color: #fff; background: var(--primary);
  border: 1px solid var(--primary); border-radius: var(--radius-control); cursor: pointer;
}
.message-body select { height: var(--field-height); padding: 0 10px; border: 1px solid var(--control); border-radius: var(--radius-control); font: 400 14px var(--font); background: var(--surface); }
.message-body label { font-weight: 500; }

@media (max-width: 760px) {
  .topbar { padding: 0 16px; }
  .topbar .who { display: none; }
  .sheet { margin: 0; border-radius: 0; border-width: 1px 0 0; padding: 24px 16px 32px; }
  .setting { grid-template-columns: 1fr; gap: 12px; padding: 24px 0; }
  .title-row { flex-direction: column; align-items: flex-start; margin-bottom: 24px; }
  .kv { grid-template-columns: 1fr; }
  .kv > div:nth-child(2) { border-top: 0; }
  .kv > div.k { border-top: 1px solid var(--rule); padding-bottom: 0; min-height: 0; }
  .kv > div.k:first-child { border-top: 0; }
  .account-head { grid-template-columns: minmax(0, 1fr) auto; row-gap: 10px; }
  .account-head .switch { grid-column: 1; }
  .btn, .joined > .btn { min-height: 44px; }
  .form-foot { flex-direction: column; align-items: stretch; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""


def render_page(
    title: str,
    body_html: str,
    *,
    subline: str = "",
    crumb: str = "Account",
    account: str = "",
    actions_html: str = "",
    wrap_body: bool = False,
) -> str:
    """The shared frame: tinted top bar, white sheet, one h1 title row."""
    who = f'<span class="who">{escape(account)}</span>' if account else ""
    sub = f'<p class="subline">{subline}</p>' if subline else ""
    body = f'<div class="message-body">{body_html}</div>' if wrap_body else body_html
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)} · Nexus</title>
    {FONT_LINKS}
    <style>{CSS}</style>
  </head>
  <body>
    <header class="topbar">
      <a class="brand" href="/" aria-label="Nexus">{MARK_SVG}<span>Nexus</span></a>
      <span class="crumb">{escape(crumb)}</span>
      {who}
    </header>
    <main class="sheet">
      <div class="canvas">
        <div class="title-row"><div><h1>{escape(title)}</h1>{sub}</div>{actions_html}</div>
        {body}
      </div>
    </main>
  </body>
</html>"""


# Provider rows of the setup page, in the order people connect them.
PROVIDERS = (
    {
        "key": "claude",
        "name": "Claude",
        "sub": "Claude Code subscription · signs in at claude.com",
        "code_hint": "After you approve, claude.com shows a code. Paste it here.",
    },
    {
        "key": "codex",
        "name": "Codex",
        "sub": "ChatGPT subscription for the Codex CLI · device code",
        "code_hint": "",
    },
    {
        "key": "agy",
        "name": "Antigravity",
        "sub": "Google Antigravity CLI · your Google account",
        "code_hint": "After Google sign-in, antigravity.google shows a code. Paste it here.",
    },
)
CODE_PROVIDERS = {"claude", "agy"}


def _api_key_field(name: str, label: str, placeholder: str, saved: bool) -> str:
    if not saved:
        return f"""
    <div>
      <label class="field" for="{name}">{escape(label)}</label>
      <div class="joined">
        <input id="{name}" name="{name}" type="password" class="mono" placeholder="{escape(placeholder)}" autocomplete="new-password" spellcheck="false" autocapitalize="off" autocorrect="off" />
        <button type="button" class="btn" data-paste="{name}">Paste</button>
      </div>
    </div>"""
    return f"""
    <div>
      <label class="field" for="{name}">{escape(label)}</label>
      <div id="{name}_saved" class="joined">
        <div class="value">Saved</div>
        <button type="button" class="btn" onclick="enableProviderField('{name}')">Replace</button>
      </div>
      <div id="{name}_editor" hidden>
        <div class="joined">
          <input id="{name}" name="{name}" type="password" class="mono" placeholder="Paste a new key, or leave empty to clear" autocomplete="new-password" spellcheck="false" autocapitalize="off" autocorrect="off" disabled />
          <button type="button" class="btn" data-paste="{name}">Paste</button>
        </div>
        <small>Leave it empty and save to clear the saved value.</small>
      </div>
    </div>"""


def _account_row(provider: dict, enabled: bool) -> str:
    key = provider["key"]
    pill = '<span class="pill ok" data-pill>Connected</span>' if enabled else '<span class="pill off" data-pill>Not connected</span>'
    checked = " checked" if enabled else ""
    code_block = ""
    if key in CODE_PROVIDERS:
        code_block = f"""
        <div data-code-step hidden>
          <label class="field" for="{key}_authorization_code">Code</label>
          <div class="joined">
            <input id="{key}_authorization_code" type="text" class="mono" placeholder="Paste the code" spellcheck="false" autocapitalize="off" autocorrect="off" autocomplete="off" />
            <button type="button" class="btn" data-paste="{key}_authorization_code">Paste</button>
            <button type="button" class="btn" onclick="submitProviderAuthCode('{key}')">Submit</button>
          </div>
          <small>{escape(provider["code_hint"])}</small>
        </div>"""
    return f"""
    <li class="account" id="account-{key}" data-provider="{key}">
      <div class="account-head">
        <div><div class="account-name">{escape(provider["name"])}</div><div class="account-sub">{escape(provider["sub"])}</div></div>
        {pill}
        <label class="switch" title="Use this account for agent runs"><input type="checkbox" name="use_{key}_account" value="1"{checked} /><span>Use</span></label>
        <button type="button" class="btn" data-connect onclick="startProviderConnect('{key}')">{"Reconnect" if enabled else "Connect"}</button>
      </div>
      <div class="account-steps" data-steps hidden>
        <div data-link-step hidden>
          <div class="step-label">Sign in</div>
          <div class="joined">
            <div class="value"><a data-link href="#" target="_blank" rel="noopener"></a></div>
            <button type="button" class="btn" data-copy-link>Copy</button>
            <a class="btn" data-open href="#" target="_blank" rel="noopener">Open</a>
          </div>
        </div>
        <div data-device-step hidden>
          <div class="step-label">Enter this code on the sign-in page</div>
          <div class="joined">
            <div class="value code" data-device-code></div>
            <button type="button" class="btn" data-copy-device>Copy</button>
          </div>
        </div>{code_block}
        <div class="status-line" data-status role="status" aria-live="polite"></div>
      </div>
    </li>"""


def render_setup_form(
    *,
    session_id: str,
    show_api_key_inputs: bool,
    copilot_checked: bool,
    show_copilot_option: bool,
    copilot_token_set: bool,
    codex_key_set: bool,
    claude_key_set: bool,
    account_flags: dict[str, bool],
    copilot_account_enabled: bool,
    existing_keys_note: str,
    identity_rows: list[tuple[str, str]] | None = None,
    session_ref: str = "",
    opencode_key_set: bool = False,
) -> str:
    """The account setup screen body: identity, AI accounts, Copilot, OpenCode, API keys, save."""
    identity = ""
    if identity_rows or session_ref:
        cells = "".join(
            f'<div class="k">{escape(k)}</div><div>{v}</div>' for k, v in (identity_rows or [])
        )
        if session_ref:
            cells += (
                '<div class="k">Session reference</div><div><div class="joined">'
                f'<div class="value" id="session_ref_value">{escape(session_ref)}</div>'
                '<button type="button" class="btn" data-copy="session_ref_value">Copy</button></div></div>'
            )
        identity = f"""
  <section class="setting" aria-labelledby="identity-h">
    <div class="label"><h2 id="identity-h">Nexus identity</h2><p>Your Nexus account and project access come from the Git account you signed in with.</p></div>
    <div class="panel"><div class="kv">{cells}</div></div>
  </section>"""

    rows = "".join(_account_row(p, bool(account_flags.get(p["key"]))) for p in PROVIDERS)
    accounts = f"""
  <section class="setting" aria-labelledby="accounts-h">
    <div class="label"><h2 id="accounts-h">AI accounts</h2><p>Agents run on your own subscriptions. Connect an account once; Studio and the bots reuse it. Switch off <em>Use</em> to stop using an account without disconnecting it.</p><p>Claude and Antigravity finish on their own site with a code: copy it there, then paste it into that account's row here. Codex shows a code to enter on its sign-in page instead.</p></div>
    <div class="panel"><ul class="accounts">{rows}</ul></div>
  </section>"""

    copilot = ""
    if show_copilot_option:
        copilot = f"""
  <section class="setting" aria-labelledby="copilot-h">
    <div class="label"><h2 id="copilot-h">Copilot</h2><p>Copilot runs on the GitHub account you signed in with; no separate key.</p></div>
    <div class="panel fields">
      <label class="switch"><input type="checkbox" name="use_copilot" value="1"{" checked" if copilot_checked else ""} /><span>Use Copilot with my linked GitHub account</span></label>
      <label class="switch"><input type="checkbox" name="use_copilot_account" value="1"{" checked" if copilot_account_enabled else ""} /><span>Use Copilot account mode</span></label>
    </div>
  </section>"""

    # OpenCode has no account sign-in here: its API key is the login, in every auth mode.
    opencode = f"""
  <section class="setting" aria-labelledby="opencode-h">
    <div class="label"><h2 id="opencode-h">OpenCode</h2><p>OpenCode runs on your own OpenCode API key from opencode.ai. Its free models work only inside the OpenCode app.</p></div>
    <div class="panel fields">
      {_api_key_field("opencode_api_key", "OpenCode API key", "sk-…", opencode_key_set)}
    </div>
  </section>"""

    keys = ""
    if show_api_key_inputs:
        keys = f"""
  <section class="setting" aria-labelledby="keys-h">
    <div class="label"><h2 id="keys-h">API keys</h2><p>Optional, for API-only features. Keys are encrypted at rest and used only for your own runs.</p></div>
    <div class="panel fields">
      {_api_key_field("claude_api_key", "Anthropic API key", "sk-ant-…", claude_key_set)}
      {_api_key_field("codex_api_key", "OpenAI API key", "sk-…", codex_key_set)}
      {_api_key_field("copilot_github_token", "Copilot token", "ghp_…", copilot_token_set)}
    </div>
  </section>"""

    data = json.dumps({"session": session_id}).replace("</", "<\\/")
    return f"""
<form method="post" action="/auth/ai-keys" autocomplete="off">
  <input type="hidden" name="session_id" value="{escape(session_id, quote=True)}" />
  {accounts}
  {identity}
  {copilot}
  {opencode}
  {keys}
  <div class="form-foot">
    <p class="note">{existing_keys_note}</p>
    <button type="submit" class="btn primary form-submit">Save setup</button>
  </div>
</form>
<script type="application/json" id="setup-data">{data}</script>
<script>{SETUP_JS}</script>"""


SETUP_JS = r"""
(function () {
  var dataEl = document.getElementById("setup-data");
  var DATA = dataEl ? JSON.parse(dataEl.textContent || "{}") : {};
  var TERMINAL = ["connected", "failed", "connected_but_not_saved", "rate_limited",
    "interactive_required", "unsupported_provider", "invalid_session", "no_active_job", "idle"];
  var NAMES = { claude: "Claude", codex: "Codex", agy: "Antigravity" };
  var polls = {};

  function row(p) { return document.getElementById("account-" + p); }
  function part(p, sel) { var r = row(p); return r ? r.querySelector(sel) : null; }
  function safeUrl(u) { return /^https?:\/\//i.test(String(u || "")) ? String(u) : ""; }
  function flash(btn, text) {
    if (!btn) return;
    var old = btn.getAttribute("data-label") || btn.textContent;
    btn.setAttribute("data-label", old);
    btn.textContent = text; btn.classList.add("done");
    setTimeout(function () { btn.textContent = old; btn.classList.remove("done"); }, 1400);
  }
  function copyText(btn, text) {
    if (!text) return;
    var done = function () { flash(btn, "Copied"); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { legacyCopy(text); done(); });
    } else { legacyCopy(text); done(); }
  }
  function legacyCopy(text) {
    var t = document.createElement("textarea");
    t.value = text; t.setAttribute("readonly", ""); t.style.position = "fixed"; t.style.opacity = "0";
    document.body.appendChild(t); t.select();
    try { document.execCommand("copy"); } catch (e) { /* ignore */ }
    document.body.removeChild(t);
  }
  function pasteInto(btn, id) {
    var input = document.getElementById(id);
    if (!input) return;
    if (input.disabled) input.disabled = false;
    if (!(navigator.clipboard && navigator.clipboard.readText)) { input.focus(); flash(btn, "Use Ctrl+V"); return; }
    navigator.clipboard.readText().then(function (t) {
      if (t) { input.value = String(t).trim(); input.dispatchEvent(new Event("input", { bubbles: true })); flash(btn, "Pasted"); }
      else { input.focus(); }
    }, function () { input.focus(); flash(btn, "Use Ctrl+V"); });
  }

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest ? ev.target.closest("button") : null;
    if (!b) return;
    if (b.hasAttribute("data-paste")) { pasteInto(b, b.getAttribute("data-paste")); }
    else if (b.hasAttribute("data-copy")) {
      var src = document.getElementById(b.getAttribute("data-copy"));
      copyText(b, src ? (src.value || src.textContent || "").trim() : "");
    } else if (b.hasAttribute("data-copy-link")) {
      var a = b.closest(".account").querySelector("[data-link]");
      copyText(b, a ? a.getAttribute("href") : "");
    } else if (b.hasAttribute("data-copy-device")) {
      var c = b.closest(".account").querySelector("[data-device-code]");
      copyText(b, c ? c.textContent.trim() : "");
    }
  });

  function setPill(p, kind, text) {
    var pill = part(p, "[data-pill]");
    if (!pill) return;
    pill.className = "pill " + kind; pill.setAttribute("data-pill", ""); pill.textContent = text;
  }
  function setStatus(p, text, bad) {
    var s = part(p, "[data-status]");
    if (!s) return;
    s.textContent = text || ""; s.classList.toggle("bad", !!bad);
  }
  function show(p, sel, on) { var el = part(p, sel); if (el) el.hidden = !on; }

  function render(p, payload) {
    var state = String(payload.state || "");
    var url = safeUrl(payload.verify_url);
    if (url) {
      var a = part(p, "[data-link]"), o = part(p, "[data-open]");
      if (a) { a.href = url; a.textContent = url; }
      if (o) o.href = url;
    }
    show(p, "[data-link-step]", !!url && state !== "connected");
    var code = String(payload.user_code || "");
    var dc = part(p, "[data-device-code]");
    if (dc) dc.textContent = code;
    show(p, "[data-device-step]", !!code && state !== "connected");
    show(p, "[data-code-step]", !!payload.requires_code && state !== "connected");

    if (state === "connected") {
      setPill(p, "ok", "Connected");
      var t = part(p, 'input[type="checkbox"]'); if (t) t.checked = true;
      var btn = part(p, "[data-connect]"); if (btn) btn.textContent = "Reconnect";
      setStatus(p, NAMES[p] + " account connected and saved.", false);
    } else if (TERMINAL.indexOf(state) >= 0) {
      setPill(p, "bad", "Not connected");
      setStatus(p, payload.message || "Sign-in did not finish. Try Connect again.", true);
    } else {
      setPill(p, "wait", payload.requires_code ? "Waiting for code" : "Waiting for sign-in");
      setStatus(p, payload.message || "", false);
    }
  }

  function poll(p) {
    clearTimeout(polls[p]);
    fetch("/auth/provider-connect/status?provider=" + encodeURIComponent(p) + "&session_id=" + encodeURIComponent(DATA.session))
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        render(p, payload);
        if (TERMINAL.indexOf(String(payload.state || "")) < 0) polls[p] = setTimeout(function () { poll(p); }, 1500);
      })
      .catch(function () { setStatus(p, "Lost contact with the server. Retrying…", true); polls[p] = setTimeout(function () { poll(p); }, 4000); });
  }

  window.startProviderConnect = function (p) {
    show(p, "[data-steps]", true);
    setPill(p, "wait", "Starting");
    setStatus(p, "Preparing the sign-in link…", false);
    fetch("/auth/provider-connect/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: p, session_id: DATA.session })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) { setPill(p, "bad", "Not connected"); setStatus(p, res.body.message || "Could not start the sign-in.", true); return; }
        render(p, res.body);
        polls[p] = setTimeout(function () { poll(p); }, 800);
      })
      .catch(function () { setPill(p, "bad", "Not connected"); setStatus(p, "Could not start the sign-in.", true); });
  };

  window.submitProviderAuthCode = function (p) {
    var input = document.getElementById(p + "_authorization_code");
    var code = input ? String(input.value || "").trim() : "";
    if (!code) { setStatus(p, "Paste the code first.", true); if (input) input.focus(); return; }
    setStatus(p, "Sending the code to " + NAMES[p] + "…", false);
    fetch("/auth/provider-connect/submit-code", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: p, session_id: DATA.session, authorization_code: code })
    }).then(function (r) { return r.json(); })
      .then(function (payload) { if (input) input.value = ""; render(p, payload); poll(p); })
      .catch(function () { setStatus(p, "Could not send the code. Try again.", true); });
  };

  // A sign-in started from Telegram or Discord shows up here: attach to running jobs.
  Object.keys(NAMES).forEach(function (p) {
    if (!row(p) || !DATA.session) return;
    fetch("/auth/provider-connect/status?provider=" + encodeURIComponent(p) + "&session_id=" + encodeURIComponent(DATA.session))
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        if (!payload.exists || TERMINAL.indexOf(String(payload.state || "")) >= 0) return;
        show(p, "[data-steps]", true);
        render(p, payload);
        polls[p] = setTimeout(function () { poll(p); }, 1500);
      })
      .catch(function () { /* the row keeps its saved state */ });
  });

  window.enableProviderField = function (name) {
    var saved = document.getElementById(name + "_saved"), editor = document.getElementById(name + "_editor");
    if (saved) saved.hidden = true;
    if (editor) { editor.hidden = false; var i = document.getElementById(name); if (i) { i.disabled = false; i.focus(); } }
  };
})();
"""


def render_field_form(
    *,
    action: str,
    method: str = "post",
    hidden: dict[str, str] | None = None,
    title: str,
    description: str,
    field_name: str,
    field_label: str,
    field_type: str = "text",
    placeholder: str = "",
    field_value: str = "",
    submit_label: str,
    message_html: str = "",
    extra_fields_html: str = "",
    aside_title: str = "",
    aside_html: str = "",
) -> str:
    """One task per page: a labelled field with Paste and the primary action, plus help beside it."""
    hidden_inputs = "".join(
        f'<input type="hidden" name="{escape(k, quote=True)}" value="{escape(v, quote=True)}" />'
        for k, v in (hidden or {}).items()
    )
    aside = ""
    if aside_title:
        aside = f"""
  <section class="setting" aria-labelledby="aside-h">
    <div class="label"><h2 id="aside-h">{escape(aside_title)}</h2></div>
    <div class="panel">{aside_html}</div>
  </section>"""
    return f"""
<form method="{method}" action="{escape(action, quote=True)}" autocomplete="off">
  {hidden_inputs}
  <section class="setting" aria-labelledby="field-h">
    <div class="label"><h2 id="field-h">{escape(title)}</h2><p>{description}</p></div>
    <div class="panel fields">
      {message_html}
      <div>
        <label class="field" for="{field_name}">{escape(field_label)}</label>
        <div class="joined">
          <input id="{field_name}" name="{field_name}" type="{field_type}" class="mono" placeholder="{escape(placeholder, quote=True)}" value="{escape(field_value, quote=True)}" spellcheck="false" autocapitalize="off" autocorrect="off" required />
          <button type="button" class="btn" data-paste="{field_name}">Paste</button>
        </div>
      </div>
      {extra_fields_html}
      <div><button type="submit" class="btn primary">{escape(submit_label)}</button></div>
    </div>
  </section>{aside}
</form>
<script>{SETUP_JS}</script>"""


def render_setup_saved(
    *,
    ready: bool,
    missing: list[str],
    accounts: list[tuple[str, bool]],
    projects: list[str],
    setup_url: str,
) -> str:
    """The page after Save setup: is it ready, and if not, what is still missing."""
    status_pill = (
        '<span class="pill ok">Ready</span>' if ready else '<span class="pill wait">Not ready yet</span>'
    )
    missing_html = ""
    if missing:
        items = "".join(f"<li>{escape(item)}</li>" for item in missing)
        missing_html = f'<div class="k">Still needed</div><div><ul class="plain-list">{items}</ul></div>'
    account_cells = "".join(
        f'<div class="k">{escape(name)}</div><div>'
        + ('<span class="pill ok">Connected</span>' if connected else '<span class="pill off">Not connected</span>')
        + "</div>"
        for name, connected in accounts
    )
    projects_html = escape(", ".join(projects)) if projects else '<span class="pill bad">None yet</span>'
    return f"""
  <section class="setting" aria-labelledby="status-h">
    <div class="label"><h2 id="status-h">Setup status</h2><p>Agents can run for you once Git is linked, an AI account is connected and you have access to a project.</p></div>
    <div class="panel"><div class="kv">
      <div class="k">Status</div><div>{status_pill}</div>
      {missing_html}
      <div class="k">Project access</div><div>{projects_html}</div>
    </div></div>
  </section>
  <section class="setting" aria-labelledby="saved-accounts-h">
    <div class="label"><h2 id="saved-accounts-h">AI accounts</h2><p>Change them any time on the account page.</p></div>
    <div class="panel"><div class="kv">{account_cells}</div></div>
  </section>
  <div class="form-foot">
    <p class="note">In Telegram send <code>/setup_status</code>, or <code>/setup-status</code> in Discord, to see this in chat.</p>
    <a class="btn primary" href="{escape(setup_url, quote=True)}">Back to account setup</a>
  </div>"""


def render_message(title: str, body_html: str, *, status_code: int = 200, subline: str = "") -> str:
    """Message pages: what happened, then the ways forward from here."""
    failed = status_code >= 400
    next_html = (
        "<p>Send <code>/login</code> to the Nexus bot in Telegram or Discord to start again, "
        "or open your account page if your sign-in is still active.</p>"
        '<div class="row-actions"><a class="btn primary" href="/auth/setup">Account page</a>'
        '<a class="btn" href="/">Sign-in page</a></div>'
        if failed
        else "<p>You can close this window, or go to your account page.</p>"
        '<div class="row-actions"><a class="btn primary" href="/auth/setup">Account page</a></div>'
    )
    body = f"""
  <section class="setting" aria-labelledby="what-h">
    <div class="label"><h2 id="what-h">{"What happened" if failed else "Done"}</h2></div>
    <div class="panel message-body">{body_html}</div>
  </section>
  <section class="setting" aria-labelledby="next-h">
    <div class="label"><h2 id="next-h">Next</h2></div>
    <div class="panel fields">{next_html}</div>
  </section>"""
    return render_page(title, body, subline=subline)
