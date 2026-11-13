"use strict";
// Drafts stay on this browser and never submit work. No credential fields or file bytes are collected.
(function (root) {
  const TTL = 7 * 24 * 60 * 60 * 1000;
  const limits = {chat:100000, builder:16000};
  const settingNames = ["agent","project","provider","model","effort","tools"];
  class DraftStore {
    constructor(storage, scope, now = () => Date.now(), uuid = () => root.crypto.randomUUID()) {
      this.storage = storage; this.now = now; this.uuid = uuid;
      this.prefix = "nexus-draft:v1:" + encodeURIComponent(JSON.stringify(scope)) + ":";
    }
    key(surface) { if (!Object.hasOwn(limits, surface)) throw Error("Unknown draft surface"); return this.prefix + surface; }
    clean(surface, value) {
      if (!value || typeof value.text !== "string" || value.text.length > limits[surface]) throw Error("Draft is too large to save on this browser.");
      const artifacts = value.artifacts;
      if (!Array.isArray(artifacts) || artifacts.length > 5 || artifacts.some(id => typeof id !== "string" || !/^[\w.-]{1,200}$/.test(id))) throw Error("Invalid draft attachments");
      const settings = {};
      for (const name of settingNames) {
        const item = value.settings?.[name];
        if (item !== undefined) {
          if (typeof item !== "string" || item.length > 200) throw Error("Invalid draft setting");
          settings[name] = item;
        }
      }
      const context = value.context ?? null;
      if (context !== null && (typeof context !== "string" || !/^[\w.-]{1,200}$/.test(context))) throw Error("Invalid draft context");
      return {text:value.text, artifacts:[...new Set(artifacts)], settings, context, uncertain:value.uncertain === true};
    }
    read(surface) {
      try {
        const raw = this.storage.getItem(this.key(surface));
        if (!raw) return {ok:true, record:null};
        let value;
        try {
          if (raw.length > 1000000) throw Error("Oversized draft");
          value = JSON.parse(raw);
          if (value.version !== 1 || typeof value.id !== "string" || !/^[\w-]{1,100}$/.test(value.id) || !Number.isFinite(value.savedAt) || value.savedAt > this.now() + 60000 || this.now() - value.savedAt >= TTL) throw Error("Expired or invalid draft");
          value = {...this.clean(surface, value), version:1, id:value.id, savedAt:value.savedAt};
        } catch { this.storage.removeItem(this.key(surface)); return {ok:true, record:null}; }
        return {ok:true, record:value};
      } catch { return {ok:false, error:"Draft recovery is unavailable because browser storage is blocked."}; }
    }
    write(surface, value, expected) {
      try {
        const current = this.read(surface);
        if (!current.ok) return current;
        if ((current.record?.id ?? null) !== expected) return {ok:false, conflict:true, record:current.record};
        const data = this.clean(surface, value);
        if (!data.text.trim() && !data.artifacts.length) return this.remove(surface, expected);
        const record = {...data, version:1, id:this.uuid(), savedAt:this.now()};
        this.storage.setItem(this.key(surface), JSON.stringify(record));
        return {ok:true, record};
      } catch (error) { return {ok:false, error:error.message === "Draft is too large to save on this browser." ? error.message : "Draft could not be saved on this browser. Keep this page open or copy your text."}; }
    }
    remove(surface, expected) {
      try {
        const current = this.read(surface);
        if (!current.ok) return current;
        if ((current.record?.id ?? null) !== expected) return {ok:false, conflict:true, record:current.record};
        this.storage.removeItem(this.key(surface));
        return {ok:true, record:null};
      } catch { return {ok:false, error:"Saved draft could not be removed from browser storage."}; }
    }
  }
  root.NexusDraftStore = DraftStore;
})(window);

