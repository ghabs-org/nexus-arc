const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

test("features.js defines structured speech preferences with all required controls", () => {
  const content = fs.readFileSync("nexus/studio_static/features.js", "utf8");
  assert.ok(content.includes('speech-preferences'), "Must define speech-preferences class");
  assert.ok(content.includes('speech-summary'), "Must define speech-summary class");
  assert.ok(content.includes('speech-body'), "Must define speech-body container");
  assert.ok(content.includes('id="device-speech-voice"'), "Must include device voice select");
  assert.ok(content.includes('id="device-speech-rate"'), "Must include speech rate select");
  assert.ok(content.includes('id="speech-autoplay"'), "Must include speech autoplay checkbox");
  assert.ok(content.includes('id="stop-speech"'), "Must include stop playback button");
});

test("studio.css defines cohesive rules for speech-preferences and generic details", () => {
  const css = fs.readFileSync("nexus/studio_static/studio.css", "utf8");
  assert.ok(css.includes(".speech-preferences"), "Must style speech-preferences");
  assert.ok(css.includes(".speech-summary"), "Must style speech-summary");
  assert.ok(css.includes(".speech-grid"), "Must style speech-grid");
  assert.ok(css.includes("details:not(.chat-attachments):not(.speech-preferences)"), "Must style generic details");
  assert.ok(css.includes("input[type=file]:not(.sr-only)"), "Must style file inputs");
  assert.ok(css.includes("input[type=checkbox]"), "Must style checkboxes");
});

test("features.js renders thumbs up and down rate buttons with Helpful and Needs improvement titles", () => {
  const content = fs.readFileSync("nexus/studio_static/features.js", "utf8");
  assert.match(content, /data-rate="1"[^>]*title="Helpful"/);
  assert.match(content, /data-rate="-1"[^>]*title="Needs improvement"/);
  assert.match(content, /class="secondary rate-btn"/);
});

test("features.js defines speech playback controls with pause and stop commands", () => {
  const content = fs.readFileSync("nexus/studio_static/features.js", "utf8");
  assert.ok(content.includes('speech-actions'), "Must define speech-actions container");
  assert.ok(content.includes('speech-playback-slot'), "Must define speech-playback-slot");
  assert.ok(content.includes('data-speech-cmd="toggle"'), "Must support toggle pause/resume command");
  assert.ok(content.includes('data-speech-cmd="stop"'), "Must support stop command");
  assert.ok(content.includes('function stopDeviceSpeech'), "Must define stopDeviceSpeech helper");
  assert.ok(content.includes('function resetSpeechControls'), "Must define resetSpeechControls helper");
});

test("studio.css defines gap and alignment for response-actions, rate-btn, and speech-actions", () => {
  const css = fs.readFileSync("nexus/studio_static/studio.css", "utf8");
  assert.match(css, /\.response-actions\{[^}]*gap:8px/);
  assert.match(css, /\.response-actions \.rate-btn/);
  assert.match(css, /\.speech-actions/);
  assert.match(css, /\.speech-playback-slot/);
});
