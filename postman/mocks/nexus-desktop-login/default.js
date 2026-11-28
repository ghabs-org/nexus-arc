// Owned Nexus contract, synthetic values only. Never a real identity provider.
const http = require('http');
const crypto = require('crypto');
let mode = 'success', attempt, calls = [], completeSent = false;
const token = 't'.repeat(43), code = 'c'.repeat(43);
const modes = ['success', 'wrong-state-first', 'deny', 'redirect', 'html', 'oversized', 'foreign-destination', 'exchange-error'];
http.createServer((req, res) => {
  const json = (status, body) => { res.writeHead(status, {'Content-Type':'application/json'}); res.end(JSON.stringify(body)); };
  if (req.method === 'POST' && req.url === '/fixture/mode') {
    if (!modes.includes(req.body?.mode)) return json(400, {error:'invalid_mode'});
    mode = req.body.mode; attempt = null; calls = []; completeSent = false;
    return json(200, {mode});
  }
  if (req.url === '/fixture/calls') return json(200, {calls});
  calls.push({path:req.url, method:req.method, bearer:!!req.headers.authorization});
  if (req.method === 'POST' && req.url === '/desktop/start') {
    if (mode === 'redirect') { res.writeHead(302, {Location:'/must-not-follow'}); return res.end(); }
    if (mode === 'html') { res.writeHead(200, {'Content-Type':'text/html'}); return res.end('<h1>not an account</h1>'); }
    if (mode === 'oversized') { res.writeHead(200); return res.end('x'.repeat(16385)); }
    attempt = req.body;
    const origin = mode === 'foreign-destination' ? 'https://evil.invalid' : `http://${req.headers.host}`;
    return json(200, {attempt_id:'fixture-attempt', authorization_url:origin+'/auth/desktop/authorize?attempt_id=fixture-attempt'});
  }
  if (req.method === 'POST' && req.url === '/fixture/complete' && attempt && !completeSent) {
    completeSent = true;
    json(200, {queued:true});
    const send = state => new Promise(resolve => {
      const callback = new URL(attempt.redirect_uri);
      callback.searchParams.set('state', state);
      callback.searchParams.set(mode === 'deny' ? 'error' : 'code', mode === 'deny' ? 'access_denied' : code);
      http.get(callback, reply => {
        calls.push({path:'fixture-callback', status:reply.statusCode, mode});
        reply.resume(); reply.on('end', resolve);
      }).on('error', error => { calls.push({path:'fixture-callback', error:error.code, mode}); resolve(); });
    });
    if (mode === 'wrong-state-first') send('wrong-state').then(() => send(attempt.state));
    else send(attempt.state);
    return;
  }
  if (req.method === 'POST' && req.url === '/desktop/exchange') {
    if (mode === 'exchange-error') { res.writeHead(500, {'Content-Type':'text/html'}); return res.end('<h1>unavailable</h1>'); }
    const hash = crypto.createHash('sha256').update(req.body?.code_verifier || '').digest('base64url');
    if (hash !== attempt.code_challenge || req.body?.code !== code || req.body?.redirect_uri !== attempt.redirect_uri) return json(400, {error:'pkce_failed'});
    return json(200, {token, device_id:'fixture-device', nexus_id:'fixture-user', expires_at:Date.now()/1000+3600});
  }
  if (req.url === '/desktop/session' && req.headers.authorization === `Bearer ${token}`) {
    if (req.method === 'DELETE') { res.writeHead(204); return res.end(); }
    return json(200, {device_id:'fixture-device', nexus_id:'fixture-user', account:'Synthetic fixture', projects:[], expires_at:Date.now()/1000+3600});
  }
  return json(501, {error:'unsupported_fixture_operation'});
}).listen(process.env.PORT || 49674);
