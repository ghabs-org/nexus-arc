import test from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {createHmac} from "node:crypto";
import plugin from "./index.ts";
import {callStudio, handleStudioCommand, registerStudioTool} from "./studio.ts";

const secret = "synthetic-openclaw-secret-" + "s".repeat(32);
process.env.OPENCLAW_TEST_SECRET = secret;
const caller = {sender: "telegram:alice", channel: "telegram:room", session: "thread-1"};
const root = (url: string) => ({plugins: {entries: {"nexus-arc": {config: {
    studioUrl: url, studioEntrypoint: "openclaw", studioSecretEnv: "OPENCLAW_TEST_SECRET", timeoutMs: 1000
}}}}});

async function fixture(fn: (url: string, requests: any[]) => Promise<void>, response?: (res: any) => void) {
    const requests: any[] = [];
    const server = createServer(async (req, res) => {
        const chunks = [];
        for await (const chunk of req) chunks.push(chunk);
        const raw = Buffer.concat(chunks);
        const expected = createHmac("sha256", secret).update(req.headers["x-nexus-timestamp"] + ".").update(raw).digest("hex");
        assert.equal(req.headers["x-nexus-signature"], "sha256=" + expected);
        requests.push({path: req.url, ...JSON.parse(raw.toString())});
        if (response) return response(res);
        res.writeHead(200, {"Content-Type": "application/json"});
        res.end(JSON.stringify({version: 1, items: [{target: "agents:reviewer", name: "Reviewer"}],
            run: {id: "run-1", status: "waiting", reply_token: "human-only"}}));
    });
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    try {await fn(`http://127.0.0.1:${(server.address() as any).port}`, requests);}
    finally {server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve()));}
}

test("Studio command routes independently of the legacy bridge with signed runtime identity", async () => {
    await fixture(async (url, requests) => {
        const commands: any[] = [];
        plugin.register({registerCommand: item => commands.push(item)});
        assert.deepEqual(commands.map(c => c.name), ["nexus", "chat", "chatagents"]);
        const result = await commands[0].handler({args: "studio agents", config: root(url),
            senderId: "alice", channel: "telegram", channelId: "room", isAuthorizedSender: true});
        assert.match(result.text, /agents:reviewer/);
        assert.equal(requests.length, 1);
        assert.equal(requests[0].path, "/api/openclaw/openclaw");
        assert.equal(requests[0].sender_id, "telegram:alice");
        assert.equal(requests[0].channel, "telegram:room");
    });
});

test("mutation requires stable identity and request ID, preserving exact task whitespace", async () => {
    await fixture(async (url, requests) => {
        const ctx = {config: root(url), senderId: "alice", channel: "telegram", channelId: "room", isAuthorizedSender: true};
        assert.match((await handleStudioCommand(ctx, "run agent reviewer Review")).text, /stable message ID/);
        assert.equal(requests.length, 0);
        await handleStudioCommand(ctx, "run agent reviewer Review  two\nlines --request-id request-1");
        await handleStudioCommand(ctx, "run agent reviewer Review  two\nlines --request-id request-1");
        assert.equal(requests[0].arguments.task, "Review  two\nlines");
        assert.deepEqual(requests[0], requests[1]);
        assert.match((await handleStudioCommand({...ctx, isAuthorizedSender: false}, "agents")).text, /authorized sender/);
        assert.equal(requests.length, 2);
    });
});

test("v2 agent tool takes identity only from trusted context and never approves", async () => {
    await fixture(async (url, requests) => {
        let factory: any, options: any, checks = 0;
        registerStudioTool({config: root(url), registerTool: (value, opts) => {factory = value; options = opts;}});
        assert.equal(options.optional, true);
        assert.equal(factory.contextVersion, 2);
        assert.equal(factory.create({}), null);
        const tool = factory.create({requesterSenderId: "alice", nativeChannelId: "room",
            deliveryContext: {channel: "telegram"}, assertInvocationCurrent: () => {checks++;}});
        await assert.rejects(tool.execute("call", {operation: "reply", token: "invented"}), /not available/);
        await assert.rejects(tool.execute("call", {operation: "submit", target: "agents:reviewer", task: "Review"}), /request_id/);
        const result = await tool.execute("call", {operation: "discover"});
        assert.equal(requests[0].sender_id, "telegram:alice");
        assert.equal(checks, 2);
        assert.equal(result.details.run.reply_token, undefined);
        assert.doesNotMatch(result.content[0].text, /human-only/);
    });
});

test("expired runtime authority and cancelled calls never reach Studio", async () => {
    await fixture(async (url, requests) => {
        await assert.rejects(callStudio(root(url), caller, "submit", {}, "same-id", undefined,
            () => {throw new Error("expired");}), /same-id/);
        await assert.rejects(callStudio(root(url), caller, "submit", {}, "same-id", AbortSignal.abort()), /same-id/);
        assert.equal(requests.length, 0);
    });
});

test("HTTP failures redact response bodies and never replay a mutation", async () => {
    await fixture(async (url, requests) => {
        await assert.rejects(callStudio(root(url), caller, "submit", {}, "keep-this-id"), error => {
            assert.match(String(error), /HTTP 503/);
            assert.match(String(error), /receipt keep-this-id/);
            assert.doesNotMatch(String(error), /PRIVATE_SECRET/);
            return true;
        });
        assert.equal(requests.length, 1);
    }, res => {res.writeHead(503, {"Content-Type": "text/html"}); res.end("PRIVATE_SECRET");});
});

test("redirects are refused without forwarding signed credentials", async () => {
    await fixture(async (url, requests) => {
        await assert.rejects(callStudio(root(url), caller, "submit", {}, "keep-this-id"), /uncertain/);
        assert.equal(requests.length, 1);
    }, res => {res.writeHead(307, {Location: "/stolen"}); res.end();});
});

test("remote plaintext endpoints and inline secrets are rejected before I/O", async () => {
    await assert.rejects(callStudio(root("http://example.test"), caller, "discover", {}, "read"), /HTTPS/);
    const value = root("http://127.0.0.1:1");
    value.plugins.entries["nexus-arc"].config.studioSecretEnv = "not-a-variable";
    await assert.rejects(callStudio(value, caller, "discover", {}, "read"), /studioSecretEnv/);
});
