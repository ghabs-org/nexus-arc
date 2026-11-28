import {createHash, createHmac, randomUUID} from "node:crypto";

export const studioConfigProperties = {
    studioUrl: {type: "string"},
    studioEntrypoint: {type: "string"},
    studioSecretEnv: {type: "string"}
};

export type StudioContext = {
    senderId?: string; channel?: string; channelId?: string; nativeChannelId?: string;
    to?: string; threadId?: string; messageThreadId?: string | number;
    sessionId?: string; sessionKey?: string; messageId?: string;
    isAuthorizedSender?: boolean; config?: Record<string, unknown>;
};
type StudioConfig = {url: string; entrypoint: string; secret: string; timeout: number};
type Identity = {sender: string; channel: string; session: string};
type Data = Record<string, unknown>;
const mutations = new Set(["submit", "reply", "cancel"]);
const toolOperations = ["discover", "runs", "schedules", "status", "receipt", "artifacts", "artifact", "submit", "cancel"];
const object = (value: unknown): Data => value && typeof value === "object" && !Array.isArray(value) ? value as Data : {};
const text = (value: unknown): string => typeof value === "string" ? value.trim() : "";
const hash = (value: unknown): string => createHash("sha256").update(JSON.stringify(value)).digest("hex");

function config(root: Data): StudioConfig {
    const entries = object(object(root.plugins).entries);
    const value = object(object(entries["nexus-arc"]).config);
    const url = new URL(text(value.studioUrl));
    if (url.username || url.password || url.search || url.hash ||
        !(url.protocol === "https:" || url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))) {
        throw new Error("Studio URL must use HTTPS or loopback HTTP, without credentials, query or fragment.");
    }
    const entrypoint = text(value.studioEntrypoint);
    const secretEnv = text(value.studioSecretEnv);
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$/.test(entrypoint) || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(secretEnv)) {
        throw new Error("Configure studioEntrypoint and studioSecretEnv in the Nexus ARC plugin.");
    }
    const secret = process.env[secretEnv] || "";
    if (secret.length < 32) throw new Error("Studio signing secret is unavailable or shorter than 32 characters.");
    const timeout = typeof value.timeoutMs === "number" ? value.timeoutMs : 15000;
    if (!Number.isInteger(timeout) || timeout < 1000 || timeout > 120000) throw new Error("Timeout must be 1000–120000 ms.");
    return {url: url.toString().replace(/\/$/, ""), entrypoint, secret, timeout};
}

function identity(ctx: StudioContext): Identity {
    const platform = text(ctx.channel);
    const sender = text(ctx.senderId);
    const channel = text(ctx.nativeChannelId) || text(ctx.channelId) || text(ctx.to);
    if (!platform || !sender || !channel) throw new Error("Studio requires trusted channel and sender context.");
    return {
        sender: `${platform}:${sender}`, channel: `${platform}:${channel}`,
        session: hash([platform, sender, channel, ctx.threadId ?? ctx.messageThreadId ?? ""])
    };
}

