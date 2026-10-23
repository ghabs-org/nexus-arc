// Consume the shipped plugin entry point over real loopback HTTP.
import assert from "node:assert/strict";
import plugin from "../../../packages/nexus-arc/dist/index.js";

const config = {plugins: {entries: {"nexus-arc": {config: {
    studioUrl: process.argv[2], studioEntrypoint: "openclaw",
    studioSecretEnv: "NEXUS_OPENCLAW_EXAMPLE_SECRET"
}}}}};
const commands = new Map();
let factory;
plugin.register({config, registerCommand: c => commands.set(c.name, c),
    registerTool: value => {factory = value;}});
const tool = factory.create({requesterSenderId: "alice", nativeChannelId: "room",
    deliveryContext: {channel: "telegram"}, assertInvocationCurrent() {}});
let calls = 0;
const execute = async params => (await tool.execute(`read-${++calls}`, params)).details;
const command = async args => (await commands.get("nexus").handler({config,
    args: `studio ${args}`, senderId: "alice", channel: "telegram", channelId: "room",
    isAuthorizedSender: true})).text;
async function settled(id) {
    for (let attempt = 0; attempt < 100; attempt++) {
        const result = await execute({operation: "status", run_id: id});
        if (!["queued", "running"].includes(result.run.status)) return result.run;
        await new Promise(resolve => setTimeout(resolve, 25));
    }
    throw new Error("Studio run did not settle");
}

assert.match(await command("agents"), /agents:reviewer/);
const submission = {operation: "submit", target: "agents:reviewer", task: "Review evidence", request_id: "example-chat"};
const first = await execute(submission);
assert.equal((await execute(submission)).run.id, first.run.id);
const complete = await settled(first.run.id);
assert.equal(complete.status, "completed");
const second = await execute({...submission, task: "Continue the review", request_id: "example-followup"});
assert.equal((await settled(second.run.id)).session_id, complete.session_id);
const artifacts = await execute({operation: "artifacts", run_id: first.run.id});
const report = artifacts.items.find(item => item.name === "report.txt");
assert.ok(report);
const bytes = await execute({operation: "artifact", run_id: first.run.id,
    artifact_id: report.id, revision: report.revision});
assert.equal(Buffer.from(bytes.artifact.content_base64, "base64").toString(), "Example evidence");

for (const kind of ["approval", "input"]) {
    const started = await command(`run workflow ${kind} Review --request-id example-${kind}`);
    const id = started.match(/Run: ([a-f0-9]+)/)?.[1];
    assert.ok(id, started);
    const waiting = await settled(id);
    assert.equal(waiting.status, "waiting");
    assert.equal(waiting.reply_token, undefined, "Agent must not receive approval credentials");
    const humanView = await command(`status ${id}`);
    const token = humanView.match(/Reply token: ([a-f0-9.]+)/)?.[1];
    assert.ok(token, humanView);
    const answer = kind === "approval" ? "approve" : `input ${JSON.stringify({[waiting.awaiting_input[0].id]: "Evidence"})}`;
    const response = await command(`reply ${token} ${answer} --request-id example-reply-${kind}`);
    assert.match(response, new RegExp(id));
    assert.equal((await settled(id)).output, "2");
}
const receipt = await execute({operation: "receipt", request_id: "example-chat"});
assert.equal(receipt.run.id, first.run.id);
console.log(JSON.stringify({result: "passed", runs: 4, artifactBytes: "verified",
    approval: "resumed", input: "resumed", conversation: "reused", duplicate: "deduplicated"}));
