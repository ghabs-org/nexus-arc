// Synthetic transport peer derived from postman/contracts/studio-openclaw.md.
// The real Studio server is exercised separately by the example consumer.
const http = require('node:http');
const {createHmac} = require('node:crypto');
const secret = 'synthetic-failure-secret-' + 's'.repeat(32);
const trace = event => {
    if (process.env.NEXUS_OPENCLAW_FAULT_EVENTS === '1') console.log(JSON.stringify(event));
};
http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const raw = Buffer.isBuffer(req.rawBody)
        ? req.rawBody
        : (typeof req.body === 'string'
            ? Buffer.from(req.body)
            : (req.body ? Buffer.from(JSON.stringify(req.body)) : Buffer.concat(chunks)));
    const reply = (code, body, type = 'application/json', headers = {}) => {
        res.writeHead(code, {'Content-Type': type, ...headers}); res.end(body);
    };
    if (req.method !== 'POST' || !req.url.startsWith('/api/openclaw/')) return reply(501, '{}');
    trace({event: 'request', method: req.method, path: req.url});
    const signature = createHmac('sha256', secret).update(req.headers['x-nexus-timestamp'] + '.').update(raw).digest('hex');
    if (req.headers['x-nexus-signature'] !== 'sha256=' + signature) return reply(401, '{}');
    const mode = req.url.split('/').pop();
    switch (mode) {
        case 'healthy': return reply(200, '{"version":1,"items":[]}');
        case 'denied': return reply(403, '{"error":"PRIVATE_FAULT_DETAIL"}');
        case 'error': return reply(503, '{"error":"PRIVATE_FAULT_DETAIL"}');
        case 'html': return reply(502, '<html>PRIVATE_FAULT_DETAIL</html>', 'text/html');
        case 'malformed': return reply(200, 'PRIVATE_FAULT_DETAIL');
        case 'redirect': return reply(307, '{}', 'application/json', {Location: '/must-not-follow'});
        case 'oversized': return reply(200, JSON.stringify({version: 1, text: 'x'.repeat(2 * 1024 * 1024)}));
        case 'hang': return setTimeout(() => reply(200, '{"version":1}'), 3000);
        default: return reply(501, '{}');
    }
}).listen(process.env.PORT || 49672, '127.0.0.1', () => trace({event: 'listening'}));