export async function callStudio(
    root: Data, caller: Identity, operation: string, args: Data, requestId: string,
    signal?: AbortSignal, assertCurrent?: () => void
): Promise<Data> {
    const selected = config(root);
    if (!requestId || requestId.length > 200) throw new Error("A stable request ID of at most 200 characters is required.");
    const body = JSON.stringify({version: 1, entrypoint_id: selected.entrypoint, id: requestId,
        sender_id: caller.sender, channel: caller.channel, session_id: caller.session,
        operation, arguments: args});
    const timestamp = String(Math.floor(Date.now() / 1000));
    const signature = createHmac("sha256", selected.secret).update(timestamp + "." + body).digest("hex");
    const timeout = AbortSignal.timeout(selected.timeout);
    const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
    try {
        combined.throwIfAborted();
        assertCurrent?.();
        const response = await fetch(`${selected.url}/api/openclaw/${selected.entrypoint}`, {
            method: "POST", redirect: "error", signal: combined,
            headers: {"Content-Type": "application/json", "X-Nexus-Timestamp": timestamp,
                "X-Nexus-Signature": `sha256=${signature}`}, body
        });
        if (!response.ok) {
            // Do not surface proxy HTML or upstream messages that may contain secrets.
            await response.body?.cancel();
            throw new Error(`Studio HTTP ${response.status}. Inspect configuration or current work; request ID: ${requestId}.`);
        }
        if (!response.headers.get("content-type")?.includes("application/json")) {
            await response.body?.cancel();
            throw new Error("Studio returned an unexpected content type.");
        }
        const reader = response.body?.getReader();
        if (!reader) throw new Error("Studio returned no response body.");
        const chunks: Uint8Array[] = [];
        let size = 0;
        while (true) {
            const chunk = await reader.read();
            if (chunk.done) break;
            size += chunk.value.length;
            if (size > 2 * 1024 * 1024) {
                await reader.cancel();
                throw new Error("Studio response exceeds two MiB.");
            }
            chunks.push(chunk.value);
        }
        const result = object(JSON.parse(Buffer.concat(chunks).toString("utf8")));
        if (result.version !== 1) throw new Error("Unsupported Studio operator protocol.");
        assertCurrent?.();
        return result;
    } catch (error) {
        const detail = error instanceof Error && error.message.startsWith("Studio HTTP") ? error.message : "Studio request failed or was interrupted.";
        throw new Error(`${detail}${mutations.has(operation) ? ` Outcome may be uncertain. Inspect /nexus studio receipt ${requestId}; never retry with a new request ID.` : ""}`);
    }
}

const help = [
    "/nexus studio agents | workflows | runs | schedules",
    "/nexus studio run agent|workflow <id> <task> [--request-id <id>]",
    "/nexus studio status|artifacts|cancel <run_id> [--request-id <id>]",
    "/nexus studio receipt <request_id>",
    "/nexus studio reply <token> approve|reject [--request-id <id>]",
    "/nexus studio reply <token> input <JSON input-ID map> [--request-id <id>]"
].join("\n");

export function renderStudio(result: Data, baseUrl: string): string {
    const lines: string[] = [];
    if (result.receipt_id) lines.push(`Request: ${result.receipt_id}${result.duplicate ? " (already accepted)" : ""}`);
    const run = object(result.run);
    if (run.id) {
        lines.push(`Run: ${run.id} · ${run.status}`);
        if (run.session_id) lines.push(`Conversation: ${run.session_id}`);
        if (run.output) lines.push(String(run.output));
        if (run.studio_path) lines.push(`Studio: ${baseUrl.replace(/\/$/, "")}${run.studio_path}`);
        if (run.reply_token) {
            const approval = Array.isArray(run.awaiting_approval) && run.awaiting_approval.length ? run.awaiting_approval : run.awaiting_input;
            lines.push(`Pending gates: ${JSON.stringify(approval)}`);
            lines.push(`Reply token: ${run.reply_token}`);
            lines.push("Use /nexus studio reply with approve, reject, or input and the requested JSON values.");
        }
    }
    if (Array.isArray(result.items)) {
        for (const raw of result.items) {
            const item = object(raw);
            lines.push([item.target || item.id, item.name || item.status,
                item.revision ? `revision ${item.revision}` : ""].filter(Boolean).join(" · "));
            if (item.download_path) lines.push(baseUrl.replace(/\/$/, "") + item.download_path);
        }
        if (!result.items.length) lines.push("No matching Studio resources.");
    }
    return lines.join("\n");
}