(function () {
  const channels = new Map();
  let store = null, scopeKey = "";
  const ids = {chat:["agent","project","provider","model","effort","tools"], builder:["provider","model","effort","tools"]};
  const currentScope = () => {
    if (!state.ready || !state.workspacePath || (state.mode === "team" && !state.user?.nexus_id)) return "";
    return JSON.stringify([state.mode, state.user?.nexus_id || "desktop", state.workspacePath,
      location.pathname === "/agent.html" ? new URLSearchParams(location.search).get("agent") : ""]);
  };
  function snapshot(name) {
    const settings = {};
    for (const field of ids[name]) settings[field] = $(`${name}-${field}`)?.value || "";
    return {text:$(name === "chat" ? "chat-task" : "builder-task").value,
      artifacts:Array.from($(`${name === "chat" ? "chat" : "builder"}-artifacts`).selectedOptions, option => option.value), settings,
      context:name === "chat" ? state.sessionId : builder.id};
  }
  const busy = name => name === "chat" ? state.busy : builder.busy;
  const fingerprint = data => JSON.stringify({text:data.text, artifacts:data.artifacts, settings:data.settings});
  function render(name, error = "") {
    const channel = channels.get(name), panel = $(`${name}-draft`);
    if (!channel || !panel) return;
    const blocked = !store || !state.ready || scopeKey !== currentScope();
    panel.hidden = blocked || !(error || channel.error || channel.pending || channel.owned);
    panel.dataset.recovery = channel.pending || error || channel.error ? "available" : "saved";
    const record = channel.pending || channel.owned;
    const occupied = !!$(name === "chat" ? "chat-task" : "builder-task").value;
    const message = error || channel.error || (channel.pending
      ? (record.uncertain ? "Submission was not confirmed. Check Activity before sending again. " : "Saved draft available on this browser. ") + (occupied ? "Clear the current prompt to restore it, or discard the saved draft." : "Restore it or discard it.")
      : "Draft saved on this browser." + (record?.uncertain ? " Earlier submission was not confirmed; check Activity before sending again." : ""));
    if ($(`${name}-draft-message`).textContent !== message) $(`${name}-draft-message`).textContent = message;
    $(`${name}-draft-restore`).hidden = !channel.pending;
    $(`${name}-draft-restore`).disabled = blocked || busy(name) || occupied || channel.restoring;
    $(`${name}-draft-discard`).hidden = !record;
    $(`${name}-draft-discard`).disabled = blocked || busy(name) || channel.restoring;
  }
  function accept(name, result) {
    const channel = channels.get(name);
    if (result.ok) { channel.owned = result.record; channel.pending = null; channel.error = ""; }
    else if (result.conflict) { channel.pending = result.record; channel.owned = null; channel.error = result.record ? "" : "Saved draft changed or expired. Current text has been kept."; }
    else channel.error = result.error;
    render(name);
    return result;
  }
  function save(name, uncertain = false) {
    const channel = channels.get(name);
    if (!store || scopeKey !== currentScope() || channel.pending || channel.restoring) return null;
    const data = snapshot(name);
    if (channel.ignore === fingerprint(data)) return null;
    const marked = uncertain || channel.owned?.uncertain === true;
    if (channel.owned && JSON.stringify(data) === JSON.stringify({text:channel.owned.text, artifacts:channel.owned.artifacts, settings:channel.owned.settings, context:channel.owned.context}) && channel.owned.uncertain === marked) return {ok:true, record:channel.owned};
    return accept(name, store.write(name, {...data, uncertain:marked}, channel.owned?.id ?? null));
  }
  function flush() {
    for (const [name, channel] of channels) { clearTimeout(channel.timer); if (!channel.submitting) save(name); }
  }
  function changed(name) {
    const channel = channels.get(name); if (!channel) return;
    channel.ignore = "";
    clearTimeout(channel.timer); render(name);
    if (!channel.submitting) channel.timer = setTimeout(() => save(name), 350);
  }
  function clearInput(name) {
    $(name === "chat" ? "chat-task" : "builder-task").value = "";
    for (const option of $(`${name}-artifacts`).options) option.selected = false;
    if (name === "chat" && typeof renderChatArtifacts === "function") renderChatArtifacts();
  }
  function lock() {
    for (const [name, channel] of channels) { clearTimeout(channel.timer); clearInput(name); $(`${name}-draft`).hidden = true; }
    channels.clear(); store = null; scopeKey = "";
  }
  function initialize() {
    const next = currentScope(); if (!next || next === scopeKey) return;
    if (store) lock();
    scopeKey = next;
    try { store = new window.NexusDraftStore(window.localStorage, JSON.parse(next)); }
    catch { store = new window.NexusDraftStore({getItem(){throw Error("Blocked");}}, JSON.parse(next)); }
    for (const name of (location.pathname === "/agent.html" ? ["chat"] : ["chat","builder"])) {
      const channel = {pending:null, owned:null, error:"", ignore:"", submitting:false, restoring:false};
      channels.set(name, channel);
      const saved = store.read(name);
      if (saved.ok) channel.pending = saved.record; else channel.error = saved.error;
      render(name);
    }
  }
  async function restore(name) {
    const channel = channels.get(name), record = channel?.pending;
    if (!record || !store || busy(name) || scopeKey !== currentScope() || $(name === "chat" ? "chat-task" : "builder-task").value) return;
    const origin = scopeKey, owner = store;
    channel.restoring = true; render(name);
    try {
      if (name === "chat") {
        if (record.context) await openSession(record.context); else newChat();
        await Promise.all([refreshModelChoices(), loadKnowledgeProjects()]);
      } else {
        if (record.context) await loadBuild(record.context); else resetBuilder();
        await refreshContextChoices();
      }
      if (scopeKey !== origin || currentScope() !== origin) return;
      if ($(name === "chat" ? "chat-task" : "builder-task").value) throw Error("Current prompt has changed. Clear it before restoring.");
      const latest = owner.read(name);
      if (!latest.ok) throw Error(latest.error);
      if (latest.record?.id !== record.id) { channel.pending = latest.record; throw Error("Saved draft changed in another tab. Review the latest draft."); }
      for (const field of ["provider", "agent", "project"]) {
        const input = $(`${name}-${field}`), value = record.settings[field];
        if (value && input?.tagName === "SELECT" && !Array.from(input.options).some(option => option.value === value)) throw Error(`The saved ${field} is unavailable. Restore its access before recovering this draft.`);
      }
      let missing = 0;
      for (const field of ids[name]) {
        const input = $(`${name}-${field}`), value = record.settings[field];
        if (value === undefined || !input) continue;
        if (input.tagName === "SELECT" && !Array.from(input.options).some(option => option.value === value)) { missing++; continue; }
        input.value = value;
      }
      const select = $(`${name}-artifacts`), available = new Set(Array.from(select.options, option => option.value));
      for (const option of select.options) option.selected = record.artifacts.includes(option.value);
      missing += record.artifacts.filter(id => !available.has(id)).length;
      $(name === "chat" ? "chat-task" : "builder-task").value = record.text;
      channel.pending = null; channel.owned = record; channel.error = "";
      if (name === "chat") { renderChatArtifacts(); if (typeof updateKnowledgeDisclosure === "function") updateKnowledgeDisclosure(); }
      if (typeof linkModelInputs === "function") linkModelInputs();
      $(name === "chat" ? "chat-task" : "builder-task").focus();
      if (record.uncertain) notify("Draft restored. Check Activity before sending it again; the earlier submission may have reached Studio.");
      if (missing) { channel.error = "Draft restored, but some files or settings are no longer available. Review the current selections before sending." + (record.uncertain ? " Check Activity before sending again; the earlier submission was not confirmed." : ""); notify(channel.error); }
    } catch (error) { if (scopeKey === origin) channel.error = `Draft was not restored: ${error.message} The saved draft has been kept.`; }
    finally { if (scopeKey === origin) { channel.restoring = false; render(name); } }
  }
  function discard(name) {
    const channel = channels.get(name), record = channel?.pending || channel?.owned;
    if (!record || !store || scopeKey !== currentScope() || busy(name) || channel.restoring) return;
    const pending = !!channel.pending;
    const result = store.remove(name, record.id);
    if (!result.ok) { accept(name, result); return; }
    channel.pending = channel.owned = null; channel.error = "";
    if (!pending) clearInput(name);
    else save(name);
    render(name);
  }
  window.NexusDrafts = {
    lock,
    settled(name) { if (channels.has(name)) render(name); },
    begin(name) {
      const channel = channels.get(name); if (!channel || !store || scopeKey !== currentScope()) return null;
      clearTimeout(channel.timer);
      const result = save(name, true);
      channel.submitting = true;
      return {name, owner:store, scope:scopeKey, record:result?.ok ? result.record : null, confirmed:false, snapshot:fingerprint(snapshot(name))};
    },
    confirmed(ticket) {
      if (!ticket) return;
      ticket.confirmed = true;
      const removed = ticket.record ? ticket.owner.remove(ticket.name, ticket.record.id) : {ok:true};
      const channel = channels.get(ticket.name);
      if (!channel || ticket.scope !== scopeKey) return;
      channel.submitting = false; channel.ignore = ticket.snapshot;
      if (channel.owned?.id === ticket.record?.id) channel.owned = null;
      if (!removed.ok && !removed.conflict) channel.error = "Submission was confirmed, but its browser draft could not be cleared. Check Activity before restoring any saved copy.";
      render(ticket.name);
    },
    failed(ticket) {
      if (!ticket || ticket.confirmed || ticket.scope !== scopeKey) return;
      const channel = channels.get(ticket.name); if (!channel) return;
      channel.submitting = false;
      if (ticket.record && channel.owned?.id === ticket.record.id) { channel.pending = ticket.record; channel.owned = null; }
      else if (!ticket.record && channel.owned && !channel.pending) { channel.pending = {...channel.owned, uncertain:true}; channel.owned = null; }
      render(ticket.name);
    }
  };
  for (const name of ["chat","builder"]) {
    $(name === "chat" ? "chat-task" : "builder-task").addEventListener("input", () => changed(name));
    $(`${name}-artifacts`).addEventListener("change", () => changed(name));
    for (const field of ids[name]) for (const event of ["input", "change"]) $(`${name}-${field}`)?.addEventListener(event, () => changed(name));
    $(`${name}-draft-restore`).addEventListener("click", () => restore(name));
    $(`${name}-draft-discard`).addEventListener("click", () => discard(name));
  }
  // Capture programmatic prompt insertions and attachment changes without reading file bodies.
  document.addEventListener("click", () => { for (const name of channels.keys()) { const channel = channels.get(name); clearTimeout(channel.timer); if (!channel.submitting) channel.timer = setTimeout(() => save(name), 350); } });
  document.addEventListener("change", event => { if (event.target.matches?.(".model-profile")) changed(event.target.closest(".builder-provider-fields") ? "builder" : "chat"); });
  window.addEventListener("pagehide", flush);
  document.addEventListener("visibilitychange", () => { if (document.hidden) flush(); });
  window.addEventListener("studio-ready", initialize);
  window.addEventListener("storage", event => {
    if (!store || scopeKey !== currentScope() || (event.storageArea && event.storageArea !== store.storage)) return;
    if (event.key !== null && !Array.from(channels.keys(), name => store.key(name)).includes(event.key)) return;
    for (const [name, channel] of channels) {
      const current = store.read(name);
      if (!current.ok) { channel.error = current.error; render(name); continue; }
      if ((current.record?.id ?? null) !== (channel.owned?.id ?? null)) { channel.pending = current.record; channel.owned = null; channel.ignore = fingerprint(snapshot(name)); render(name); }
    }
  });
  if (state.ready) initialize();
})();
