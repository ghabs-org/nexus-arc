// Synthetic identity subset. No credentials, chat sends, or publishing operations.
const http = require('http');
http.createServer((req, res) => {
  const url = new URL(req.url, 'http://fixture');
  const raw = req.body;
  const form = typeof raw === 'string' ? Object.fromEntries(new URLSearchParams(raw)) : raw || {};
  const reply = (status, value) => {
    res.writeHead(status, {'Content-Type': 'application/json'});
    res.end(JSON.stringify(value));
  };
  if (['/oauth/v2/accessToken', '/2/oauth2/token', '/v26.0/oauth/access_token'].includes(url.pathname)) {
    const code = form.code || url.searchParams.get('code');
    if (code === 'denied') return reply(400, {error: 'invalid_grant'});
    if (!['linked', 'unknown', 'missing'].includes(code)) return reply(400, {error: 'invalid_request'});
    if (url.pathname === '/2/oauth2/token' && !form.code_verifier) return reply(400, {error: 'missing_verifier'});
    return reply(200, {access_token: 'synthetic-' + code, expires_in: 3600});
  }
  if (['/v2/userinfo', '/2/users/me', '/v26.0/me'].includes(url.pathname)) {
    const token = (req.headers.authorization || '').replace(/^Bearer /, '');
    if (!token.startsWith('synthetic-')) return reply(401, {error: 'missing_bearer'});
    const subject = token === 'synthetic-linked' ? 'social-123' : 'unlinked-456';
    const profile = token === 'synthetic-missing' ? {} : {id: subject, sub: subject, name: 'Synthetic user', username: 'renamed-user'};
    return reply(200, url.pathname === '/2/users/me' ? {data: profile} : profile);
  }
  return reply(501, {error: 'Unmodeled identity operation'});
}).listen(process.env.PORT || 49649);
