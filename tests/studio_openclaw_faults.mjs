import assert from "node:assert/strict";
import {callStudio} from "../packages/nexus-arc/dist/studio.js";

process.env.OPENCLAW_FAULT_SECRET = "synthetic-failure-secret-" + "s".repeat(32);
const root = (mode, url = process.argv[2]) => ({plugins: {entries: {"nexus-arc": {config: {
    studioUrl: url, studioEntrypoint: mode, studioSecretEnv: "OPENCLAW_FAULT_SECRET", timeoutMs: 1000
}}}}});
const caller = {sender: "telegram:alice", channel: "telegram:room", session: "session"};
assert.equal((await callStudio(root("healthy"), caller, "discover", {}, "read")).version, 1);
for (const mode of ["denied", "error", "html", "malformed", "redirect", "oversized", "hang"]) {
    await assert.rejects(callStudio(root(mode), caller, "submit", {target: "agents:reviewer", task: "Synthetic"}, mode), error => {
        assert.match(String(error), /uncertain/);
        assert.match(String(error), new RegExp(`receipt ${mode}`));
        assert.doesNotMatch(String(error), /PRIVATE_FAULT_DETAIL/);
        return true;
    });
}
await assert.rejects(callStudio(root("healthy", "http://127.0.0.1:1"), caller, "submit", {}, "unreachable"), /receipt unreachable/);
console.log(JSON.stringify({result: "passed", httpCases: 8, unreachable: "passed"}));