export async function handleStudioCommand(ctx: StudioContext, input: string): Promise<{text: string}> {
    try {
        if (ctx.isAuthorizedSender !== true) throw new Error("Studio commands require an authorized sender.");
        if (!input.trim() || input.trim() === "help") return {text: help};
        let value = input.trim();
        const override = value.match(/\s+--request-id\s+([A-Za-z0-9_-]{1,160})$/);
        if (override) value = value.slice(0, override.index).trim();
        const [command, ...words] = value.split(/\s+/);
        let operation = command;
        let args: Data = {};
        if (["agents", "workflows"].includes(command)) operation = "discover";
        else if (command === "run") {
            const [kind, id, ...task] = words;
            if (!["agent", "workflow"].includes(kind) || !id || !task.length) throw new Error(help);
            operation = "submit";
            // Preserve whitespace in the task rather than reconstructing it from words.
            args = {target: `${kind}s:${id}`, task: value.replace(/^run\s+\S+\s+\S+\s+/, "")};
        } else if (["status", "artifacts", "cancel"].includes(command) && words.length === 1) args = {run_id: words[0]};
        else if (command === "receipt" && words.length === 1) args = {request_id: words[0]};
        else if (command === "reply") {
            if (words[1] === "input") args = {token: words[0], values: JSON.parse(value.replace(/^reply\s+\S+\s+input\s+/, ""))};
            else if (words.length === 2 && ["approve", "reject"].includes(words[1])) args = {token: words[0], decision: words[1]};
            else throw new Error(help);
        } else if (!["runs", "schedules"].includes(command) || words.length) throw new Error(help);
        const requestId = override?.[1] || (ctx.messageId ? "command-" + hash(ctx.messageId) : mutations.has(operation) ? "" : randomUUID());
        if (!requestId) throw new Error("This channel provides no stable message ID. Add --request-id <unique-id>; reuse it only to recover this same request.");
        const result = await callStudio(ctx.config || {}, identity(ctx), operation, args, requestId);
        if (["agents", "workflows"].includes(command) && Array.isArray(result.items)) {
            result.items = result.items.filter(item => text(object(item).target).startsWith(command + ":"));
        }
        return {text: renderStudio(result, config(ctx.config || {}).url)};
    } catch (error) {
        return {text: error instanceof Error ? error.message : "Studio command failed."};
    }
}

type ToolContext = {
    requesterSenderId?: string; nativeChannelId?: string; sessionId?: string; sessionKey?: string;
    deliveryContext?: {channel?: string; threadId?: string | number};
    assertInvocationCurrent: () => void;
};
export type StudioToolApi = {
    config?: Data;
    registerTool?: (factory: {contextVersion: 2; create: (ctx: ToolContext) => unknown},
                    options: {name: string; optional: boolean; workspaceAccess: false}) => void;
};

export function registerStudioTool(api: StudioToolApi): void {
    if (!api.registerTool) return;
    api.registerTool({contextVersion: 2, create(ctx) {
        if (!ctx.requesterSenderId || !ctx.nativeChannelId || !ctx.deliveryContext?.channel ||
            typeof ctx.assertInvocationCurrent !== "function") return null;
        const caller = identity({senderId: ctx.requesterSenderId, nativeChannelId: ctx.nativeChannelId,
            channel: ctx.deliveryContext.channel, messageThreadId: ctx.deliveryContext.threadId});
        return {
            name: "nexus_studio", label: "Nexus Studio",
            description: "Discover Studio agents/workflows, submit and track durable work, read artifacts, or cancel owned runs. For submit/cancel supply a stable request_id and inspect receipt after any failure. Never replace the ID to retry uncertain work. Approval/input replies require the user's /nexus studio reply command.",
            parameters: {type: "object", additionalProperties: false, required: ["operation"], properties: {
                operation: {type: "string", enum: toolOperations}, request_id: {type: "string", maxLength: 200},
                target: {type: "string"}, task: {type: "string"}, run_id: {type: "string"},
                artifact_id: {type: "string"}, revision: {type: "integer", minimum: 1}
            }},
            async execute(callId: string, params: Data, signal?: AbortSignal) {
                const operation = text(params.operation);
                if (!toolOperations.includes(operation)) throw new Error("This operation is not available to agent tools.");
                if (mutations.has(operation) && !text(params.request_id)) throw new Error("A stable request_id is required for mutations.");
                const args = Object.fromEntries(Object.entries(params).filter(([key]) => !["operation", "request_id"].includes(key)));
                if (operation === "receipt") args.request_id = params.request_id;
                const details = await callStudio(api.config || {}, caller, operation, args,
                    text(params.request_id) || callId, signal, ctx.assertInvocationCurrent);
                // Never expose a reply capability to the model. The human obtains it through status.
                const safe = JSON.parse(JSON.stringify(details, (key, value) => key === "reply_token" ? undefined : value));
                return {content: [{type: "text", text: JSON.stringify(safe)}], details: safe};
            }
        };
    }}, {name: "nexus_studio", optional: true, workspaceAccess: false});
}
