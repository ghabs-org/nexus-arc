// Independent MCP wire fixture. Synthetic actions never contact external accounts.
// Protocol: https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
const http = require('http');
const crypto = require('crypto');
const streams = new Map();
const calls = [];
const tools = {tavily: 'tavily_search', bright: 'scrape_as_markdown', zapier: 'add_row'};

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://fixture.invalid');
  const reply = (status, value) => {
    res.writeHead(status, {'Content-Type': 'application/json'});
    res.end(JSON.stringify(value));
  };
  if (url.pathname === '/fixture/calls') return reply(200, calls);
  const parts = url.pathname.split('/').filter(Boolean);
  const provider = parts[0];
  if (!tools[provider]) return reply(501, {error: 'Unmodeled provider'});
  const authenticated = provider === 'tavily'
    ? req.headers.authorization === 'Bearer synthetic-catalog-token'
    : provider === 'bright'
      ? url.searchParams.get('token') === 'synthetic-catalog-token'
      : parts[1] === 'synthetic-catalog-token';
  if (!authenticated) return reply(401, {error: 'Fixture account required'});

  if (req.method === 'GET' && parts.at(-1) === 'sse') {
    const session = crypto.randomUUID();
    streams.set(session, {provider, res});
    res.writeHead(200, {'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache'});
    // Relative URL preserves the simulation member prefix and this account path.
    const query = new URLSearchParams(url.searchParams);
    query.set('session_id', session);
    res.write(`event: endpoint\ndata: messages?${query}\n\n`);
    res.on('close', () => streams.delete(session));
    return;
  }
  if (req.method === 'GET') return reply(405, {});
  if (req.method === 'DELETE') return reply(200, {});
  if (req.method !== 'POST') return reply(501, {});

  const message = req.body;
  if (!message || message.jsonrpc !== '2.0') return reply(400, {error: 'JSON-RPC required'});
  if (message.method?.startsWith('notifications/')) {
    res.writeHead(202); res.end(); return;
  }
  let result;
  if (message.method === 'initialize') {
    result = {protocolVersion: message.params.protocolVersion, capabilities: {tools: {}},
      serverInfo: {name: 'independent-catalog-fixture', version: '1'}};
  } else if (message.method === 'tools/list') {
    result = {tools: [{name: tools[provider], description: 'Synthetic selected action',
      inputSchema: {type: 'object', properties: {text: {type: 'string'}}, required: ['text']}}]};
  } else if (message.method === 'tools/call') {
    if (message.params.name !== tools[provider] || typeof message.params.arguments?.text !== 'string') {
      return reply(400, {error: 'Wrong tool or arguments'});
    }
    const text = message.params.arguments.text;
    calls.push({provider, tool: message.params.name, text});
    result = {content: [{type: 'text', text: `${provider}:${text}`}], isError: text === 'fixture:reject'};
  } else return reply(501, {error: 'Unmodeled method'});

  const response = {jsonrpc: '2.0', id: message.id, result};
  if (parts.at(-1) === 'messages') {
    const session = streams.get(url.searchParams.get('session_id'));
    if (!session || session.provider !== provider) return reply(400, {error: 'Invalid session'});
    res.writeHead(202); res.end();
    session.res.write(`event: message\ndata: ${JSON.stringify(response)}\n\n`);
  } else reply(200, response);
}).listen(process.env.PORT || 49911, '127.0.0.1');
