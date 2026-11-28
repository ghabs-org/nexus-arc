// Contract: https://www.twilio.com/docs/sendgrid/api-reference/mail-send/mail-send
// Local synthetic transport only. No email leaves this process.
const http = require('http');
let attempts = 0;
http.createServer((req, res) => {
  const reply = (status, value) => {
    res.writeHead(status, {'Content-Type': 'application/json'});
    res.end(JSON.stringify(value));
  };
  if (req.method === 'GET' && req.url === '/fixture/attempts') {
    return reply(200, {attempts});
  }
  if (req.method !== 'POST' || req.url !== '/v3/mail/send') return reply(501, {});
  attempts++;
  if (req.headers.authorization !== 'Bearer synthetic-sendgrid-key') {
    return reply(401, {errors: [{message: 'synthetic private account detail'}]});
  }
  const body = req.body;
  if (!body?.from?.email || !body.personalizations?.[0]?.to?.[0]?.email) {
    return reply(400, {errors: [{message: 'Missing sender or recipient'}]});
  }
  if (body.subject === 'fixture:reject') return reply(429, {errors: [{message: 'private quota detail'}]});
  if (body.subject === 'fixture:redirect') {
    res.writeHead(307, {Location: 'https://example.invalid/never-send'});
    return res.end();
  }
  if (body.template_id) {
    if (body.template_id !== 'd-fixture' || !body.personalizations[0].dynamic_template_data?.name) {
      return reply(400, {errors: [{message: 'Synthetic template requires name'}]});
    }
  } else if (!body.content?.[0]?.value || !(body.subject || body.personalizations.every(p => p.subject))) {
    return reply(400, {errors: [{message: 'Missing subject or content'}]});
  }
  res.writeHead(202, {'X-Message-Id': 'synthetic-accepted-message'});
  res.end();
}).listen(process.env.PORT || 49871);
